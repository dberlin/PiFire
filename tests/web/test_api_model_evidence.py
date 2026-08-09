import json

import pytest

from app import app as flask_app
from blueprints.api import routes
from common.datastore_accessors import (
    append_model_evidence,
    commit_model_activation_phase,
    read_model_activation,
    read_model_evidence,
)
from common.model_evidence import (
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RefreshDiagnosticsEvidence,
    RollbackEvidence,
)
from controller.model_learning.activation import (
    ActivationPhase,
    GreyControlPairDescriptor,
    OwnedGreyControlPair,
    PreparedActivationRecord,
    canonical_snapshot_digest,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin


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


class _ReadyReport:
    def __init__(self, candidate_digest: str, decision_id: str) -> None:
        self._candidate_digest = candidate_digest
        self._decision_id = decision_id

    def to_dict(self):
        return {
            "status": "ready-for-review",
            "candidate": {"digest": self._candidate_digest},
            "decision_id": self._decision_id,
        }


class _ApiHandle:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def _api_descriptor(config, candidate_generation, role_generation):
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(config),
        configuration=config,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=candidate_generation,
        role_generation=role_generation,
    )


def _api_activation():
    incumbent = _api_descriptor(
        {"schema": "pifire-grey-box-model/v4", "theta": 50.0},
        3,
        4,
    )
    candidate = _api_descriptor(
        {"schema": "pifire-grey-box-model/v4", "theta": 40.0},
        4,
        5,
    )
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent,
        candidate=candidate,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.OPERATOR_REVIEWED,
        decision_id="decision-api-grey",
    )
    confidence = ModelEvidenceRecord(
        evidence_id="confidence-api-grey",
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id="session-api",
        cook_id=None,
        timestamp_ms=999,
        role_generation=incumbent.role_generation,
        model_digest=candidate.model_digest,
        provenance_digest=incumbent.model_digest,
        payload=ConfidenceDecisionEvidence(
            decision_id=prepared.decision_id,
            blocked=False,
        ),
    )
    return incumbent, candidate, prepared, confidence


def _patch_manual_candidate(monkeypatch, incumbent, candidate, prepared):
    estimator = _ApiHandle()
    solver = _ApiHandle()
    pair = OwnedGreyControlPair(candidate, estimator, solver)
    monkeypatch.setattr(
        routes,
        "_model_evidence_projection",
        lambda: (_ReadyReport(candidate.model_digest, prepared.decision_id), ()),
    )
    monkeypatch.setattr(
        routes,
        "_activation_checkpoint",
        lambda: {
            "active_pair": incumbent.to_dict(),
            "candidate_pair": candidate.to_dict(),
        },
    )
    monkeypatch.setattr(routes, "_build_manual_candidate_pair", lambda _descriptor: pair)
    monkeypatch.setattr(routes, "_manual_candidate_dry_solve", lambda value: value is pair)
    return pair, estimator, solver


def test_activate_route_persists_only_prepared_after_exact_manual_validation(client, monkeypatch):
    incumbent, candidate, prepared, confidence = _api_activation()
    append_model_evidence((confidence,))
    _pair, estimator, solver = _patch_manual_candidate(
        monkeypatch, incumbent, candidate, prepared
    )

    response = client.post(
        "/api/model-evidence/activate",
        json={
            "candidate_digest": candidate.model_digest,
            "decision_id": prepared.decision_id,
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "accepted": True,
        "phase": "prepared",
        "transaction_id": prepared.transaction_id,
        "decision_id": prepared.decision_id,
        "candidate_digest": candidate.model_digest,
        "role_generation": candidate.role_generation,
    }
    state = read_model_activation()
    assert state is not None
    assert state.phase == "prepared"
    assert state.active_pair == incumbent
    assert state.candidate_pair == candidate
    assert estimator.closed == solver.closed == 1


def test_activate_route_rechecks_latest_confidence_inside_prepared_transaction(client, monkeypatch):
    incumbent, candidate, prepared, confidence = _api_activation()
    append_model_evidence((confidence,))
    _patch_manual_candidate(monkeypatch, incumbent, candidate, prepared)
    original_checkpoint = routes._activation_checkpoint

    def race_confidence():
        checkpoint = original_checkpoint()
        append_model_evidence(
            (
                confidence.model_copy(
                    update={
                        "evidence_id": "confidence-api-raced",
                        "timestamp_ms": 1_001,
                        "payload": ConfidenceDecisionEvidence(
                            decision_id=prepared.decision_id,
                            blocked=True,
                            reason="confidence-regressed",
                        ),
                    }
                ),
            )
        )
        return checkpoint

    monkeypatch.setattr(routes, "_activation_checkpoint", race_confidence)

    response = client.post(
        "/api/model-evidence/activate",
        json={
            "candidate_digest": candidate.model_digest,
            "decision_id": prepared.decision_id,
        },
    )

    assert response.status_code == 409
    assert response.get_json()["detail"] == "activation-authority-changed"
    assert read_model_activation() is None


def test_activate_route_requires_exact_body_digest_decision_and_operator_policy(client, monkeypatch):
    incumbent, candidate, prepared, confidence = _api_activation()
    append_model_evidence((confidence,))
    _patch_manual_candidate(monkeypatch, incumbent, candidate, prepared)

    malformed = client.post("/api/model-evidence/activate", json={"decision_id": prepared.decision_id})
    stale = client.post(
        "/api/model-evidence/activate",
        json={"candidate_digest": candidate.model_digest, "decision_id": "stale"},
    )

    assert malformed.status_code == 422
    assert stale.status_code == 409
    assert read_model_activation() is None


def test_rollback_is_atomic_idempotent_and_names_exact_recorded_owner(client):
    incumbent, candidate, prepared, confidence = _api_activation()
    append_model_evidence((confidence,))
    commit_model_activation_phase(prepared, expected_phase=None)
    commit_model_activation_phase(
        prepared.transition(ActivationPhase.ACTIVE),
        expected_phase=ActivationPhase.PREPARED,
    )

    first = client.post("/api/model-evidence/rollback", json={"reason": "operator rollback grey"})
    duplicate = client.post("/api/model-evidence/rollback", json={"reason": "different retry text"})

    assert first.status_code == duplicate.status_code == 200
    assert first.get_json() == duplicate.get_json() == {
        "accepted": True,
        "active_kind": "grey-box",
        "decision_id": prepared.decision_id,
        "reason": "operator rollback grey",
        "role_generation": candidate.role_generation + 1,
        "rollback_digest": incumbent.model_digest,
    }
    rollbacks = [record for record in read_model_evidence() if isinstance(record.payload, RollbackEvidence)]
    assert len(rollbacks) == 1
    assert rollbacks[0].model_digest == candidate.model_digest
