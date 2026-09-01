"""Unified model-learning report vocabulary and authority contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from common.control_trace import (
    AmbientSource,
)
from common.controller_model_state import ControllerModelStore
from common.learning_trajectory import ModelFitLineage
from common.model_evidence import (
    ActivationLifecycleEvidence,
    CandidateAssessmentEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RecorderGapEvidence,
    SchemaInvalidationEvidence,
)
from common.persistence.control_trace import read_control_trace_session
from common.persistence.model_challenger import (
    ModelChallengerState,
    abort_model_challenger_activation,
    create_model_challenger,
    read_model_challenger,
    retire_model_challenger,
)
from common.persistence.model_evidence import read_model_evidence
from controller.model_learning import report as report_module
from controller.model_learning.activation import (
    GreyControlPairDescriptor,
)
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    CheckStatus,
    FitRequest,
    FitStatus,
    FrameObservation,
    LearningStatus,
    activation_policy_for_origin,
)
from controller.model_learning.report import (
    backend_learning_report,
    build_learning_artifact,
    build_learning_report,
    current_learning_report,
)
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.runtime.model_fitting import grey_config_digest
from tests.unit.common._model_challenger_helpers import (
    _corpus,
)
from tests.unit.common._model_challenger_helpers import (
    _state as _stored_challenger,
)

_CANDIDATE = "b" * 64
_INCUMBENT = "a" * 64


_REQUIRED_HORIZONS = (3, 15, 45, 90, 180)


def _persist_evaluating_challenger(
    incumbent: GreyControlPairDescriptor,
    candidate: GreyControlPairDescriptor,
    request: FitRequest,
) -> ModelChallengerState:
    corpus = request.fit_corpus
    policy = activation_policy_for_origin(request.origin)
    state = ModelChallengerState(
        schema_version=1,
        challenger_id=f"challenger-{request.request_id}",
        revision=0,
        phase="evaluating",
        origin=request.origin,
        policy=policy,
        fit_corpus=corpus,
        fit_lineage=ModelFitLineage(
            request_id=request.request_id,
            parent_incumbent_digest=incumbent.model_digest,
            parent_incumbent_generation=incumbent.role_generation,
            candidate_generation=candidate.candidate_generation,
            fit_corpus=corpus,
            fit_corpus_digest=corpus.corpus_digest,
            trigger_origin=request.origin.value,
            result_status="succeeded",
            candidate_digest=candidate.model_digest,
        ),
        fit_preparation={
            "request_id": request.request_id,
            "accepted": True,
            "candidate_digest": candidate.model_digest,
            "required_horizons": list(_REQUIRED_HORIZONS),
            "native_build": "passed",
            "dry_solve": "passed",
            "target_timing": None,
            "fit_corpus_digest": request.fit_corpus.corpus_digest,
            "fit_result": {
                "rmse_c": 1.5,
                "max_error_c": 2.0,
                "identifiability": 0.9,
                "sample_count": 16,
                "temperature_band_c": [75.0, 125.0],
                "nfev": 5,
                "result_digest": "e" * 64,
            },
        },
        controller_configuration_digest=request.configuration_digest,
        incumbent=incumbent,
        candidate=candidate,
        calibration_manifest=None,
        evaluation_epoch=0,
        evaluation_round=0,
        consecutive_wins=0,
        required_wins=2,
        last_decision_id=None,
        last_evidence_id=None,
        activation_transaction_id=None,
        retirement_reason=None,
        created_ms=1,
        updated_ms=1,
        retired_ms=None,
    )
    current = read_model_challenger()
    if current is not None and current != state and current.phase != "retired":
        retired_ms = current.updated_ms + 1
        if current.phase == "activating":
            abort_model_challenger_activation(
                expected_revision=current.revision,
                activation_transaction_id=current.activation_transaction_id,
                reason="legacy-harness-replaced",
                retired_ms=retired_ms,
            )
        else:
            retire_model_challenger(
                expected_revision=current.revision,
                reason="legacy-harness-replaced",
                retired_ms=retired_ms,
            )
    return create_model_challenger(state)


def _fit_request(
    request_id: str,
    incumbent: GreyControlPairDescriptor,
    *,
    configuration_digest: str = "c" * 64,
) -> FitRequest:
    return FitRequest(
        request_id=request_id,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        fit_corpus=_corpus(request_id),
        configuration_digest=configuration_digest,
        parent_incumbent_digest=incumbent.model_digest,
        parent_incumbent_generation=incumbent.role_generation,
        candidate_generation=1,
    )


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
        schema_version=4,
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


def test_report_status_vocabulary_is_automatic_causal_progress_only() -> None:
    assert {status.value for status in LearningStatus} == {
        "warming",
        "collecting",
        "fitting",
        "evaluating",
        "interrupted",
        "qualified",
        "activating",
        "active",
        "fallback",
        "error",
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
    assert fit["status"] == "running"
    assert checks["native-build"] == "passed"
    assert payload["candidate"] is None
    identities = _section(payload, "identities")
    assert identities["candidate_digest"] == _CANDIDATE
    assert identities["candidate_generation"] == 9


def test_report_projects_activation_policy_without_fabricating_a_challenger() -> None:
    activation = _activation(phase="aborted")
    activation["origin"] = CandidateOrigin.PASSIVE_ONLINE.value
    activation["policy"] = ActivationPolicy.CAUSAL_AUTO.value
    live = _live(status=LearningStatus.EVALUATING)
    live["origin"] = CandidateOrigin.PASSIVE_ONLINE

    payload = build_learning_report(
        (),
        activation_state=activation,
        live_status=live,
        calibration_command_high_water=0,
    ).as_dict()

    assert payload["candidate"] is None
    activation_report = _section(payload, "activation")
    assert activation_report["origin"] == CandidateOrigin.PASSIVE_ONLINE.value
    assert activation_report["policy"] == ActivationPolicy.CAUSAL_AUTO.value


def test_prior_active_activation_does_not_override_new_evaluating_candidate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        report_module,
        "_validated_checkpoint",
        lambda checkpoint: checkpoint,
    )
    challenger = _stored_challenger(phase="evaluating")
    activation = _activation(phase="active")
    activation.update(
        {
            "incumbent_digest": challenger.incumbent.model_digest,
            "role_generation": challenger.incumbent.role_generation,
            "policy": ActivationPolicy.CAUSAL_AUTO.value,
        }
    )
    live = _live(status=LearningStatus.EVALUATING)
    live.update(
        {
            "activation_phase": "aborted",
            "role_generation": challenger.incumbent.role_generation,
            "candidate_digest": challenger.candidate.model_digest,
            "candidate_generation": challenger.candidate.candidate_generation,
            "checkpoint_digest": challenger.incumbent.model_digest,
            "origin": CandidateOrigin.PASSIVE_ONLINE,
        }
    )
    checkpoint = {
        "origin": None,
        "policy": None,
        "identities": {
            "active_digest": challenger.incumbent.model_digest,
            "active_generation": challenger.incumbent.role_generation,
            "rollback_digest": None,
            "rollback_generation": None,
        },
        "challenger_authority": {
            "challenger_id": challenger.challenger_id,
            "revision": challenger.revision,
        },
    }

    payload = build_learning_report(
        (),
        activation_state=activation,
        checkpoint=checkpoint,
        live_status=live,
        calibration_command_high_water=0,
        challenger_state=challenger,
    ).as_dict()

    candidate = _section(payload, "candidate")
    projected_activation = _section(payload, "activation")
    assert payload["status"] == LearningStatus.EVALUATING.value
    assert payload["blockers"] == []
    assert candidate["digest"] == challenger.candidate.model_digest
    assert candidate["candidate_generation"] == challenger.candidate.candidate_generation
    assert candidate["origin"] == CandidateOrigin.PASSIVE_ONLINE.value
    assert candidate["policy"] == ActivationPolicy.CAUSAL_AUTO.value
    assert projected_activation["phase"] == "aborted"
    assert payload["active_model"]["digest"] == challenger.incumbent.model_digest


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


def test_invalid_live_candidate_origin_fails_closed_visibly() -> None:
    live = _live()
    live["origin"] = "retired-origin"

    payload = build_learning_report(
        (),
        activation_state=_activation(),
        live_status=live,
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == "error"
    assert payload["errors"] == ["candidate-origin-invalid"]


def test_current_lifecycle_matches_production_activation_decision_key() -> None:
    current_lifecycle = ModelEvidenceRecord(
        evidence_id="current-active",
        kind=EvidenceKind.ACTIVATION_LIFECYCLE,
        session_id="session-current",
        cook_id="cook-current",
        timestamp_ms=2,
        role_generation=4,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
        schema_version=4,
        payload=ActivationLifecycleEvidence(
            decision_id="decision-9",
            phase="active",
            origin=CandidateOrigin.OPERATOR_CALIBRATION.value,
            policy=ActivationPolicy.CAUSAL_AUTO.value,
        ),
    )
    activation = _activation(phase="active")
    activation["evidence_decision_id"] = activation.pop("decision_id")

    payload = build_learning_report(
        (current_lifecycle,),
        activation_state=activation,
        live_status=_live(status=LearningStatus.ACTIVE),
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["latest_lifecycle"] == {
        "decision_id": "decision-9",
        "phase": "active",
        "origin": "operator-calibration",
        "policy": "causal-auto",
        "reason": None,
        "payload_type": "activation_lifecycle",
    }


def test_stale_active_lifecycle_cannot_override_a_new_live_challenger() -> None:
    stale_digest = "c" * 64
    stale_lifecycle = ModelEvidenceRecord(
        evidence_id="stale-active",
        kind=EvidenceKind.ACTIVATION_LIFECYCLE,
        session_id="session-old",
        cook_id="cook-old",
        timestamp_ms=2,
        role_generation=3,
        model_digest=stale_digest,
        provenance_digest=_INCUMBENT,
        schema_version=4,
        payload=ActivationLifecycleEvidence(
            decision_id="decision-old",
            phase="active",
            origin=CandidateOrigin.PASSIVE_ONLINE.value,
            policy=ActivationPolicy.CAUSAL_AUTO.value,
        ),
    )
    stale_activation = {
        **_activation(phase="active"),
        "candidate_digest": stale_digest,
        "role_generation": 3,
        "candidate_generation": 8,
        "decision_id": "decision-old",
    }

    payload = build_learning_report(
        (stale_lifecycle,),
        activation_state=stale_activation,
        live_status=_live(status=LearningStatus.EVALUATING),
        calibration_command_high_water=7,
    ).as_dict()

    assert payload["status"] == "evaluating"
    assert payload["latest_lifecycle"] is None


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


def test_completed_schema_migration_stops_gating_once_the_ledger_moves_on(ds) -> None:
    controller = Controller(dict(DEFAULT_MPC_CONFIG), "C", {"u_min": 0.1, "u_max": 0.9})
    checkpoint = controller.get_model_snapshot()
    controller.close()
    active_digest = checkpoint["identities"]["active_digest"]
    marker = ModelEvidenceRecord(
        evidence_id="mpc:schema-migration:1:defaults",
        kind=EvidenceKind.SCHEMA_INVALIDATION,
        session_id="mpc-schema-migration",
        cook_id=None,
        timestamp_ms=0,
        role_generation=0,
        schema_version=3,
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
    assert payload["evidence"] == {
        "count": 1,
        "audit_count": 2,
        "high_water": [1, "post-migration-gap"],
        "retired_excluded": 1,
    }


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
    assert payload["candidate"] is None
    assert _section(payload, "identities")["candidate_digest"] == _CANDIDATE


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


@pytest.mark.parametrize(
    (
        "expected_status",
        "round_number",
        "wins",
        "completed_horizons",
        "resumed",
        "pending_count",
    ),
    (
        ("warming", 0, 1, (), True, 0),
        ("collecting", 0, 0, (), False, 0),
        ("evaluating", 1, 1, (3, 15), False, 1),
        ("interrupted", 1, 1, (), True, 0),
        ("qualified", 2, 2, _REQUIRED_HORIZONS, False, 0),
        ("activating", 2, 2, _REQUIRED_HORIZONS, False, 0),
        ("active", 2, 2, _REQUIRED_HORIZONS, False, 0),
    ),
)
def test_report_v3_projects_exact_causal_progress_and_lineage_for_every_phase(
    monkeypatch,
    expected_status,
    round_number,
    wins,
    completed_horizons,
    resumed,
    pending_count,
) -> None:
    phase = (
        "activating"
        if expected_status == "activating"
        else "qualified"
        if expected_status in {"qualified", "active"}
        else "evaluating"
    )
    challenger = _stored_challenger(
        phase=phase,
        evaluation_epoch=3,
        evaluation_round=round_number,
        consecutive_wins=wins,
        last_decision_id=(None if round_number == 0 else f"decision-3-{round_number}"),
        last_evidence_id=(None if round_number == 0 else f"round-3-{round_number}"),
        activation_transaction_id=("transaction-3" if phase == "activating" else None),
    )
    pending_origin = {
        "origin_sequence": 81,
        "horizon_steps": 45,
        "role_generation": challenger.incumbent.role_generation,
        "candidate_generation": challenger.candidate.candidate_generation,
        "incumbent_digest": challenger.incumbent.model_digest,
        "candidate_digest": challenger.candidate.model_digest,
    }
    live = {
        "status": expected_status,
        "fit_status": "succeeded",
        "origin": challenger.origin.value,
        "role_generation": challenger.incumbent.role_generation,
        "candidate_generation": challenger.candidate.candidate_generation,
        "candidate_digest": challenger.candidate.model_digest,
        "checkpoint_digest": challenger.incumbent.model_digest,
        "checks": {},
        "activation_phase": (
            "active" if expected_status == "active" else "prepared" if expected_status == "activating" else "aborted"
        ),
        "pending_persistence": False,
        "pending_swap": expected_status == "activating",
        "completed_horizons": completed_horizons,
        "required_horizons": _REQUIRED_HORIZONS,
        "resumed_from_previous_cook": resumed,
        "pending_origins": ([pending_origin] if pending_count else []),
    }
    activation = {
        "phase": live["activation_phase"],
        "incumbent_digest": challenger.incumbent.model_digest,
        "candidate_digest": challenger.candidate.model_digest,
        "role_generation": challenger.incumbent.role_generation,
        "candidate_generation": challenger.candidate.candidate_generation,
        "origin": challenger.origin.value,
        "policy": ActivationPolicy.CAUSAL_AUTO.value,
    }
    checkpoint = {
        "identities": {
            "active_digest": challenger.incumbent.model_digest,
            "active_generation": challenger.incumbent.role_generation,
            "rollback_digest": None,
            "rollback_generation": None,
        },
        "challenger_authority": {
            "challenger_id": challenger.challenger_id,
            "revision": challenger.revision,
        },
    }
    monkeypatch.setattr(report_module, "_validated_checkpoint", lambda value: value)

    payload = build_learning_report(
        (),
        activation_state=activation,
        live_status=live,
        checkpoint=checkpoint,
        challenger_state=challenger,
        calibration_command_high_water=11,
    ).as_dict()

    assert payload["schema_version"] == 3
    assert payload["status"] == expected_status
    lineage = {
        "request_id": challenger.fit_lineage.request_id,
        "parent_incumbent_digest": challenger.incumbent.model_digest,
        "parent_incumbent_generation": challenger.incumbent.role_generation,
        "candidate_generation": challenger.candidate.candidate_generation,
        "fit_corpus_digest": challenger.fit_corpus.corpus_digest,
        "trigger_origin": challenger.origin.value,
        "result_status": "succeeded",
        "candidate_digest": challenger.candidate.model_digest,
    }
    assert payload["candidate"] == {
        "challenger_id": challenger.challenger_id,
        "phase": phase,
        "lineage": lineage,
        "digest": challenger.candidate.model_digest,
        "origin": challenger.origin.value,
        "policy": ActivationPolicy.CAUSAL_AUTO.value,
        "role_generation": challenger.incumbent.role_generation,
        "candidate_generation": challenger.candidate.candidate_generation,
        "parameters": {
            "C_c": 320.0,
            "K_Q": 350.0,
            "T_amb": 20.0,
            "h_amb": 0.5,
            "sigma": 1.4e-9,
            "theta": 65.0,
            "n_delay": 8,
        },
        "parameter_deltas": None,
        "fit_quality": None,
        "identifiability": None,
        "assessment": None,
    }
    assert payload["evaluation"] == {
        "epoch": 3,
        "round": round_number,
        "completed_horizons": list(completed_horizons),
        "required_horizons": list(_REQUIRED_HORIZONS),
        "wins": wins,
        "required_wins": 2,
        "resumed_from_previous_cook": resumed,
        "pending_origins": ([pending_origin] if pending_count else []),
    }
    corpus_slice = challenger.fit_corpus.slices[0]
    assert payload["corpus"] == {
        "digest": challenger.fit_corpus.corpus_digest,
        "revision": 7,
        "fit_partition_digest": challenger.fit_corpus.fit_partition_digest,
        "slices": [
            {
                "segment_id": corpus_slice.segment_id,
                "through_ordinal": 2,
                "prefix_digest": corpus_slice.prefix_digest,
                "segment_content_digest": corpus_slice.segment_content_digest,
                "pre_roll_count": 1,
                "scored_count": 2,
            }
        ],
    }
    assert set(payload) == {
        "schema_version",
        "status",
        "mode",
        "decision_id",
        "evidence",
        "fit",
        "checks",
        "evaluation",
        "corpus",
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
    assert set(_section(payload, "fit")) == {
        "status",
        "request_id",
        "fit_corpus_digest",
        "error",
    }


def test_production_live_terminal_failure_overlays_prior_active_with_exact_reason(
    ds,
) -> None:
    controller = Controller(dict(DEFAULT_MPC_CONFIG), "C", {"u_min": 0.1, "u_max": 0.9})
    controller.terminate_mpc_activation("native solver crashed")

    checkpoint = controller.get_model_snapshot()
    active_digest = checkpoint["identities"]["active_digest"]
    live = controller._grey_learning_runtime.learning_status()
    controller.close()
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
    assert incompatible["status"] == "error"
    assert incompatible["errors"] == ["checkpoint-schema-invalid"]


def test_real_fit_submission_persists_queued_lifecycle_for_restart_report(ds) -> None:
    controller = Controller(dict(DEFAULT_MPC_CONFIG), "C", {"u_min": 0.1, "u_max": 0.9})
    controller.bind_learning_identity("session-submit", "cook-submit", 0)
    incumbent = controller.active_control_pair.descriptor
    request = _fit_request("request-submit", incumbent)

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
                trigger=SimpleNamespace(input_variance=0.03, input_levels=3),
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
    assert fits[0].fit_corpus_digest == request.fit_corpus.corpus_digest
    assert report.as_dict()["fit"]["fit_corpus_digest"] == request.fit_corpus.corpus_digest
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
    request = _fit_request(f"request-{case}", incumbent)
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


@pytest.mark.parametrize("retirement_hook", ["present", "missing"])
def test_real_evaluation_blocker_persists_rejection_context_before_retirement(
    ds,
    retirement_hook: str,
) -> None:
    controller = Controller(dict(DEFAULT_MPC_CONFIG), "C", {"u_min": 0.1, "u_max": 0.9})
    controller.bind_learning_identity("session-evaluation-blocker", "cook-blocked", 0)
    incumbent = controller.active_control_pair.descriptor
    candidate_config = controller.mpc.config
    candidate_digest = grey_config_digest(candidate_config)
    request = _fit_request(
        "blocked-evaluation-request",
        incumbent,
        configuration_digest="d" * 64,
    )
    candidate_descriptor = GreyControlPairDescriptor(
        model_digest=candidate_digest,
        configuration=dict(incumbent.configuration),
        estimator_kind=incumbent.estimator_kind,
        solver_kind=incumbent.solver_kind,
        candidate_generation=1,
        role_generation=1,
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
    durable = _persist_evaluating_challenger(
        incumbent,
        candidate_descriptor,
        request,
    )
    controller._grey_learning_runtime._challenger_state = durable
    controller._grey_learning_runtime._adopt_prepared_checkpoint_lineage(preparation)
    evaluation = SimpleNamespace(
        decision_id="blocked-evaluation-decision",
        accepted=False,
        blockers=("confidence-window",),
        role_generation=0,
        candidate_generation=1,
        incumbent_digest=incumbent.model_digest,
        challenger_digest=candidate_digest,
        completed_origins=(),
        completed_horizons=_REQUIRED_HORIZONS,
    )

    class _Learning:
        handoff = None
        pending_request = None
        evaluation_config = SimpleNamespace(required_horizons=_REQUIRED_HORIZONS)

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
    if retirement_hook == "missing":
        delattr(_Learning, "retire_evaluated_candidate")
    checkpoint = controller.get_model_snapshot()
    assert ControllerModelStore().save("mpc", checkpoint) is True
    controller._grey_learning_runtime._learning = learning
    controller._grey_learning_runtime._grey_evaluation_payload = lambda *_args, **_kwargs: SimpleNamespace()
    if retirement_hook == "missing":
        try:
            with pytest.raises(
                AttributeError,
                match="retire_evaluated_candidate",
            ):
                controller.poll_learning_off_path(
                    live_origin=CandidateOrigin.OPERATOR_CALIBRATION,
                )
        finally:
            controller.close()
        return
    controller.poll_learning_off_path(
        live_origin=CandidateOrigin.OPERATOR_CALIBRATION,
    )
    assert (
        ControllerModelStore().save(
            "mpc",
            controller.get_model_snapshot(),
        )
        is True
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
    assert report.as_dict()["candidate"] is None
    assert any(
        row["kind"] == "candidate_assessment" and row["payload"]["rejection_reasons"] == ["confidence-window"]
        for row in artifact["records"]
    )
    assert artifact["report"] == report.as_dict()
