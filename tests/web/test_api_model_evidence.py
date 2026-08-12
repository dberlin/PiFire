import json
from types import SimpleNamespace

import pytest
from blueprints.api import routes
from common.datastore_accessors import (
    read_status,
    write_status,
    read_settings,
    write_settings,
)
from common.persistence.model_evidence import (
    append_model_evidence,
    commit_model_activation_phase,
    read_model_activation,
    read_model_evidence,
)
from common.model_evidence import (
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RollbackEvidence,
)
from controller.model_learning.activation import (
    ActivationPhase,
    GreyControlPairDescriptor,
    OwnedGreyControlPair,
    PreparedActivationRecord,
    canonical_snapshot_digest,
)
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FitRequest,
    FitWindowIdentity,
)
from common.controller_model_state import ControllerModelStore

from controller.mpc import Controller, _DEFAULTS
from controller.mpc_snapshot import migrate_grey_learning_snapshot
from controller.runtime.model_fitting import grey_config_digest

_CANDIDATE = "c" * 64
_INCUMBENT = "a" * 64


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


def test_empty_report_route_surfaces_missing_authority_terminally(client):
    response = client.get("/api/model-evidence/report")

    assert response.status_code == 200
    payload = response.get_json()
    assert set(payload) == {
        "schema_version",
        "status",
        "mode",
        "decision_id",
        "evidence",
        "fit",
        "cook_refit",
        "window",
        "checks",
        "candidate",
        "activation",
        "active_model",
        "identities",
        "calibration",
        "latest_lifecycle",
        "failure",
        "gates",
        "blockers",
        "errors",
        "revision",
    }
    assert set(payload["fit"]) == {"status", "request_id", "window_id", "error"}
    assert set(payload["cook_refit"]) == {
        "status",
        "latest",
        "final_status",
        "authorization",
        "next_cook",
    }
    assert set(payload["candidate"]) == {
        "digest",
        "origin",
        "policy",
        "role_generation",
        "candidate_generation",
        "parameters",
        "parameter_deltas",
        "fit_quality",
        "identifiability",
        "assessment",
    }
    assert set(payload["active_model"]) == {"digest", "role_generation"}
    assert set(payload["identities"]) == {
        "active_digest",
        "active_generation",
        "candidate_digest",
        "candidate_generation",
        "rollback_digest",
        "rollback_generation",
    }
    assert set(payload["calibration"]) == {"revision", "command_high_water"}
    assert payload["schema_version"] == 2
    assert payload["status"] == "error"
    assert payload["errors"] == ["checkpoint-missing"]
    assert payload["decision_id"] is None
    assert payload["candidate"]["digest"] is None


