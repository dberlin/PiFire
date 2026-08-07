import json

import pytest

from app import app as flask_app
from blueprints.api import routes
from common.datastore_accessors import append_model_evidence, read_model_activation
from common.model_evidence import (
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RefreshDiagnosticsEvidence,
)


_CANDIDATE = "c" * 64
_INCUMBENT = "a" * 64


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client


def _record(evidence_id, payload, timestamp_ms):
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind(payload.payload_type),
        session_id="session-api",
        cook_id="cook-api",
        timestamp_ms=timestamp_ms,
        role_generation=3,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
        payload=payload,
    )


def test_empty_report_route_returns_collecting_with_exact_missing_gates(client):
    response = client.get("/api/model-evidence/report")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["schema_version"] == 1
    assert payload["status"] == "collecting"
    assert payload["decision_id"] is None
    assert payload["candidate"]["digest"] is None
    assert payload["missing_gates"] == [gate["name"] for gate in payload["gates"] if not gate["passed"]]
    assert payload["blockers"] == [gate["reason"] for gate in payload["gates"] if gate["reason"] is not None]


def test_report_route_reads_the_compact_ledger_without_changing_activation_state(client):
    append_model_evidence(
        (
            _record(
                "refresh-api",
                RefreshDiagnosticsEvidence(
                    accepted=False,
                    reason="rank-deficient",
                    full_rank=False,
                    finite_diagnostics=True,
                    covariance_finite=True,
                ),
                10,
            ),
            _record(
                "decision-api",
                ConfidenceDecisionEvidence(
                    decision_id="decision-api-3",
                    blocked=True,
                    reason="identifiability",
                ),
                20,
            ),
        )
    )
    before = read_model_activation()

    response = client.get("/api/model-evidence/report")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["decision_id"] == "decision-api-3"
    assert payload["candidate"] == {
        "kind": "innovation-state-space",
        "generation": 3,
        "digest": _CANDIDATE,
    }
    assert payload["identifiability"]["reason"] == "rank-deficient"
    assert read_model_activation() == before


def test_artifact_route_returns_the_canonical_report_and_only_referenced_records(client):
    append_model_evidence(
        (
            _record(
                "refresh-api",
                RefreshDiagnosticsEvidence(accepted=False, reason="rank-deficient"),
                10,
            ),
            _record(
                "decision-api",
                ConfidenceDecisionEvidence(
                    decision_id="decision-api-3",
                    blocked=True,
                    reason="identifiability",
                ),
                20,
            ),
        )
    )

    response = client.get("/api/model-evidence/artifact")
    decoded = json.loads(response.data)

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.data == json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    assert decoded["artifact_schema"] == "pifire-model-evidence/v1"
    assert decoded["report"]["decision_id"] == "decision-api-3"
    assert [record["evidence_id"] for record in decoded["records"]] == decoded["report"]["artifact_metadata"][
        "evidence_ids"
    ]


def test_artifact_generation_failure_is_read_only_and_returns_an_explicit_error(client, monkeypatch):
    valid = _record(
        "invalid-api",
        RefreshDiagnosticsEvidence(accepted=False, reason="rank-deficient"),
        10,
    )
    invalid = valid.model_copy(update={"kind": EvidenceKind.FALLBACK})
    monkeypatch.setattr(routes, "read_model_evidence", lambda: [invalid])
    before = read_model_activation()

    response = client.get("/api/model-evidence/artifact")

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "model-evidence-artifact-invalid",
        "detail": "invalid ledger record 'invalid-api'",
    }
    assert read_model_activation() == before


def test_report_route_exposes_the_accepted_calibration_command_high_water(client, monkeypatch):
    monkeypatch.setattr(routes, "mpc_calibration_command_revision", lambda: 7)

    response = client.get("/api/model-evidence/report")

    assert response.status_code == 200
    assert response.get_json()["calibration"]["revision"] == 7
