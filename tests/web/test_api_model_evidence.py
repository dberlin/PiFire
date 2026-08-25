import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from blueprints.api import routes
from common.controller_model_state import ControllerModelStore
from common.model_evidence import (
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RollbackEvidence,
)
from common.persistence.model_evidence import (
    append_model_evidence,
    commit_model_activation_phase,
    read_model_activation,
    read_model_evidence,
)
from common.persistence.runtime import (
    read_settings,
    read_status,
    write_settings,
    write_status,
)
from controller.model_learning.activation import (
    ActivationPhase,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
    canonical_snapshot_digest,
)
from controller.model_learning.activation_service import (
    ActivationAccepted,
    ActivationRejected,
    ActivationRejectionCategory,
    RollbackAccepted,
    RollbackRejected,
    RollbackRejectionCategory,
)
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FitRequest,
    FitWindowIdentity,
)
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_core import MpcCore
from controller.mpc_snapshot import migrate_grey_learning_snapshot
from controller.runtime.model_fitting import (
    CandidatePair,
    CandidatePreparation,
    GreyFitSuccess,
    TargetTimingEvidence,
)

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
    from common.persistence import control as control_persistence

    monkeypatch.setattr(control_persistence, "mpc_calibration_command_revision", lambda: 7)

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


def _api_descriptor(theta, candidate_generation, role_generation, *, legacy=False):
    settings = dict(DEFAULT_MPC_CONFIG, theta=theta)
    native = MpcCore.native_configuration(settings)
    native_configuration = {name: getattr(native, name) for name in native.__dataclass_fields__}
    configuration = dict(native_configuration)
    if not legacy:
        configuration.update(
            {
                "control_period": DEFAULT_MPC_CONFIG["control_period"],
                "est_q_temp": DEFAULT_MPC_CONFIG["est_q_temp"],
                "est_q_dist": DEFAULT_MPC_CONFIG["est_q_dist"],
                "est_r_meas": DEFAULT_MPC_CONFIG["est_r_meas"],
            }
        )
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(native_configuration),
        configuration=configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=candidate_generation,
        role_generation=role_generation,
    )


def _api_activation(*, legacy=False):
    incumbent = _api_descriptor(50.0, 3, 4, legacy=legacy)
    candidate = _api_descriptor(40.0, 4, 5, legacy=legacy)
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


