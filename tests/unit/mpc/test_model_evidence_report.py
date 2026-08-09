"""Unified model-learning report vocabulary and authority contracts."""

from __future__ import annotations
import json


import pytest

from common.model_evidence import (
    CandidateAssessmentEvidence,
    ActivationLifecycleEvidence,
    EvidenceKind,
    FitLifecycleEvidence,
    LearningFailureEvidence,
    ModelEvidenceRecord,
    RecorderGapEvidence,
    SchemaInvalidationEvidence,
)
from controller.model_learning import report as report_module
from controller.model_learning.contracts import CandidateOrigin, CheckStatus, FitStatus, LearningStatus
from common.control_trace import (
    GreyActivationLifecyclePayload,
    GreyCandidateAssessmentPayload,
    GreyFitLifecyclePayload,
    GreyLearningFailurePayload,
)
from common.controller_model_state import ControllerModelStore
from common.datastore_accessors import read_control_trace_session, read_model_evidence
from controller.mpc import Controller, _DEFAULTS
from controller.model_learning.report import (
    backend_learning_report,
    build_learning_artifact,
    build_learning_report,
    current_learning_report,
)


_CANDIDATE = "b" * 64
_INCUMBENT = "a" * 64


def _evidence(evidence_id: str = "gap-1") -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.RECORDER_GAP,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=1,
        role_generation=4,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
        payload=RecorderGapEvidence(lost_record_count=1, reason="recorder-gap"),
    )


def _activation(*, phase: str = "prepared") -> dict[str, object]:
    return {
        "phase": phase,
        "incumbent_digest": _INCUMBENT,
        "candidate_digest": _CANDIDATE,
        "role_generation": 4,
        "candidate_generation": 9,
        "decision_id": "decision-9",
        "origin": CandidateOrigin.OPERATOR_CALIBRATION.value,
    }


def _live(
    *,
    status: LearningStatus = LearningStatus.COLLECTING,
    fit_status: FitStatus = FitStatus.IDLE,
    build_status: CheckStatus = CheckStatus.NOT_RUN,
) -> dict[str, object]:
    return {
        "status": status,
        "fit_status": fit_status,
        "origin": CandidateOrigin.OPERATOR_CALIBRATION,
        "role_generation": 4,
        "candidate_generation": 9,
        "candidate_digest": _CANDIDATE,
        "checkpoint_digest": _INCUMBENT,
        "checks": {"native-build": build_status},
    }


def _payload(*, status: LearningStatus = LearningStatus.COLLECTING) -> dict[str, object]:
    return build_learning_report(
        (),
        activation_state=_activation(phase="aborted"),
        live_status=_live(status=status),
        calibration_command_high_water=7,
    ).as_dict()


def _section(payload: dict[str, object], name: str) -> dict[str, object]:
    value = payload[name]
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("status", tuple(LearningStatus))
def test_report_emits_only_the_locked_status_vocabulary(status: LearningStatus) -> None:
    payload = _payload(status=status)

    assert payload["status"] == status.value
    assert payload["status"] in {
        "collecting",
        "insufficient-excitation",
        "fitting",
        "evaluating",
        "ready-for-review",
        "activating",
        "active",
        "fallback",
        "error",
        "schema-invalidated",
    }


def test_report_serializes_locked_fit_and_check_statuses_without_linear_model_fields() -> None:
    payload = build_learning_report(
        (),
        activation_state=_activation(),
        live_status=_live(fit_status=FitStatus.RUNNING, build_status=CheckStatus.PASSED),
        calibration_command_high_water=7,
    ).as_dict()

    fit = _section(payload, "fit")
    checks = _section(payload, "checks")
    candidate = _section(payload, "candidate")
    assert fit["status"] == "running"
    assert checks["native-build"] == "passed"
    assert candidate["origin"] == "operator-calibration"
    assert candidate["role_generation"] == 4
    assert candidate["candidate_generation"] == 9
    assert "pole_magnitude" not in candidate
    assert "covariance" not in candidate
    assert "state_alignment" not in candidate


