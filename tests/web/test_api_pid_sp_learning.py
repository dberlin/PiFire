"""Read-only API contracts for the durable PID-SP learning report."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from blueprints.api import routes
from common import controller_model_state
from common.controller_model_state import (
    MODEL_STATE_KEY,
    SCHEMA_VERSION,
    ControllerModelStore,
)
from common.model_evidence import EvidenceKind, ModelEvidenceRecord, PidSpFitDecisionEvidence
from common.persistence import runtime as runtime_persistence
from common.persistence.runtime import write_generic_key
from controller.fopdt_identifier import (
    MIN_ACCEPTED,
    MIN_ACCEPTED_SECONDS,
    MIN_DUTY_STD,
    MIN_TEMP_SPAN_F,
)
from controller.pid_sp_learning import (
    build_pid_sp_live_learning,
    current_pid_sp_learning_report,
)


def _checkpoint(form, revision, parameters, theta, model_digest):
    return {
        "schema_version": 2,
        "revision": revision,
        "provenance": "common-validation",
        "selected": {
            "schema_version": "pid-sp-model-selection/v1",
            "form": form,
            "parameters": parameters,
            "delay_basin": {
                "lower_s": theta,
                "upper_s": theta,
                "representative_s": theta,
                "confidence_lower_s": theta,
                "confidence_upper_s": theta,
                "confidence_method": "provided",
                "confidence_resamples": 0,
                "episode_count": 3,
                "interior": True,
                "blockers": [],
            },
            "one_step_loss": 1.0,
            "horizon_losses": [
                [3, 1.0],
                [15, 1.0],
                [45, 1.0],
                [90, 1.0],
                [180, 1.0],
            ],
            "fold_losses": [1.0, 1.0],
            "standard_error": 0.0,
            "comparison_threshold": 1.0,
            "selection_margin": 0.0,
            "episode_ids": ["episode-a", "episode-b", "episode-c"],
            "fit_corpus_digest": "1" * 64,
            "configuration_digest": "2" * 64,
            "common_row_digest": ("e862de29171cf90e8f6b527b50fa9a9f18244547d1eced92e00235e9f381db04"),
            "confirmation_observed": 20,
            "confirmation_required": 20,
            "authorized": True,
            "model_digest": model_digest,
        },
    }


_FOPDT_CHECKPOINT = _checkpoint(
    "fopdt",
    3,
    {"K": 800.0, "tau": 600.0, "theta": 40.0},
    40,
    "2e7d0ba075c86562bbecb85df21712281b4861663c1f2c086ac4c99beca51454",
)
_IPDT_CHECKPOINT = _checkpoint(
    "ipdt",
    4,
    {"K_i": 0.46, "c0": -0.033, "theta": 90.0},
    90,
    "a9d655a0ead182748cf03d9e6d33dd1039a27dfec4cf5f0fc244a387e682fe5b",
)


def _pending_checkpoint():
    return {
        "schema": "pid-sp-learning-checkpoint/v1",
        "revision": 5,
        "confirmation": {
            "schema": "pid-sp-confirmation/v1",
            "candidate_key": None,
            "observed": 0,
        },
        "identity": {
            "fit_corpus_digest": "1" * 64,
            "configuration_digest": "2" * 64,
            "incumbent_digest": _FOPDT_CHECKPOINT["selected"]["model_digest"],
        },
        "incumbent": deepcopy(_FOPDT_CHECKPOINT),
    }


def _prepared_checkpoint():
    candidate_digest = _IPDT_CHECKPOINT["selected"]["model_digest"]
    confirmation_digest = "c" * 64
    payload = PidSpFitDecisionEvidence(
        request_id="request-pid-sp-api",
        controller="pid_sp",
        origin="passive-online",
        outcome="accepted-next-cook",
        reason="confirmed:20/20",
        request_bound=True,
        fit_corpus_digest="1" * 64,
        configuration_digest="2" * 64,
        selected_form="ipdt",
        candidate_digest=candidate_digest,
        parent_incumbent_digest=_FOPDT_CHECKPOINT["selected"]["model_digest"],
        confirmation_observed=20,
        parent_incumbent_generation=3,
        candidate_generation=4,
        confirmation_candidate_digest=confirmation_digest,
        episode_ids=("episode-a", "episode-b", "episode-c"),
    )
    terminal = ModelEvidenceRecord(
        evidence_id="evidence-pid-sp-api",
        kind=EvidenceKind.PID_SP_FIT_DECISION,
        session_id="session-pid-sp-api",
        cook_id="cook-pid-sp-api",
        timestamp_ms=1_000,
        role_generation=3,
        model_digest=candidate_digest,
        provenance_digest="1" * 64,
        payload=payload,
    )
    return {
        "schema": "pid-sp-learning-prepare/v1",
        "revision": 6,
        "terminal_evidence_json": terminal.model_dump_json(),
        "proposed": {
            "checkpoint": deepcopy(_IPDT_CHECKPOINT),
            "lineage": {
                "request_id": payload.request_id,
                "candidate_digest": payload.candidate_digest,
                "confirmation_candidate_digest": payload.confirmation_candidate_digest,
                "fit_corpus_digest": payload.fit_corpus_digest,
                "configuration_digest": payload.configuration_digest,
                "parent_incumbent_digest": payload.parent_incumbent_digest,
                "parent_incumbent_generation": payload.parent_incumbent_generation,
                "candidate_generation": payload.candidate_generation,
            },
        },
        "incumbent": deepcopy(_FOPDT_CHECKPOINT),
    }


def _live_status():
    return build_pid_sp_live_learning(
        {
            "accepted": MIN_ACCEPTED,
            "accepted_seconds": MIN_ACCEPTED_SECONDS,
            "duty_std": MIN_DUTY_STD,
            "temp_span": MIN_TEMP_SPAN_F,
            "transition_seen": True,
            "duty_segments": 3,
            "raw_best_residual": 0.5,
            "raw_runner_up_residual": 1.0,
            "raw_candidates_passing": 1,
            "trusted": None,
            "distrust_count": 0,
            "distrust_ratio": 0.0,
        },
        {
            "active": False,
            "disabled": False,
            "x0": 225.0,
            "xd": 224.0,
            "z0": 225.0,
            "zd": 224.0,
            "residual_streak": 0,
            "truncated": 0,
            "model": None,
        },
        completed_episode_count=0,
        delay_profile=None,
        comparison=None,
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
        "delay_evidence": None,
        "comparison": None,
        "active_model": None,
        "checkpoint": None,
        "failure": None,
    }


def test_report_route_serializes_the_complete_live_and_checkpoint_projection(
    client,
    monkeypatch,
):
    report = current_pid_sp_learning_report(
        status={"learning": _live_status()},
        checkpoint=_IPDT_CHECKPOINT,
    )
    monkeypatch.setattr(
        routes,
        "backend_pid_sp_learning_report",
        lambda: report,
        raising=False,
    )

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 200
    assert response.get_json() == report.as_dict()
    assert response.get_json()["status"] == "evaluating"
    assert response.get_json()["confirmation"] == {
        "observed": None,
        "required": 20,
    }
    assert response.get_json()["checkpoint"] == _IPDT_CHECKPOINT
    assert response.get_json()["comparison"] is None
    assert response.get_json()["active_model"] is None
    assert response.get_json()["delay_evidence"]["status"] == ("insufficient-excitation-episodes")
    assert b"NaN" not in response.data
    assert b"Infinity" not in response.data


def test_report_route_serializes_canonical_schema_2_checkpoint(client, monkeypatch):
    report = current_pid_sp_learning_report(
        status={},
        checkpoint=_FOPDT_CHECKPOINT,
    )
    monkeypatch.setattr(routes, "backend_pid_sp_learning_report", lambda: report)

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 200
    assert response.get_json()["checkpoint"] == _FOPDT_CHECKPOINT


@pytest.mark.parametrize(
    "snapshot",
    [_pending_checkpoint(), _prepared_checkpoint()],
    ids=["pending-confirmation", "prepared-terminal"],
)
def test_persisted_transitional_checkpoint_strict_loads_and_reports_incumbent(
    client,
    monkeypatch,
    snapshot,
):
    write_generic_key(
        MODEL_STATE_KEY,
        {
            "version": SCHEMA_VERSION,
            "models": {"pid_sp": snapshot},
        },
    )
    assert ControllerModelStore().load_strict("pid_sp") == snapshot
    monkeypatch.setattr(runtime_persistence, "read_status", dict)

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 200
    assert response.get_json()["checkpoint"] == _FOPDT_CHECKPOINT


@pytest.mark.parametrize(
    "snapshot",
    [
        {**_pending_checkpoint(), "identity": {"fit_corpus_digest": "1" * 64}},
        {**_prepared_checkpoint(), "proposed": {"checkpoint": _IPDT_CHECKPOINT, "lineage": {}}},
    ],
    ids=["malformed-pending", "malformed-prepared"],
)
def test_transitional_checkpoint_strict_load_still_rejects_malformed_payload(snapshot):
    write_generic_key(
        MODEL_STATE_KEY,
        {
            "version": SCHEMA_VERSION,
            "models": {"pid_sp": snapshot},
        },
    )

    with pytest.raises(ValueError, match="malformed stored snapshot.*pid_sp"):
        ControllerModelStore().load_strict("pid_sp")


def test_unrepresentable_report_returns_the_existing_explicit_422_shape(
    client,
    monkeypatch,
):
    def fail_report():
        checkpoint = deepcopy(_FOPDT_CHECKPOINT)
        checkpoint["selected"]["parameters"]["K"] = float("nan")
        return current_pid_sp_learning_report(status={}, checkpoint=checkpoint)

    monkeypatch.setattr(
        routes,
        "backend_pid_sp_learning_report",
        fail_report,
        raising=False,
    )

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 422
    assert response.get_json()["error"] == "pid-sp-learning-report-invalid"
    assert "selected parameter K must be finite" in response.get_json()["detail"]


def test_corrupt_persisted_checkpoint_returns_an_explicit_422(client, monkeypatch):
    class CorruptCheckpointStore:
        def load_strict(self, name):
            assert name == "pid_sp"
            raise ValueError("malformed stored snapshot for 'pid_sp'")

    monkeypatch.setattr(runtime_persistence, "read_status", dict)
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


def test_corrupt_persistence_is_not_hidden_by_a_warm_shared_cache(client):
    store = ControllerModelStore()
    assert store.save(
        "pid_sp",
        {
            "form": "fopdt",
            "K": 800.0,
            "tau": 600.0,
            "theta": 40.0,
            "revision": 3,
        },
    )
    assert store.load("pid_sp")["revision"] == 3
    write_generic_key(
        MODEL_STATE_KEY,
        {
            "version": SCHEMA_VERSION,
            "models": {"pid_sp": {"revision": "interrupted-write"}},
        },
    )

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 422
    assert response.get_json()["error"] == "pid-sp-learning-report-invalid"
    assert "malformed stored snapshot" in response.get_json()["detail"]


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
    if status == "active":
        live["active_model"] = {
            "form": "ipdt",
            "model_digest": _IPDT_CHECKPOINT["selected"]["model_digest"],
        }
        live["predictor"]["active"] = True
    report = current_pid_sp_learning_report(status=live, checkpoint=None)
    monkeypatch.setattr(routes, "backend_pid_sp_learning_report", lambda: report)

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 200
    assert response.get_json()["status"] == status
    assert response.get_json()["live"] is True


def test_report_route_rejects_a_projection_outside_the_pydantic_contract(client, monkeypatch):
    report = current_pid_sp_learning_report(status={}, checkpoint=None).as_dict()
    report["schema_version"] = 2
    monkeypatch.setattr(
        routes,
        "backend_pid_sp_learning_report",
        lambda: SimpleNamespace(as_dict=lambda: report),
    )

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 422
    assert response.get_json()["error"] == "pid-sp-learning-report-invalid"


def test_report_serialization_failure_is_an_explicit_422(client, monkeypatch):
    report = SimpleNamespace(as_dict=list)
    monkeypatch.setattr(routes, "backend_pid_sp_learning_report", lambda: report)

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 422
    assert response.get_json()["error"] == "pid-sp-learning-report-invalid"
    assert "an object" in response.get_json()["detail"]


@pytest.mark.parametrize(
    ("field", "detail"),
    [
        pytest.param("parameter", "selected parameter K must be a JSON float", id="model-parameter"),
        pytest.param("provenance", "checkpoint provenance must be a nonempty string", id="provenance"),
    ],
)
def test_oversized_checkpoint_integer_is_an_explicit_422(
    client,
    monkeypatch,
    field,
    detail,
):
    checkpoint = deepcopy(_FOPDT_CHECKPOINT)
    if field == "parameter":
        checkpoint["selected"]["parameters"]["K"] = 10**10000
    else:
        checkpoint["provenance"] = 10**10000

    def fail_report():
        return current_pid_sp_learning_report(status={}, checkpoint=checkpoint)

    monkeypatch.setattr(routes, "backend_pid_sp_learning_report", fail_report)

    response = client.get("/api/pid-sp-learning/report")

    assert response.status_code == 422
    assert response.get_json()["error"] == "pid-sp-learning-report-invalid"
    assert detail in response.get_json()["detail"]


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