def test_operator_evaluation_persists_restart_checkpoint_consumed_by_unmocked_activation_route(
    client,
):
    settings = read_settings()
    settings["controller"]["selected"] = "mpc"
    settings["controller"]["config"]["mpc"] = dict(DEFAULT_MPC_CONFIG)
    write_settings(settings)
    controller = Controller(
        dict(DEFAULT_MPC_CONFIG),
        settings["globals"]["units"],
        settings["cycle_data"],
    )
    controller.bind_learning_identity("session-api-operator", None, 0)
    incumbent = controller.active_control_pair.descriptor
    candidate_settings = dict(DEFAULT_MPC_CONFIG)
    candidate_settings["theta"] = float(candidate_settings["theta"]) + 1.0
    candidate_controller = Controller(
        candidate_settings,
        settings["globals"]["units"],
        settings["cycle_data"],
    )
    candidate_config = candidate_controller.mpc.config
    candidate = replace(
        candidate_controller.active_control_pair.descriptor,
        candidate_generation=1,
        role_generation=1,
        ownership_digest="",
    )
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
    fit = GreyFitSuccess(
        request=request,
        config=candidate_config,
        rmse_c=1.0,
        max_error_c=1.5,
        identifiability=0.9,
        sample_count=12,
        temperature_band_c=(80.0, 120.0),
        nfev=4,
    )
    preparation = CandidatePreparation.accepted_for_test(
        candidate=fit,
        candidate_pair=CandidatePair(object(), object()),
        incumbent_pair=CandidatePair(object(), object()),
        timing=TargetTimingEvidence("candidate-dry-solve", 3, 1.0, 25.0),
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
        pending_request = None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return evaluation

        def close(self):
            return None

    controller._grey_learning_runtime._learning = _Learning()
    controller._grey_learning_runtime._grey_evaluation_payload = lambda *_args, **_kwargs: SimpleNamespace()
    controller.poll_learning_off_path(
        live_origin=CandidateOrigin.OPERATOR_CALIBRATION,
    )
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
    assert rebuilt_controller.active_control_pair.descriptor.configuration == candidate.configuration

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
    controller.close()
    candidate_controller.close()
    rebuilt_controller.close()


def test_activate_route_requires_exact_body_and_preserves_stale_rejection(
    client,
    monkeypatch,
):
    calls = []

    class _Service:
        def activate(self, request, *, now_ms):
            calls.append((request, now_ms))
            return ActivationRejected(
                ActivationRejectionCategory.CONFLICT,
                "stale-confidence-decision",
            )

    monkeypatch.setattr(routes, "ModelActivationService", _Service)
    malformed = client.post(
        "/api/model-evidence/activate",
        json={"decision_id": "decision-api-grey"},
    )
    stale = client.post(
        "/api/model-evidence/activate",
        json={"candidate_digest": "c" * 64, "decision_id": "stale"},
    )

    assert malformed.status_code == 422
    assert malformed.get_json() == {
        "accepted": False,
        "active_kind": "grey-box",
        "error": "model-activation-rejected",
        "detail": "request must contain exactly candidate_digest and decision_id",
    }
    assert stale.status_code == 409
    assert stale.get_json()["detail"] == "stale-confidence-decision"
    assert len(calls) == 1


def test_activate_route_treats_typed_contract_failures_as_unprocessable(client):
    response = client.post(
        "/api/model-evidence/activate",
        json={"candidate_digest": "not-a-digest", "decision_id": "decision-1"},
    )

    assert response.status_code == 422
    assert response.get_json()["error"] == "model-activation-rejected"


def test_activate_route_preserves_non_operator_policy_rejection(client, monkeypatch):
    class _Service:
        def activate(self, _request, *, now_ms):
            assert isinstance(now_ms, int)
            return ActivationRejected(
                ActivationRejectionCategory.CONFLICT,
                "manual activation requires operator-reviewed policy",
            )

    monkeypatch.setattr(routes, "ModelActivationService", _Service)
    response = client.post(
        "/api/model-evidence/activate",
        json={
            "candidate_digest": "c" * 64,
            "decision_id": "decision-api-grey",
        },
    )

    assert response.status_code == 409
    assert response.get_json()["detail"] == "manual activation requires operator-reviewed policy"


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
    assert (
        first.get_json()
        == duplicate.get_json()
        == {
            "accepted": True,
            "active_kind": "grey-box",
            "decision_id": prepared.decision_id,
            "reason": "operator rollback grey",
            "role_generation": candidate.role_generation + 1,
            "rollback_digest": incumbent.model_digest,
        }
    )
    rollbacks = [record for record in read_model_evidence() if isinstance(record.payload, RollbackEvidence)]
    assert len(rollbacks) == 1
    assert rollbacks[0].model_digest == candidate.model_digest


@pytest.mark.parametrize(
    ("outcome", "status", "payload"),
    (
        (
            ActivationAccepted("1" * 64, "decision-adapter", "2" * 64, 7),
            200,
            {
                "accepted": True,
                "phase": "prepared",
                "transaction_id": "1" * 64,
                "decision_id": "decision-adapter",
                "candidate_digest": "2" * 64,
                "role_generation": 7,
            },
        ),
        (
            ActivationRejected(
                ActivationRejectionCategory.CONFLICT,
                "conflict",
                cleanup_failed=True,
            ),
            409,
            {
                "accepted": False,
                "active_kind": "grey-box",
                "error": "model-activation-rejected",
                "detail": "conflict",
            },
        ),
        (
            ActivationRejected(ActivationRejectionCategory.INVALID_DATA, "invalid"),
            422,
            {
                "accepted": False,
                "active_kind": "grey-box",
                "error": "model-activation-rejected",
                "detail": "invalid",
            },
        ),
        (
            ActivationRejected(
                ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
                "unavailable",
            ),
            503,
            {
                "accepted": False,
                "active_kind": "grey-box",
                "error": "model-activation-rejected",
                "detail": "unavailable",
            },
        ),
        (
            ActivationRejected(ActivationRejectionCategory.CLEANUP_FAILED, "cleanup"),
            503,
            {
                "accepted": False,
                "active_kind": "grey-box",
                "error": "model-activation-rejected",
                "detail": "cleanup",
            },
        ),
    ),
)
def test_activate_route_exhaustively_maps_typed_service_outcomes(
    client,
    monkeypatch,
    outcome,
    status,
    payload,
):
    calls = []

    class _Service:
        def activate(self, request, *, now_ms):
            calls.append((request, now_ms))
            return outcome

    monkeypatch.setattr(routes, "ModelActivationService", _Service)

    response = client.post(
        "/api/model-evidence/activate",
        json={"candidate_digest": "2" * 64, "decision_id": "decision-adapter"},
    )

    assert response.status_code == status
    assert response.content_type == "application/json"
    assert response.data == (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    assert response.get_json() == payload
    assert len(calls) == 1
    assert calls[0][0].candidate_digest == "2" * 64
    assert isinstance(calls[0][1], int)


@pytest.mark.parametrize(
    ("outcome", "status", "payload"),
    (
        (
            RollbackAccepted("decision-adapter", "operator rollback", 8, "3" * 64),
            200,
            {
                "accepted": True,
                "active_kind": "grey-box",
                "decision_id": "decision-adapter",
                "reason": "operator rollback",
                "role_generation": 8,
                "rollback_digest": "3" * 64,
            },
        ),
        (
            RollbackRejected(RollbackRejectionCategory.CONFLICT, "stale"),
            409,
            {
                "accepted": False,
                "active_kind": "grey-box",
                "error": "model-activation-rejected",
                "detail": "stale",
            },
        ),
        (
            RollbackRejected(
                RollbackRejectionCategory.PERSISTENCE_UNAVAILABLE,
                "unavailable",
            ),
            503,
            {
                "accepted": False,
                "active_kind": "grey-box",
                "error": "model-activation-rejected",
                "detail": "unavailable",
            },
        ),
    ),
)
def test_rollback_route_exhaustively_maps_typed_service_outcomes(
    client,
    monkeypatch,
    outcome,
    status,
    payload,
):
    calls = []

    class _Service:
        def rollback(self, request, *, now_ms):
            calls.append((request, now_ms))
            return outcome

    monkeypatch.setattr(routes, "ModelActivationService", _Service)

    response = client.post(
        "/api/model-evidence/rollback",
        json={"reason": "operator rollback"},
    )

    assert response.status_code == status
    assert response.content_type == "application/json"
    assert response.get_json() == payload
    assert len(calls) == 1
    assert calls[0][0].reason == "operator rollback"
    assert isinstance(calls[0][1], int)