def test_prepared_activation_is_reported_as_activating_not_active() -> None:
    payload = build_learning_report(
        (),
        activation_state=_activation(phase="prepared"),
        live_status=_live(status=LearningStatus.ACTIVATING),
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == "activating"
    activation = _section(payload, "activation")
    active_model = _section(payload, "active_model")
    assert activation["phase"] == "prepared"
    assert activation["pending_frame_boundary_swap"] is True
    assert active_model["digest"] == _INCUMBENT


def test_report_projects_calibration_command_high_water_without_making_it_a_second_state_machine() -> None:
    payload = _payload()

    calibration = _section(payload, "calibration")
    assert calibration["command_high_water"] == 7
    assert "command_status" not in calibration


def test_current_report_cache_key_includes_every_authoritative_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = report_module.build_learning_report

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(report_module, "build_learning_report", counted)
    base_records = (_evidence(),)
    base_activation = _activation()
    base_live = _live()
    base_checkpoint = {"cache-token": 1}

    first = current_learning_report(
        base_records,
        activation_state=base_activation,
        checkpoint=base_checkpoint,
        live_status=base_live,
        calibration_command_high_water=7,
    )
    duplicate = current_learning_report(
        base_records,
        activation_state=dict(base_activation),
        live_status=dict(base_live),
        checkpoint=dict(base_checkpoint),
        calibration_command_high_water=7,
    )
    assert first is duplicate
    assert calls == 1

    current_learning_report(
        (_evidence("gap-2"),),
        activation_state=base_activation,
        checkpoint=base_checkpoint,
        live_status=base_live,
        calibration_command_high_water=7,
    )
    current_learning_report(
        base_records,
        activation_state={**base_activation, "phase": "aborted"},
        checkpoint=base_checkpoint,
        live_status=base_live,
        calibration_command_high_water=7,
    )
    current_learning_report(
        base_records,
        activation_state=base_activation,
        checkpoint=base_checkpoint,
        live_status={**base_live, "status": LearningStatus.ERROR},
        calibration_command_high_water=7,
    )
    current_learning_report(
        base_records,
        activation_state=base_activation,
        checkpoint=base_checkpoint,
        live_status=base_live,
        calibration_command_high_water=8,
    )
    current_learning_report(
        base_records,
        activation_state=base_activation,
        checkpoint={"cache-token": 2},
        live_status=base_live,
        calibration_command_high_water=7,
    )

    assert calls == 6


def test_invalid_live_checkpoint_generation_fails_closed_visibly() -> None:
    live = _live()
    live["role_generation"] = 5

    payload = build_learning_report(
        (),
        activation_state=_activation(),
        live_status=live,
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == "error"
    assert payload["errors"] == ["live-role-generation-mismatch"]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("candidate_digest", "c" * 64, "live-candidate-digest-mismatch"),
        ("origin", CandidateOrigin.COOK_REFIT, "live-candidate-origin-mismatch"),
    ),
)
def test_inconsistent_live_candidate_authority_fails_closed_visibly(
    field: str,
    value: object,
    error: str,
) -> None:
    live = _live()
    live[field] = value

    payload = build_learning_report(
        (),
        activation_state=_activation(),
        live_status=live,
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == "error"
    assert payload["errors"] == [error]


def test_retired_evidence_is_counted_only_as_audit_history() -> None:
    current = _evidence("current-gap")
    retired = _evidence("retired-gap").model_copy(update={"schema_version": 2})

    payload = build_learning_report(
        (retired, current),
        activation_state=_activation(phase="aborted"),
        live_status=_live(),
        calibration_command_high_water=7,
    ).as_dict()

    evidence = _section(payload, "evidence")
    assert evidence["count"] == 1
    assert evidence["audit_count"] == 2
    assert evidence["retired_excluded"] == 1


def test_retired_schema_invalidation_audit_cannot_gate_current_report() -> None:
    retired = ModelEvidenceRecord(
        evidence_id="retired-invalidation",
        kind=EvidenceKind.SCHEMA_INVALIDATION,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=2,
        role_generation=4,
        schema_version=2,
        model_digest=None,
        provenance_digest=None,
        payload=SchemaInvalidationEvidence(previous_schema_version=1, reason="old-migration"),
    )

    payload = build_learning_report(
        (retired,),
        activation_state=_activation(phase="aborted"),
        live_status=_live(status=LearningStatus.ACTIVE),
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == "active"
    assert payload["evidence"]["count"] == 0


@pytest.mark.parametrize(
    ("live", "expected"),
    (
        ({**_live(), "fit_status": FitStatus.QUEUED}, "fitting"),
        ({**_live(), "fit_status": FitStatus.RUNNING}, "fitting"),
        ({**_live(), "pending_swap": True}, "activating"),
    ),
)
def test_live_transient_phases_overlay_without_changing_durable_identity(live, expected) -> None:
    payload = build_learning_report(
        (),
        activation_state=_activation(phase="aborted"),
        live_status=live,
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == expected
    assert _section(payload, "active_model")["digest"] == _INCUMBENT
    assert _section(payload, "candidate")["digest"] == _CANDIDATE


def test_live_terminal_failure_overrides_stale_active_lifecycle() -> None:
    live = _live(status=LearningStatus.ACTIVE)
    live["failure"] = {
        "code": "activation-terminal",
        "detail": "native solver crashed",
        "terminal": True,
    }

    payload = build_learning_report(
        (),
        activation_state=_activation(phase="active"),
        live_status=live,
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == "error"
    assert payload["errors"] == ["activation-terminal"]
    assert payload["failure"] == live["failure"]


def test_manual_policy_comes_from_matching_current_candidate_assessment() -> None:
    assessment = ModelEvidenceRecord(
        evidence_id="assessment-reviewed",
        kind=EvidenceKind.CANDIDATE_ASSESSMENT,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=3,
        role_generation=4,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
        payload=CandidateAssessmentEvidence(
            decision_id="decision-1",
            origin="operator-calibration",
            policy="operator-reviewed",
            fit_accepted=True,
            identifiability_accepted=True,
            native_build="passed",
            native_dry_solve="passed",
            target_timing="passed",
            confidence_accepted=True,
        ),
    )
    live = _live(status=LearningStatus.EVALUATING)
    live["origin"] = CandidateOrigin.OPERATOR_CALIBRATION

    payload = build_learning_report(
        (assessment,),
        activation_state={**_activation(phase="aborted"), "policy": None},
        live_status=live,
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == "ready-for-review"
    assert payload["candidate"]["policy"] == "operator-reviewed"


def test_missing_and_incompatible_authority_are_explicit_terminal_states() -> None:
    missing = build_learning_report(
        (),
        activation_state=None,
        live_status=None,
        calibration_command_high_water=0,
    ).as_dict()
    incompatible = build_learning_report(
        (),
        activation_state=None,
        checkpoint={"version": 3, "revision": 1},
        live_status=None,
        calibration_command_high_water=0,
    ).as_dict()

    assert missing["status"] == "error"
    assert missing["errors"] == ["checkpoint-missing"]
    assert incompatible["status"] == "schema-invalidated"
    assert incompatible["errors"] == ["checkpoint-schema-invalid"]


def test_production_grey_lifecycle_writers_persist_matching_evidence_trace_report_and_artifact(ds):
    controller = Controller(dict(_DEFAULTS), "C", {"u_min": 0.1, "u_max": 0.9})
    controller.bind_learning_identity("session-lifecycle", "cook-lifecycle", 0)
    incumbent = controller.active_control_pair.descriptor
    candidate_digest = "b" * 64
    events = (
        (
            FitLifecycleEvidence(
                request_id="request-1",
                status="succeeded",
                origin="operator-calibration",
                policy="operator-reviewed",
                window_id="window-1",
            ),
            GreyFitLifecyclePayload(
                request_id="request-1",
                status="succeeded",
                origin="operator-calibration",
                policy="operator-reviewed",
                window_id="window-1",
            ),
        ),
        (
            CandidateAssessmentEvidence(
                decision_id="decision-1",
                origin="operator-calibration",
                policy="operator-reviewed",
                fit_accepted=True,
                identifiability_accepted=True,
                native_build="passed",
                native_dry_solve="passed",
                target_timing="passed",
                confidence_accepted=True,
            ),
            GreyCandidateAssessmentPayload(
                decision_id="decision-1",
                origin="operator-calibration",
                policy="operator-reviewed",
                fit_accepted=True,
                identifiability_accepted=True,
                native_build="passed",
                native_dry_solve="passed",
                target_timing="passed",
                confidence_accepted=True,
            ),
        ),
        (
            ActivationLifecycleEvidence(
                decision_id="decision-1",
                phase="aborted",
                origin="operator-calibration",
                policy="operator-reviewed",
                reason="native-dry-solve-failed",
            ),
            GreyActivationLifecyclePayload(
                decision_id="decision-1",
                phase="aborted",
                origin="operator-calibration",
                policy="operator-reviewed",
                reason="native-dry-solve-failed",
            ),
        ),
        (
            LearningFailureEvidence(
                code="native-dry-solve-failed",
                detail="candidate failed finite dry solve",
                terminal=True,
            ),
            GreyLearningFailurePayload(
                code="native-dry-solve-failed",
                detail="candidate failed finite dry solve",
                terminal=True,
            ),
        ),
    )
    for offset, (evidence_payload, trace_payload) in enumerate(events):
        controller._persist_grey_lifecycle(
            evidence_payload,
            trace_payload,
            timestamp_ms=10 + offset,
            role_generation=1,
            model_digest=candidate_digest,
            provenance_digest=incumbent.model_digest,
        )
    worker = controller._activation_persistence_worker
    assert worker is not None
    worker.flush_and_stop(timeout=2.0)
    checkpoint = controller.get_model_snapshot()
    checkpoint["challenger"] = {
        "parameters": checkpoint["active"]["parameters"],
        "metadata": checkpoint["active"]["metadata"],
    }
    checkpoint["origin"] = "operator-calibration"
    checkpoint["policy"] = "operator-reviewed"
    checkpoint["identities"]["candidate_digest"] = candidate_digest
    checkpoint["identities"]["candidate_generation"] = 1
    assert ControllerModelStore().save("mpc", checkpoint) is True

    report, records = backend_learning_report()
    artifact = build_learning_artifact(report, records)
    trace = read_control_trace_session("session-lifecycle")

    assert {record.kind.value for record in records} >= {
        "fit_lifecycle",
        "candidate_assessment",
        "activation_lifecycle",
        "learning_failure",
    }
    assert {record.event_kind.value for record in trace} == {
        "fit_lifecycle",
        "candidate_assessment",
        "activation_lifecycle",
        "learning_failure",
    }
    assert report.as_dict()["status"] == "error"
    assert json.loads(artifact)["report"] == report.as_dict()
