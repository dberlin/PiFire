"""Unified model-learning report vocabulary and authority contracts."""

from __future__ import annotations
import json
from types import SimpleNamespace


import pytest

from common.model_evidence import (
    CandidateAssessmentEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RecorderGapEvidence,
    SchemaInvalidationEvidence,
)
from controller.model_learning import report as report_module
from controller.model_learning.contracts import (
    CandidateOrigin,
    FrameObservation,
    CheckStatus,
    FitRequest,
    FitStatus,
    FitWindowIdentity,
    LearningStatus,
)
from common.control_trace import (
    AmbientSource,
)
from common.controller_model_state import ControllerModelStore
from common.persistence.control_trace import read_control_trace_session
from common.persistence.model_evidence import read_model_evidence
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.model_learning.activation import (
    GreyControlPairDescriptor,
)
from controller.model_learning.report import (
    backend_learning_report,
    build_learning_artifact,
    build_learning_report,
    current_learning_report,
)
from controller.runtime.model_fitting import (
    CandidatePair,
    CandidatePreparation,
    GreyFitSuccess,
    TargetTimingEvidence,
    grey_config_digest,
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


def test_diagnostic_learning_report_wraps_only_the_backend_report(monkeypatch) -> None:
    canonical = build_learning_report(
        (),
        activation_state=_activation(phase="aborted"),
        live_status=_live(),
        calibration_command_high_water=7,
    )
    records = (_evidence(),)
    calls = []

    def backend_report():
        calls.append("backend")
        return canonical, records

    monkeypatch.setattr(report_module, "backend_learning_report", backend_report)

    report = report_module.diagnostic_learning_report()

    assert report.controller == "mpc"
    assert report.schema_version == 1
    assert report.revision == canonical.revision
    assert report.report == canonical.as_dict()
    assert calls == ["backend"]


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


@pytest.mark.parametrize(
    ("latest", "authorization", "next_cook"),
    (
        ("disabled", "blocked", False),
        ("insufficient", "blocked", False),
        ("rejected", "blocked", False),
        ("failed", "blocked", False),
        ("ready-for-review", "operator-review", False),
        ("accepted-next-cook", "next-cook", True),
        ("checkpoint-failure", "blocked", False),
    ),
)
def test_report_projects_every_final_cook_refit_outcome(monkeypatch, latest, authorization, next_cook) -> None:
    monkeypatch.setattr(report_module, "_validated_checkpoint", lambda checkpoint: checkpoint)
    payload = build_learning_report(
        (),
        activation_state=_activation(phase="aborted"),
        live_status=_live(),
        calibration_command_high_water=0,
        checkpoint={"cook_refit": {"status": "idle", "latest": latest}},
    ).as_dict()

    assert payload["cook_refit"] == {
        "status": "idle",
        "latest": latest,
        "final_status": latest,
        "authorization": authorization,
        "next_cook": next_cook,
    }


def test_report_separates_a_refit_that_never_ran_from_one_that_was_refused(monkeypatch) -> None:
    """No recorded outcome is not a refusal, and must not be reported as one.

    A cook_refit still at idle with no latest has never had a refit reach it --
    a fresh install, or a cook whose final checkpoint never persisted. Telling
    an operator that is "blocked" sends them looking for a block that does not
    exist, which is exactly what a stale checkpoint on a live grill did.
    """
    monkeypatch.setattr(report_module, "_validated_checkpoint", lambda checkpoint: checkpoint)
    payload = build_learning_report(
        (),
        activation_state=_activation(phase="aborted"),
        live_status=_live(),
        calibration_command_high_water=0,
        checkpoint={"cook_refit": {"status": "idle", "latest": None}},
    ).as_dict()

    assert payload["cook_refit"] == {
        "status": "idle",
        "latest": None,
        "final_status": "idle",
        "authorization": "not-run",
        "next_cook": False,
    }


def test_report_rejects_malformed_final_cook_refit(monkeypatch) -> None:
    monkeypatch.setattr(report_module, "_validated_checkpoint", lambda checkpoint: checkpoint)
    with pytest.raises(ValueError, match="invalid cook_refit"):
        build_learning_report(
            (),
            activation_state=_activation(phase="aborted"),
            live_status=_live(),
            calibration_command_high_water=0,
            checkpoint={"cook_refit": {"status": "idle", "latest": "activate-now"}},
        )


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


def test_completed_schema_migration_stops_gating_once_the_ledger_moves_on() -> None:
    controller = Controller(dict(DEFAULT_MPC_CONFIG), "C", {"u_min": 0.1, "u_max": 0.9})
    checkpoint = controller.get_model_snapshot()
    active_digest = checkpoint["identities"]["active_digest"]
    marker = ModelEvidenceRecord(
        evidence_id="mpc:schema-migration:1:defaults",
        kind=EvidenceKind.SCHEMA_INVALIDATION,
        session_id="mpc-schema-migration",
        cook_id=None,
        timestamp_ms=0,
        role_generation=0,
        model_digest=active_digest,
        provenance_digest=None,
        payload=SchemaInvalidationEvidence(previous_schema_version=3, reason="schema-invalidated"),
    )
    live = _live(status=LearningStatus.ACTIVE)
    live["checkpoint_digest"] = active_digest

    payload = build_learning_report(
        (marker, _evidence("post-migration-gap")),
        activation_state={**_activation(phase="aborted"), "incumbent_digest": active_digest},
        checkpoint=checkpoint,
        live_status=live,
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["errors"] == []
    assert payload["status"] == "active"
    assert payload["evidence"]["count"] == 2


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


def test_production_live_terminal_failure_overlays_prior_active_with_exact_reason() -> None:
    controller = Controller(dict(DEFAULT_MPC_CONFIG), "C", {"u_min": 0.1, "u_max": 0.9})
    controller.terminate_mpc_activation("native solver crashed")

    checkpoint = controller.get_model_snapshot()
    active_digest = checkpoint["identities"]["active_digest"]
    live = controller._grey_learning_runtime.learning_status()
    payload = build_learning_report(
        (),
        activation_state={
            **_activation(phase="active"),
            "incumbent_digest": active_digest,
            "candidate_digest": active_digest,
            "role_generation": 0,
            "candidate_generation": 0,
            "origin": None,
        },
        checkpoint=checkpoint,
        live_status=live,
        calibration_command_high_water=0,
    ).as_dict()
    assert live["failure"] == {
        "code": "activation-terminal",
        "detail": "native solver crashed",
        "terminal": True,
    }
    assert payload["status"] == "error", payload["errors"]
    assert payload["errors"] == ["activation-terminal"]
    assert payload["failure"] == live["failure"]


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


def test_real_operator_evaluation_persists_reviewed_assessment_for_restart_report(ds) -> None:
    controller = Controller(dict(DEFAULT_MPC_CONFIG), "C", {"u_min": 0.1, "u_max": 0.9})
    controller.bind_learning_identity("session-operator", "cook-operator", 0)
    incumbent = controller.active_control_pair.descriptor
    native_config = controller.mpc.config
    candidate_digest = grey_config_digest(native_config)
    candidate_descriptor = GreyControlPairDescriptor(
        model_digest=candidate_digest,
        configuration=dict(incumbent.configuration),
        estimator_kind=incumbent.estimator_kind,
        solver_kind=incumbent.solver_kind,
        candidate_generation=1,
        role_generation=1,
    )
    request = FitRequest(
        request_id="operator-request",
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        candidate_generation=1,
        window=FitWindowIdentity(
            session_id="session-operator",
            cook_id="cook-operator",
            first_observation_sequence=3,
            last_observation_sequence=9,
            role_generation=0,
            incumbent_digest=incumbent.model_digest,
            configuration_digest="c" * 64,
        ),
    )
    candidate = GreyFitSuccess(
        request=request,
        config=native_config,
        rmse_c=1.0,
        max_error_c=1.5,
        identifiability=0.9,
        sample_count=12,
        temperature_band_c=(80.0, 120.0),
        nfev=4,
    )
    preparation = CandidatePreparation.accepted_for_test(
        candidate=candidate,
        candidate_pair=CandidatePair(object(), object()),
        incumbent_pair=CandidatePair(object(), object()),
        timing=TargetTimingEvidence("candidate-dry-solve", 3, 1.0, 25.0),
    )
    evaluation = SimpleNamespace(
        decision_id="operator-decision",
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

    controller.close()
    checkpoint = ControllerModelStore().load("mpc")
    assert checkpoint is not None
    assert checkpoint["revision"] == 1
    assert checkpoint["active_pair"] == incumbent.to_dict()
    assert checkpoint["candidate_pair"] == candidate_descriptor.to_dict()
    report, records = backend_learning_report()
    artifact = json.loads(build_learning_artifact(report, records))

    assessments = [record.payload for record in records if record.kind is EvidenceKind.CANDIDATE_ASSESSMENT]
    assert len(assessments) == 1
    assert assessments[0].origin == CandidateOrigin.OPERATOR_CALIBRATION.value
    assert assessments[0].policy == "operator-reviewed"
    assert report.as_dict()["status"] == "ready-for-review", report.as_dict()
    assert report.as_dict()["candidate"]["policy"] == "operator-reviewed"
    assert artifact["report"] == report.as_dict()


def test_real_fit_submission_persists_queued_lifecycle_for_restart_report(ds) -> None:
    controller = Controller(dict(DEFAULT_MPC_CONFIG), "C", {"u_min": 0.1, "u_max": 0.9})
    controller.bind_learning_identity("session-submit", "cook-submit", 0)
    incumbent = controller.active_control_pair.descriptor
    request = FitRequest(
        request_id="request-submit",
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        candidate_generation=1,
        window=FitWindowIdentity(
            session_id="session-submit",
            cook_id="cook-submit",
            first_observation_sequence=1,
            last_observation_sequence=7,
            configuration_digest="c" * 64,
            incumbent_digest=incumbent.model_digest,
            role_generation=0,
        ),
    )

    class _Learning:
        pending_request = None
        prepared = None
        handoff = None
        passive_history = SimpleNamespace(observations=())

        def observe_completed_frame(self, *_args, **_kwargs):
            return SimpleNamespace(
                request=request,
                history=SimpleNamespace(accepted=True, reasons=()),
                completed_forecasts=(),
            )

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return None

        def close(self):
            return None

    assert ControllerModelStore().save("mpc", controller.get_model_snapshot()) is True
    controller._grey_learning_runtime._learning = _Learning()
    controller._grey_learning_runtime._register_learning_forecasts = lambda _observation: ()
    controller.observe_frame(
        FrameObservation(
            frame_start_s=25.0,
            frame_end_s=50.0,
            temp_c=90.0,
            setpoint_c=120.0,
            ambient_c=20.0,
            requested_q=0.4,
            realized_q=0.4,
            requested_auger_duty=0.4,
            delivered_on_s=10.0,
            requested_fan_duty=0.5,
            actual_fan_duty=0.5,
            result_revision=1,
            output_source="controller",
            lid_open=False,
            safety_inhibited=False,
            manual_override=False,
            stale=False,
            skipped=False,
            reset=False,
            continuous=True,
            role_generation=0,
            observation_sequence=1,
            ambient_source=AmbientSource.CONFIGURED,
        )
    )
    controller.poll_learning_off_path(
        live_origin=CandidateOrigin.OPERATOR_CALIBRATION,
    )
    controller.close()

    report, records = backend_learning_report()
    artifact = json.loads(build_learning_artifact(report, records))
    fits = [record.payload for record in records if record.kind is EvidenceKind.FIT_LIFECYCLE]
    trace = read_control_trace_session("session-submit")

    assert [payload.status for payload in fits] == ["queued"]
    assert [record.event_kind.value for record in trace] == ["fit_lifecycle"]
    assert artifact["report"] == report.as_dict()


@pytest.mark.parametrize(
    ("case", "expected_fit_status", "expected_rejection"),
    (
        ("fit-error", "failed", "fit-error"),
        ("stale", "stale", None),
        ("identifiability", "succeeded", "identifiability"),
        ("preparation", "succeeded", "native-dry-solve-failed"),
        ("success", "succeeded", None),
    ),
)
def test_real_fit_completion_branches_persist_lifecycle_for_restart_report(
    ds,
    case,
    expected_fit_status,
    expected_rejection,
) -> None:
    controller = Controller(dict(DEFAULT_MPC_CONFIG), "C", {"u_min": 0.1, "u_max": 0.9})
    controller.bind_learning_identity(f"session-{case}", f"cook-{case}", 0)
    incumbent = controller.active_control_pair.descriptor
    native_config = controller.mpc.config
    candidate_digest = grey_config_digest(native_config)
    request = FitRequest(
        request_id=f"request-{case}",
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        candidate_generation=1,
        window=FitWindowIdentity(
            session_id=f"session-{case}",
            cook_id=f"cook-{case}",
            first_observation_sequence=2,
            last_observation_sequence=8,
            configuration_digest="c" * 64,
            incumbent_digest=incumbent.model_digest,
            role_generation=0,
        ),
    )
    outcome = (
        SimpleNamespace(request=request, detail="native fitter crashed")
        if case == "fit-error"
        else SimpleNamespace(request=request, config=native_config)
    )
    preparation = (
        SimpleNamespace(
            accepted=False,
            candidate_digest=candidate_digest,
            candidate_pair=None,
            candidate=SimpleNamespace(request=request, config=native_config),
            blockers=("native-dry-solve-failed",),
            dry_solve_finite=False,
            timing=SimpleNamespace(accepted=True),
        )
        if case == "preparation"
        else None
    )
    delivery = SimpleNamespace(
        message=SimpleNamespace(request=request, outcome=outcome),
        stale_reasons=("role-generation-changed",) if case == "stale" else (),
        preparation=preparation,
        blockers=(("fit-error",) if case == "fit-error" else ("identifiability",) if case == "identifiability" else ()),
    )

    class _Learning:
        prepared = preparation
        handoff = None
        pending_request = request

        def poll_fit_off_path(self, **_kwargs):
            return delivery

        def evaluate_ready_off_path(self):
            return None

        def close(self):
            return None

    checkpoint = controller.get_model_snapshot()
    assert ControllerModelStore().save("mpc", checkpoint) is True
    controller._grey_learning_runtime._learning = _Learning()
    controller.poll_learning_off_path(
        live_origin=CandidateOrigin.OPERATOR_CALIBRATION,
    )
    controller.close()

    report, records = backend_learning_report()
    artifact = json.loads(build_learning_artifact(report, records))
    fits = [record.payload for record in records if record.kind is EvidenceKind.FIT_LIFECYCLE]
    assessments = [record.payload for record in records if record.kind is EvidenceKind.CANDIDATE_ASSESSMENT]
    trace = read_control_trace_session(f"session-{case}")

    assert fits[-1].status == expected_fit_status
    assert report.as_dict()["fit"]["status"] == expected_fit_status
    if case == "fit-error":
        assert report.as_dict()["status"] == "error"
        assert "native fitter crashed" in report.as_dict()["errors"]
    if expected_rejection is None:
        assert assessments == []
    else:
        assert expected_rejection in assessments[-1].rejection_reasons
    assert {record.event_kind.value for record in trace} >= {"fit_lifecycle"}
    assert artifact["report"] == report.as_dict()


def test_real_evaluation_blocker_persists_rejection_context_before_retirement(ds) -> None:
    controller = Controller(dict(DEFAULT_MPC_CONFIG), "C", {"u_min": 0.1, "u_max": 0.9})
    controller.bind_learning_identity("session-evaluation-blocker", "cook-blocked", 0)
    incumbent = controller.active_control_pair.descriptor
    candidate_config = controller.mpc.config
    candidate_digest = grey_config_digest(candidate_config)
    request = FitRequest(
        request_id="blocked-evaluation-request",
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        candidate_generation=1,
        window=FitWindowIdentity(
            session_id="session-evaluation-blocker",
            cook_id="cook-blocked",
            first_observation_sequence=7,
            last_observation_sequence=15,
            role_generation=0,
            incumbent_digest=incumbent.model_digest,
            configuration_digest="d" * 64,
        ),
    )
    preparation = SimpleNamespace(
        accepted=True,
        candidate_digest=candidate_digest,
        candidate_pair=SimpleNamespace(estimator=object(), controller=object()),
        candidate=SimpleNamespace(
            request=request,
            config=candidate_config,
            rmse_c=1.5,
            sample_count=16,
            temperature_band_c=(75.0, 125.0),
            nfev=5,
        ),
        blockers=(),
        dry_solve_finite=True,
        timing=SimpleNamespace(accepted=True),
    )
    evaluation = SimpleNamespace(
        decision_id="blocked-evaluation-decision",
        accepted=False,
        blockers=("confidence-window",),
        role_generation=0,
        candidate_generation=1,
        incumbent_digest=incumbent.model_digest,
        challenger_digest=candidate_digest,
        completed_origins=(),
    )

    class _Learning:
        handoff = None
        pending_request = None

        def __init__(self):
            self.prepared = preparation
            self.retired = []

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return evaluation

        def retire_evaluated_candidate(self, retired):
            self.retired.append(retired)
            self.prepared = None

        def close(self):
            return None

    learning = _Learning()
    checkpoint = controller.get_model_snapshot()
    assert ControllerModelStore().save("mpc", checkpoint) is True
    controller._grey_learning_runtime._learning = learning
    controller._grey_learning_runtime._grey_evaluation_payload = lambda *_args, **_kwargs: SimpleNamespace()
    controller.poll_learning_off_path(
        live_origin=CandidateOrigin.OPERATOR_CALIBRATION,
    )
    controller.close()

    report, records = backend_learning_report()
    artifact = json.loads(build_learning_artifact(report, records))
    assessments = [record for record in records if record.kind is EvidenceKind.CANDIDATE_ASSESSMENT]
    confidence = [record for record in records if record.kind is EvidenceKind.CONFIDENCE_DECISION]
    trace = read_control_trace_session("session-evaluation-blocker")

    assert learning.retired == [evaluation]
    assert len(assessments) == 1
    assert assessments[0].model_digest == candidate_digest
    assert assessments[0].provenance_digest == incumbent.model_digest
    assert assessments[0].payload.rejection_reasons == ("confidence-window",)
    assert len(confidence) == 1
    assert confidence[0].model_digest == candidate_digest
    assert confidence[0].provenance_digest == incumbent.model_digest
    assert confidence[0].payload.blocked is True
    assert confidence[0].payload.reason == "confidence-window"
    assert {record.event_kind.value for record in trace} >= {"candidate_assessment"}
    assert report.as_dict()["candidate"]["assessment"]["rejection_reasons"] == ["confidence-window"]
    assert artifact["report"] == report.as_dict()
