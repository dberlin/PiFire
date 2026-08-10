"""Read-only API contracts for the durable PID-SP learning report."""

import pytest
from common import controller_model_state
from common import datastore_accessors

from app import app as flask_app
from blueprints.api import routes
from controller.fopdt_identifier import (
    MIN_ACCEPTED,
    MIN_ACCEPTED_SECONDS,
    MIN_DUTY_STD,
    MIN_TEMP_SPAN_F,
)
from controller.pid_sp_learning import (
    PidSpLearningReport,
    build_pid_sp_live_learning,
    current_pid_sp_learning_report,
)


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client


def _live_status():
    return build_pid_sp_live_learning(
        {
            "accepted": MIN_ACCEPTED,
            "accepted_seconds": MIN_ACCEPTED_SECONDS,
            "duty_std": MIN_DUTY_STD,
            "temp_span": MIN_TEMP_SPAN_F,
            "transition_seen": True,
            "duty_segments": 3,
            "best_residual": 0.5,
            "runner_up_residual": 1.0,
            "candidates_passing": 1,
            "confirming": 2,
            "trusted": None,
            "distrust_count": 0,
            "distrust_ratio": 0.0,
        },
        {
            "active": False,
            "disabled": False,
            "x0": 225.0,
            "xd": 224.0,
            "residual_streak": 0,
            "truncated": 0,
            "model": None,
        },
    )


def test_empty_report_route_returns_the_exact_idle_schema(client, monkeypatch):
    report = current_pid_sp_learning_report(status={}, checkpoint=None)
    monkeypatch.setattr(routes, "backend_pid_sp_learning_report", lambda: report, raising=False)

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 200
    assert response.get_json() == {
        "schema_version": 1,
        "controller": "pid_sp",
        "status": "idle",
        "live": False,
        "revision": report.revision,
        "gates": [],
        "identifier": None,
        "predictor": None,
        "confirmation": None,
        "checkpoint": None,
        "failure": None,
    }


def test_report_route_serializes_the_complete_live_and_checkpoint_projection(client, monkeypatch):
    report = current_pid_sp_learning_report(
        status={"learning": _live_status()},
        checkpoint={
            "form": "ipdt",
            "K_i": 0.8,
            "c0": -0.2,
            "theta": 20.0,
            "revision": 4,
            "identified_at_f": 230.0,
        },
    )
    monkeypatch.setattr(routes, "backend_pid_sp_learning_report", lambda: report, raising=False)

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 200
    assert response.get_json() == report.as_dict()
    assert response.get_json()["status"] == "evaluating"
    assert response.get_json()["confirmation"] == {
        "observed": 2,
        "required": 20,
    }
    assert response.get_json()["checkpoint"] == {
        "form": "ipdt",
        "K_i": 0.8,
        "c0": -0.2,
        "theta": 20.0,
        "revision": 4,
        "identified_at_f": 230.0,
    }
    assert b"NaN" not in response.data
    assert b"Infinity" not in response.data


def test_unrepresentable_report_returns_the_existing_explicit_422_shape(client, monkeypatch):
    def fail_report():
        return current_pid_sp_learning_report(
            status={},
            checkpoint={
                "form": "fopdt",
                "K": float("nan"),
                "tau": 600.0,
                "theta": 40.0,
                "revision": 3,
            },
        )

    monkeypatch.setattr(routes, "backend_pid_sp_learning_report", fail_report, raising=False)

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "pid-sp-learning-report-invalid",
        "detail": "checkpoint.K must be finite",
    }


def test_corrupt_persisted_checkpoint_returns_an_explicit_422(client, monkeypatch):
    class CorruptCheckpointStore:
        def load_strict(self, name):
            assert name == "pid_sp"
            raise ValueError("malformed stored snapshot for 'pid_sp'")

    monkeypatch.setattr(datastore_accessors, "read_status", lambda: {})
    monkeypatch.setattr(
        controller_model_state,
        "ControllerModelStore",
        CorruptCheckpointStore,
    )

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "pid-sp-learning-report-invalid",
        "detail": "malformed stored snapshot for 'pid_sp'",
    }


def test_pid_sp_learning_api_exposes_no_mutation_endpoint(client):
    body = {"action": "start"}

    assert client.post("/api/pid-sp-learning/report", json=body).status_code == 404
    assert client.post("/api/pid-sp-learning/action", json=body).status_code == 404


@pytest.mark.parametrize(
    "status",
    [
        "collecting",
        "insufficient-excitation",
        "evaluating",
        "active",
        "fallback",
    ],
)
def test_report_route_preserves_every_published_live_status(client, monkeypatch, status):
    live = _live_status()
    live["status"] = status
    report = current_pid_sp_learning_report(status=live, checkpoint=None)
    monkeypatch.setattr(routes, "backend_pid_sp_learning_report", lambda: report)

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 200
    assert response.get_json()["status"] == status
    assert response.get_json()["live"] is True


def test_report_serialization_failure_is_an_explicit_422(client, monkeypatch):
    report = PidSpLearningReport(b"[]")
    monkeypatch.setattr(routes, "backend_pid_sp_learning_report", lambda: report)

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "pid-sp-learning-report-invalid",
        "detail": "PID-SP learning report root is not an object",
    }


@pytest.mark.parametrize(
    ("field", "detail"),
    [
        pytest.param("K", "checkpoint K must be a number", id="model-parameter"),
        pytest.param(
            "identified_at_f",
            "checkpoint identified_at_f must be a number",
            id="provenance",
        ),
    ],
)
def test_oversized_checkpoint_integer_is_an_explicit_422(client, monkeypatch, field, detail):
    checkpoint = {
        "form": "fopdt",
        "K": 800.0,
        "tau": 600.0,
        "theta": 40.0,
        "revision": 3,
        field: 10**10000,
    }

    def fail_report():
        return current_pid_sp_learning_report(status={}, checkpoint=checkpoint)

    monkeypatch.setattr(routes, "backend_pid_sp_learning_report", fail_report)

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "pid-sp-learning-report-invalid",
        "detail": detail,
    }


def test_confirmation_progress_is_visible_and_changes_the_api_revision(client, monkeypatch):
    live = _live_status()
    first = current_pid_sp_learning_report(status=live, checkpoint=None)
    live["confirmation"]["observed"] = 3
    changed = current_pid_sp_learning_report(status=live, checkpoint=None)
    monkeypatch.setattr(routes, "backend_pid_sp_learning_report", lambda: changed)

    response = client.get("/api/pid-sp-learning/report")

    assert first.revision != changed.revision
    assert response.status_code == 200
    assert response.get_json()["confirmation"] == {
        "observed": 3,
        "required": 20,
    }
    assert response.get_json()["revision"] == changed.revision