def test_report_route_reads_current_grey_ledger_without_changing_activation_state(client):
    append_model_evidence(
        (
            _record(
                "assessment-api",
                CandidateAssessmentEvidence(
                    decision_id="decision-api-3",
                    origin="operator-calibration",
                    policy="operator-reviewed",
                    fit_accepted=True,
                    identifiability_accepted=False,
                    native_build="passed",
                    native_dry_solve="passed",
                    target_timing="passed",
                    confidence_accepted=False,
                    rejection_reasons=("identifiability",),
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
    assert payload["evidence"]["count"] == 1
    assert payload["candidate"]["assessment"]["rejection_reasons"] == ["identifiability"]
    assert read_model_activation() == before


def test_artifact_route_contains_the_identical_report_projection_and_revision(client):
    append_model_evidence(
        (
            _record(
                "assessment-api",
                CandidateAssessmentEvidence(
                    decision_id="decision-api-3",
                    origin="operator-calibration",
                    policy="operator-reviewed",
                    fit_accepted=True,
                    identifiability_accepted=False,
                    native_build="passed",
                    native_dry_solve="passed",
                    target_timing="passed",
                    confidence_accepted=False,
                    rejection_reasons=("identifiability",),
                ),
                20,
            ),
        )
    )

    report_response = client.get("/api/model-evidence/report")
    response = client.get("/api/model-evidence/artifact")
    decoded = json.loads(response.data)

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.data == json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert decoded["artifact_schema"] == "pifire-grey-learning-report/v2"
    assert decoded["report"] == report_response.get_json()
    assert decoded["revision"] == decoded["report"]["revision"]
    assert [record["evidence_id"] for record in decoded["records"]] == ["assessment-api"]


def test_artifact_generation_failure_is_read_only_and_returns_an_explicit_error(client, monkeypatch):
    def fail_projection():
        raise ValueError("corrupt authority")

    monkeypatch.setattr(routes, "backend_learning_report", fail_projection)
    before = read_model_activation()

    response = client.get("/api/model-evidence/artifact")

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "model-evidence-artifact-invalid",
        "detail": "corrupt authority",
    }
    assert read_model_activation() == before


def test_report_route_exposes_the_accepted_calibration_command_high_water(client, monkeypatch):
    from common import datastore_accessors

    monkeypatch.setattr(datastore_accessors, "mpc_calibration_command_revision", lambda: 7)

    response = client.get("/api/model-evidence/report")

    assert response.status_code == 200
    assert response.get_json()["calibration"]["revision"] == 7


def test_report_route_rejects_a_producer_projection_outside_the_pydantic_contract(client, monkeypatch):
    report, records = routes._model_evidence_projection()
    malformed = report.as_dict()
    malformed["schema_version"] = 99
    monkeypatch.setattr(
        routes,
        "_model_evidence_projection",
        lambda: (SimpleNamespace(as_dict=lambda: malformed), records),
    )

    response = client.get("/api/model-evidence/report")

    assert response.status_code == 422
    assert response.get_json()["error"] == "model-evidence-report-invalid"


def test_calibration_route_uses_the_existing_error_envelope_for_pydantic_request_failures(client):
    response = client.post("/api/set_mpc_calibration", json={"action": "start"})

    assert response.status_code == 422
    assert response.get_json()["result"] == "ERROR"
    assert response.get_json()["data"] == {}


class _ReadyReport:
    def __init__(
        self,
        candidate_digest: str,
        decision_id: str,
        policy: str = ActivationPolicy.OPERATOR_REVIEWED.value,
    ) -> None:
        self._candidate_digest = candidate_digest
        self._decision_id = decision_id
        self._policy = policy

    def to_dict(self):
        return {
            "status": "ready-for-review",
            "candidate": {
                "digest": self._candidate_digest,
                "policy": self._policy,
            },
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


def test_real_backend_requires_checkpoint_even_when_activation_exists(client):
    _incumbent, _candidate, prepared, confidence = _api_activation()
    append_model_evidence((confidence,))
    commit_model_activation_phase(prepared, expected_phase=None)

    response = client.get("/api/model-evidence/report")

    assert response.status_code == 200
    assert response.get_json()["status"] == "error"
    assert response.get_json()["errors"] == ["checkpoint-missing"]


def test_manual_policy_is_rejected_from_real_backend_candidate_assessment(client):
    incumbent, candidate, prepared, _confidence = _api_activation()
    checkpoint = migrate_grey_learning_snapshot(
        {
            "version": 3,
            "revision": 1,
            "params": {
                "C_c": 2520.0,
                "h_amb": 18.5,
                "T_amb": 21.0,
                "theta": 47.0,
                "n_delay": 8,
                "K_Q": 910.0,
                "sigma": 0.0,
            },
            "rmse": None,
            "samples": 0,
            "band_c": [0.0, 0.0],
            "nfev": None,
        }
    )
    checkpoint["challenger"] = {
        "parameters": checkpoint["active"]["parameters"],
        "metadata": checkpoint["active"]["metadata"],
    }
    checkpoint["origin"] = "passive-online"
    checkpoint["policy"] = "passive-auto"
    checkpoint["identities"] = {
        "active_digest": incumbent.model_digest,
        "active_generation": incumbent.role_generation,
        "candidate_digest": candidate.model_digest,
        "candidate_generation": candidate.candidate_generation,
        "rollback_digest": None,
        "rollback_generation": None,
    }
    assert ControllerModelStore().save("mpc", checkpoint) is True
    append_model_evidence(
        (
            ModelEvidenceRecord(
                evidence_id="assessment-real-backend",
                kind=EvidenceKind.CANDIDATE_ASSESSMENT,
                session_id="session-api",
                cook_id=None,
                timestamp_ms=1_001,
                role_generation=incumbent.role_generation,
                model_digest=candidate.model_digest,
                provenance_digest=incumbent.model_digest,
                payload=CandidateAssessmentEvidence(
                    decision_id=prepared.decision_id,
                    origin="passive-online",
                    policy="passive-auto",
                    fit_accepted=True,
                    identifiability_accepted=True,
                    native_build="passed",
                    native_dry_solve="passed",
                    target_timing="passed",
                    confidence_accepted=True,
                ),
            ),
        )
    )
    write_status(
        {
            **read_status(),
            "learning": {
                "status": "ready-for-review",
                "fit_status": "succeeded",
                "role_generation": incumbent.role_generation,
                "candidate_generation": candidate.candidate_generation,
                "checkpoint_digest": incumbent.model_digest,
                "candidate_digest": candidate.model_digest,
                "origin": "passive-online",
                "checks": {},
                "activation_phase": "aborted",
                "pending_persistence": False,
                "pending_swap": False,
                "failure": None,
            },
        }
    )

    response = client.post(
        "/api/model-evidence/activate",
        json={
            "candidate_digest": candidate.model_digest,
            "decision_id": prepared.decision_id,
        },
    )

    assert response.status_code == 409
    assert response.get_json()["detail"] == "manual activation requires operator-reviewed policy"


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


def test_operator_evaluation_persists_restart_checkpoint_consumed_by_unmocked_activation_route(
    client,
):
    settings = read_settings()
    settings["controller"]["selected"] = "mpc"
    settings["controller"]["config"]["mpc"] = dict(_DEFAULTS)
    write_settings(settings)
    controller = Controller(
        dict(_DEFAULTS),
        settings["globals"]["units"],
        settings["cycle_data"],
    )
    controller.bind_learning_identity("session-api-operator", None, 0)
    incumbent = controller.active_control_pair.descriptor
    candidate_settings = dict(_DEFAULTS)
    candidate_settings["theta"] = float(candidate_settings["theta"]) + 1.0
    candidate_controller = Controller(
        candidate_settings,
        settings["globals"]["units"],
        settings["cycle_data"],
    )
    candidate_config = candidate_controller.mpc.config
    configuration = dict(candidate_controller.active_control_pair.descriptor.configuration)
    candidate = GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(configuration),
        configuration=configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=1,
        role_generation=1,
    )
    candidate_pair = OwnedGreyControlPair(candidate, _ApiHandle(), _ApiHandle())
    candidate_digest = candidate.model_digest
    request = FitRequest(
        request_id="request-api-operator",
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        candidate_generation=1,
        window=FitWindowIdentity(
            session_id="session-api-operator",
            cook_id=None,
            first_observation_sequence=4,
            last_observation_sequence=10,
            configuration_digest="c" * 64,
            incumbent_digest=incumbent.model_digest,
            role_generation=0,
        ),
    )
    preparation = SimpleNamespace(
        accepted=True,
        candidate_digest=candidate_digest,
        candidate_pair=candidate_pair,
        candidate=SimpleNamespace(
            request=request,
            config=candidate_config,
            rmse_c=1.0,
            sample_count=12,
            temperature_band_c=(80.0, 120.0),
            nfev=4,
        ),
        blockers=(),
        dry_solve_finite=True,
        timing=SimpleNamespace(accepted=True),
    )
    evaluation = SimpleNamespace(
        decision_id="decision-api-operator",
        accepted=True,
        blockers=(),
        role_generation=0,
        candidate_generation=1,
        incumbent_digest=incumbent.model_digest,
        challenger_digest=candidate_digest,
        completed_origins=(),
    )

    class _Learning:
        prepared = preparation
        handoff = None
        _pending_request = None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return evaluation

    controller._learning = _Learning()
    controller._grey_evaluation_payload = lambda *_args, **_kwargs: SimpleNamespace()
    controller._poll_learning_off_path_locked(
        live_origin=CandidateOrigin.OPERATOR_CALIBRATION,
    )
    controller._activation_persistence_worker.flush_and_stop(timeout=2.0)
    checkpoint = ControllerModelStore().load("mpc")
    assert checkpoint is not None
    assert checkpoint["revision"] == 1
    assert checkpoint["active_pair"] == incumbent.to_dict()
    assert migrate_grey_learning_snapshot(checkpoint)["revision"] == 1
    restart_report = client.get("/api/model-evidence/report").get_json()
    assert restart_report["status"] == "ready-for-review", restart_report
    assert checkpoint["candidate_pair"] == candidate.to_dict()
    rebuilt_controller = Controller(
        {
            **settings["controller"]["config"]["mpc"],
            **candidate.configuration,
        },
        settings["globals"]["units"],
        settings["cycle_data"],
    )
    assert (
        rebuilt_controller.active_control_pair.descriptor.configuration
        == candidate.configuration
    )
    rebuilt = routes._build_manual_candidate_pair(candidate)
    assert rebuilt.descriptor == candidate
    assert routes._manual_candidate_dry_solve(rebuilt) is True
    rebuilt.close()

    response = client.post(
        "/api/model-evidence/activate",
        json={
            "candidate_digest": candidate_digest,
            "decision_id": evaluation.decision_id,
        },
    )

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["phase"] == "prepared"
    assert read_model_activation().candidate_pair == candidate


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


def test_activate_route_treats_typed_contract_failures_as_unprocessable(client):
    response = client.post(
        "/api/model-evidence/activate",
        json={"candidate_digest": "not-a-digest", "decision_id": "decision-1"},
    )

    assert response.status_code == 422
    assert response.get_json()["error"] == "model-activation-rejected"


def test_activate_route_rejects_non_operator_reviewed_candidate_policy(client, monkeypatch):
    incumbent, candidate, prepared, _confidence = _api_activation()
    _patch_manual_candidate(monkeypatch, incumbent, candidate, prepared)
    monkeypatch.setattr(
        routes,
        "_model_evidence_projection",
        lambda: (
            _ReadyReport(
                candidate.model_digest,
                prepared.decision_id,
                ActivationPolicy.PASSIVE_AUTO.value,
            ),
            (),
        ),
    )

    response = client.post(
        "/api/model-evidence/activate",
        json={
            "candidate_digest": candidate.model_digest,
            "decision_id": prepared.decision_id,
        },
    )

    assert response.status_code == 409
    assert response.get_json()["detail"] == "manual activation requires operator-reviewed policy"
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
