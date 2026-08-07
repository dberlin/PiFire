import json

import pytest

from app import app as flask_app
from blueprints.api import routes
from common.datastore_accessors import (
    append_model_evidence,
    commit_model_activation,
    read_model_activation,
    read_model_evidence,
)
from common.model_evidence import (
    ActivationEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RefreshDiagnosticsEvidence,
    RollbackEvidence,
)
from controller.linear_mpc.activation import canonical_snapshot_digest
from tests.unit.mpc.test_innovation_state_space import _config, _frames
from tests.unit.mpc.test_model_activation import _fixture
from controller.linear_mpc.state_space import InnovationStateSpace


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


def test_rollback_is_atomic_truthful_and_idempotent_for_the_exact_activation(client):
    active_snapshot = {"schema": "innovation-state-space/v2", "generation": "b"}
    rollback_snapshot = {"schema": "innovation-state-space/v2", "generation": "a"}
    active_json = json.dumps(active_snapshot, sort_keys=True, separators=(",", ":"))
    rollback_json = json.dumps(rollback_snapshot, sort_keys=True, separators=(",", ":"))
    activation = ModelEvidenceRecord(
        evidence_id="activation-b",
        kind=EvidenceKind.ACTIVATION,
        session_id="session-api",
        cook_id=None,
        timestamp_ms=1_000,
        role_generation=8,
        model_digest=canonical_snapshot_digest(active_snapshot),
        provenance_digest=canonical_snapshot_digest(rollback_snapshot),
        payload=ActivationEvidence(
            decision_id="decision-b",
            active_snapshot_json=active_json,
            rollback_snapshot_json=rollback_json,
            controller_configuration_digest="d" * 64,
        ),
    )
    append_model_evidence(
        (
            ModelEvidenceRecord(
                evidence_id="confidence-b",
                kind=EvidenceKind.CONFIDENCE_DECISION,
                session_id="session-api",
                cook_id=None,
                timestamp_ms=999,
                role_generation=7,
                model_digest=activation.model_digest,
                provenance_digest=activation.provenance_digest,
                payload=ConfidenceDecisionEvidence(decision_id="decision-b", blocked=False),
            ),
        )
    )
    commit_model_activation(activation)

    first = client.post("/api/model-evidence/rollback", json={"reason": "operator rollback b"})
    duplicate = client.post("/api/model-evidence/rollback", json={"reason": "different retry text"})

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert (
        first.get_json()
        == duplicate.get_json()
        == {
            "accepted": True,
            "active_kind": "innovation-state-space",
            "decision_id": "decision-b",
            "reason": "operator rollback b",
            "role_generation": 9,
        }
    )
    rollbacks = [record for record in read_model_evidence() if isinstance(record.payload, RollbackEvidence)]
    assert len(rollbacks) == 1


def test_activate_route_reloads_live_authorities_at_commit(client, monkeypatch):
    model = InnovationStateSpace(_config(orders=(1,), delays=(1,)))
    assert model.fit(_frames(order=1)).accepted
    snapshot = model.snapshot()
    _manager, activation_request, records, _writes, _invalidations, _config_tree, rollback = _fixture(snapshot)

    class ReadyReport:
        def to_dict(self):
            return {
                "status": "ready-for-review",
                "candidate": {"digest": activation_request.candidate_digest},
                "decision_id": activation_request.decision_id,
            }

    settings = {
        "controller": {"selected": "mpc", "config": {"mpc": {"n_horizon": 24}}},
        "cycle_data": {},
        "globals": {"units": "F"},
    }
    settings_reads = 0

    def read_live_settings():
        nonlocal settings_reads
        settings_reads += 1
        if settings_reads == 2:
            records.append(
                ModelEvidenceRecord(
                    evidence_id="decision-raced",
                    kind=EvidenceKind.CONFIDENCE_DECISION,
                    session_id="session-api",
                    cook_id=None,
                    timestamp_ms=9_999,
                    role_generation=7,
                    model_digest=activation_request.candidate_digest,
                    provenance_digest=records[0].provenance_digest,
                    payload=ConfidenceDecisionEvidence(decision_id="decision-raced", blocked=False),
                )
            )
        return settings

    monkeypatch.setattr(routes, "_model_evidence_projection", lambda: (ReadyReport(), tuple(records)))
    monkeypatch.setattr(routes, "read_model_evidence", lambda: list(records))
    monkeypatch.setattr(routes, "read_model_activation", lambda: None)
    monkeypatch.setattr(routes, "read_settings", read_live_settings)
    monkeypatch.setattr(
        routes,
        "_activation_checkpoint",
        lambda: {"online_adaptation": {"challenger": snapshot, "incumbent": rollback}},
    )
    monkeypatch.setattr(routes, "_configured_activation_prospective_solve", lambda _candidate, _configuration: 0.3)

    response = client.post(
        "/api/model-evidence/activate",
        json={
            "candidate_digest": activation_request.candidate_digest,
            "decision_id": activation_request.decision_id,
        },
    )

    assert response.status_code == 409
    assert response.get_json()["detail"] == "stale-confidence-decision"
    assert read_model_activation() is None


def test_activate_route_commits_the_exact_live_authority(client, monkeypatch):
    model = InnovationStateSpace(_config(orders=(1,), delays=(1,)))
    assert model.fit(_frames(order=1)).accepted
    snapshot = model.snapshot()
    _manager, activation_request, records, _writes, _invalidations, _config_tree, rollback = _fixture(snapshot)
    append_model_evidence(tuple(records))

    class ReadyReport:
        def to_dict(self):
            return {
                "status": "ready-for-review",
                "candidate": {"digest": activation_request.candidate_digest},
                "decision_id": activation_request.decision_id,
            }

    settings = {
        "controller": {"selected": "mpc", "config": {"mpc": {"n_horizon": 24}}},
        "cycle_data": {},
        "globals": {"units": "F"},
    }
    monkeypatch.setattr(routes, "_model_evidence_projection", lambda: (ReadyReport(), tuple(records)))
    monkeypatch.setattr(routes, "read_settings", lambda: settings)
    monkeypatch.setattr(
        routes,
        "_activation_checkpoint",
        lambda: {"online_adaptation": {"challenger": snapshot, "incumbent": rollback}},
    )
    monkeypatch.setattr(routes, "_configured_activation_prospective_solve", lambda _candidate, _configuration: 0.3)

    response = client.post(
        "/api/model-evidence/activate",
        json={
            "candidate_digest": activation_request.candidate_digest,
            "decision_id": activation_request.decision_id,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["active_kind"] == "innovation-state-space"
    active = read_model_activation()
    assert active is not None
    assert active.evidence_decision_id == activation_request.decision_id
    assert active.active_snapshot_json == json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    activations = [record for record in read_model_evidence() if isinstance(record.payload, ActivationEvidence)]
    assert len(activations) == 1
