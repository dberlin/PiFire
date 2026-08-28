"""Production-boundary proof for durable passive MPC online learning."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from threading import Condition
from typing import Any, cast

import pytest

from common import datastore
from common.control_trace import (
    AllocationClampReason,
    AmbientSource,
    AmbientUncertainty,
    ControllerType,
    ModelEvaluationPayload,
    ModelObservationPayload,
    TraceEventKind,
)
from common.controller_model_state import ControllerModelStore
from common.model_evidence import (
    ActivationLifecycleEvidence,
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FitLifecycleEvidence,
    ForecastOriginEvidence,
    SessionSummaryEvidence,
)
from common.persistence.control_trace import read_control_trace_cook, read_control_trace_session
from common.persistence.model_evidence import read_model_activation, read_model_evidence
from controller.applied_output import AppliedOutput, FrameFeedbackDisposition, OutputSource
from controller.grill_sim import MAKGrillSim
from controller.model_learning.activation import ActivationPhase, PreparedActivationRecord
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FrameObservation,
)
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_snapshot import migrate_grey_learning_snapshot
from controller.runtime.control_trace_recorder import ControlTraceRecorder, RETENTION_PERIOD_MS
from controller.runtime.control_trace_session import (
    ControlTraceSession,
    TraceModelAuthority,
    TraceSessionContext,
)
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.runtime.modes.hold_learning import HoldLearningRuntime
from controller.runtime.runner import SyncControllerRunner, ThreadedControllerRunner

_FRAME_SECONDS = 20
_FIT_SAMPLES = 120
_FIRST_EVALUATION_END = 300
_SECOND_EVALUATION_END = 301
_EVALUATION_PUBLICATION_SEQUENCE = 302
_REQUIRED_HORIZONS = {3, 15, 45, 90, 180}
_LOAD_LEVELS = (0.20, 0.55, 0.90)
_LEVEL_DWELL_FRAMES = 8
_U_MAX = 0.90
_SETPOINT_C = 200.0
_CYCLE = {"u_min": 0.1, "u_max": _U_MAX}
_OBSOLETE_V6_SCORES = {"incumbent_innovation_c", "challenger_innovation_c"}
_LOGGER = logging.getLogger(__name__)


class _TestLogger:
    def info(self, message: str) -> None:
        _LOGGER.info(message)

    def warning(self, message: str) -> None:
        _LOGGER.warning(message)

    def error(self, message: str) -> None:
        _LOGGER.error(message)


_TEST_LOGGER = _TestLogger()


class _FrameBoundaryGate:
    """Let the real threaded runner execute exactly one iteration per permit."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._arrivals = 0
        self._permits = 0
        self._closed = False

    def __call__(self, _period_s: float) -> None:
        with self._condition:
            self._arrivals += 1
            self._condition.notify_all()
            released = self._condition.wait_for(
                lambda: self._permits > 0 or self._closed,
                timeout=30.0,
            )
            if not released:
                raise TimeoutError("threaded controller did not receive a frame-boundary permit")
            if self._permits:
                self._permits -= 1

    def wait_until_blocked(self) -> None:
        with self._condition:
            if not self._condition.wait_for(lambda: self._arrivals > 0, timeout=30.0):
                raise TimeoutError("threaded controller did not reach its initial boundary")

    def advance(self) -> None:
        with self._condition:
            target = self._arrivals + 1
            self._permits += 1
            self._condition.notify_all()
            if not self._condition.wait_for(
                lambda: self._arrivals >= target or self._closed,
                timeout=30.0,
            ):
                raise TimeoutError("threaded controller did not complete its frame boundary")

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


def _wait_until(
    predicate: Callable[[], Any],
    *,
    timeout_s: float,
    description: str,
) -> Any:
    """Bound asynchronous production workers without using timing sleeps."""

    deadline = time.monotonic() + timeout_s
    condition = Condition()
    with condition:
        while True:
            result = predicate()
            if result:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                pytest.fail(f"timed out waiting for {description}")
            condition.wait(timeout=min(0.05, remaining))


def _load_for(sequence: int) -> float:
    if sequence >= _FIT_SAMPLES:
        # Evaluation forecasts assume the origin load persists through the
        # horizon; hold it steady so each score measures the models, not a
        # deliberately false future-input assumption.
        return 0.55
    return _LOAD_LEVELS[(sequence // _LEVEL_DWELL_FRAMES) % len(_LOAD_LEVELS)]


def _simulated_frame(plant: MAKGrillSim, sequence: int) -> FrameObservation:
    normalized_load = _load_for(sequence)
    auger_duty = normalized_load * _U_MAX
    for _ in range(_FRAME_SECONDS):
        plant.step(auger_duty, 1.0)
    frame_start_s = sequence * _FRAME_SECONDS
    frame_end_s = frame_start_s + _FRAME_SECONDS
    return FrameObservation(
        frame_start_s=frame_start_s,
        frame_end_s=frame_end_s,
        temp_c=plant.measured(),
        setpoint_c=_SETPOINT_C,
        ambient_c=plant.T_amb,
        requested_q=normalized_load,
        realized_q=normalized_load,
        baseline_q=normalized_load,
        allocated_q=normalized_load,
        requested_auger_duty=auger_duty,
        scheduled_on_s=auger_duty * _FRAME_SECONDS,
        delivered_on_s=auger_duty * _FRAME_SECONDS,
        realized_auger_duty=auger_duty,
        requested_fan_duty=None,
        actual_fan_duty=1.0,
        allocator_revision=2,
        allocation_clamp_reasons=(AllocationClampReason.NONE,),
        result_revision=sequence + 1,
        output_source=OutputSource.CONTROLLER.value,
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
        observation_sequence=sequence,
        probe_source="mak-sim-probe",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.UNMEASURED,
    )


def _drive_frame(
    *,
    plant: MAKGrillSim,
    sequence: int,
    runner: ThreadedControllerRunner,
    gate: _FrameBoundaryGate,
    learning: HoldLearningRuntime,
) -> FrameObservation:
    observation = _simulated_frame(plant, sequence)
    auger_duty = observation.realized_auger_duty
    assert auger_duty is not None
    runner.submit(observation.temp_c)
    learning.submit_completed_observation(
        (int(observation.frame_start_s), int(observation.frame_end_s)),
        observation,
        AppliedOutput(
            ratio=auger_duty,
            requested=observation.requested_auger_duty,
            source=OutputSource.CONTROLLER,
            timestamp=observation.frame_end_s,
            producing_result_revision=observation.result_revision,
            feedback_disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        ),
    )
    gate.advance()
    learning.reconcile_outcomes(observation.frame_end_s + 0.001)
    learning.reconcile_activation()
    learning.drain_activation_events()
    _wait_until(
        lambda: next(
            (
                record
                for record in read_model_evidence(kind=EvidenceKind.SESSION_SUMMARY)
                if record.timestamp_ms == int(observation.frame_end_s * 1_000)
                and isinstance(record.payload, SessionSummaryEvidence)
                and record.payload.accepted_observations == 1
            ),
            None,
        ),
        timeout_s=5.0,
        description=f"durable typed evidence for observation {sequence}",
    )
    return observation


def _legacy_observation(active_digest: str) -> None:
    legacy_payload = {
        "frame_start_ms": 0,
        "frame_end_ms": 20_000,
        "temp_c": 110.0,
        "setpoint_c": 120.0,
        "ambient_c": 20.0,
        "baseline_combustion_load": 0.40,
        "calibration_probe_load": 0.0,
        "requested_combustion_load": 0.40,
        "allocated_combustion_load": 0.40,
        "realized_combustion_load": 0.40,
        "requested_auger_duty": 0.20,
        "scheduled_on_seconds": 8.0,
        "delivered_on_seconds": 8.0,
        "realized_auger_duty": 0.20,
        "allocator_revision": 2,
        "allocation_clamp_reasons": [],
        "observation_sequence": 1,
        "probe_valid": True,
        "probe_source": "legacy-probe",
        "ambient_source": AmbientSource.CONFIGURED.value,
        "ambient_uncertainty": AmbientUncertainty.UNMEASURED.value,
        "calibration_stage": None,
        "calibration_fit": False,
        "eligible": True,
        "rejection_reasons": [],
        "input_variance": 0.03,
        "input_levels": 3,
        "effective_updates": 120,
        "role_generation": 0,
        "model_digest": active_digest,
        "result_revision": 7,
        "output_source": OutputSource.CONTROLLER.value,
        "lid_open": False,
        "safety_inhibited": False,
        "manual_override": False,
        "stale": False,
        "skipped": False,
        "reset": False,
        "continuous": True,
        "payload_type": "model_observation",
        "incumbent_innovation_c": 4.5,
        "challenger_innovation_c": 2.25,
    }
    with datastore.transaction() as connection:
        connection.execute(
            """
            INSERT INTO control_trace(
                ts_ms, session_id, cook_id, controller, event_kind,
                schema_version, payload
            ) VALUES (?, ?, ?, ?, ?, 6, ?)
            """,
            (
                20_000,
                "legacy-v6-session",
                "legacy-v6-cook",
                ControllerType.MPC.value,
                TraceEventKind.MODEL_OBSERVATION.value,
                json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")),
            ),
        )


def _seed_v6_state(config: dict[str, Any]) -> str:
    """Seed the stale prior-cook authority represented by the field diagnostics."""

    source = Controller(config, "C", dict(_CYCLE))
    try:
        raw_snapshot = source.get_model_snapshot()
        assert isinstance(raw_snapshot, dict)
        snapshot: dict[str, Any] = raw_snapshot
        active = source.active_control_pair.descriptor
        active_configuration: dict[str, Any] = dict(active.configuration)
        candidate_settings: dict[str, Any] = dict(source.cfg)
        candidate_settings["theta"] = float(candidate_settings["theta"]) + 25.0
        candidate = source._pair_factory.descriptor(
            source._pair_factory.configured(
                candidate_settings,
                candidate_generation=1,
                role_generation=1,
                model_identified=True,
            )
        )
        candidate_configuration: dict[str, Any] = dict(candidate.configuration)
    finally:
        source.close()
    decision_id = "legacy-undurable-operator-decision"
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=active,
        candidate=candidate,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.OPERATOR_REVIEWED,
        decision_id=decision_id,
    )
    aborted = prepared.transition(ActivationPhase.ABORTED, reason="legacy-interrupted-activation")

    challenger_parameters = dict(snapshot["active"]["parameters"])
    challenger_parameters["theta"] = candidate_configuration["theta"]
    snapshot["challenger"] = {
        "parameters": challenger_parameters,
        "metadata": {
            "rmse": 1.5,
            "samples": _FIT_SAMPLES,
            "band_c": [40.0, 160.0],
            "nfev": 8,
        },
    }
    snapshot["window"] = {
        "session_id": "legacy-v6-session",
        "cook_id": "legacy-v6-cook",
        "first_observation_sequence": 10,
        "last_observation_sequence": 129,
        "configuration_digest": "d" * 64,
        "incumbent_digest": active.model_digest,
        "role_generation": 0,
    }
    snapshot["candidate_pair"] = candidate.to_dict()
    snapshot["evidence"]["confidence_decision_id"] = None
    snapshot["origin"] = CandidateOrigin.OPERATOR_CALIBRATION.value
    snapshot["policy"] = ActivationPolicy.OPERATOR_REVIEWED.value
    snapshot["identities"]["candidate_digest"] = candidate.model_digest
    snapshot["identities"]["candidate_generation"] = candidate.candidate_generation
    snapshot["activation"] = {
        "phase": ActivationPhase.ABORTED.value,
        "pending_persistence": False,
        "pending_swap": False,
    }
    snapshot["failure"] = None
    canonical = migrate_grey_learning_snapshot(snapshot)
    assert canonical is not None
    assert ControllerModelStore().save("mpc", canonical)

    with datastore.transaction() as connection:
        connection.execute(
            """
            INSERT INTO model_activation_state(
                singleton, active_snapshot_json, rollback_snapshot_json,
                evidence_decision_id, controller_configuration_digest,
                role_generation, phase, transaction_id, incumbent_pair_json,
                candidate_pair_json, rollback_pair_json, origin, policy,
                candidate_generation, candidate_digest, reason
            ) VALUES (1, ?, ?, ?, ?, 0, 'aborted', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                json.dumps(active_configuration, sort_keys=True, separators=(",", ":")),
                json.dumps(active_configuration, sort_keys=True, separators=(",", ":")),
                decision_id,
                candidate.ownership_digest,
                aborted.transaction_id,
                json.dumps(active.to_dict(), sort_keys=True, separators=(",", ":")),
                json.dumps(candidate.to_dict(), sort_keys=True, separators=(",", ":")),
                json.dumps(active.to_dict(), sort_keys=True, separators=(",", ":")),
                CandidateOrigin.OPERATOR_CALIBRATION.value,
                ActivationPolicy.OPERATOR_REVIEWED.value,
                candidate.candidate_generation,
                candidate.model_digest,
                aborted.reason,
            ),
        )
    _legacy_observation(active.model_digest)
    return aborted.transaction_id


def _trace_context(snapshot: dict[str, Any], config: dict[str, Any], cook_id: str) -> TraceSessionContext:
    return TraceSessionContext(
        controller=ControllerType.MPC,
        controller_config=config,
        temperature_unit="C",
        control_period_seconds=float(config["control_period"]),
        fallback_model=TraceModelAuthority(snapshot, "runner"),
        runner_snapshot_fallback_safe=True,
        pulse_slot_seconds=1.0,
        pulse_frame_seconds=float(_FRAME_SECONDS),
        fan_authority=False,
        fan_pwm_capable=False,
        fan_min_duty=0.0,
        fan_max_duty=1.0,
        setpoint=_SETPOINT_C,
        ambient_temperature=MAKGrillSim.AMBIENT_C,
        software_version="e2e",
        build_version="e2e",
        cook_id=cook_id,
        runner_generation=0,
    )


def _identity(snapshot: dict[str, Any]) -> tuple[str, int]:
    identities = snapshot["identities"]
    assert isinstance(identities, dict)
    digest = identities["active_digest"]
    generation = identities["active_generation"]
    assert isinstance(digest, str)
    assert isinstance(generation, int)
    return digest, generation


@pytest.mark.parametrize(
    "database_state",
    ("fresh-v7", "upgraded-v6"),
    ids=("fresh-v7", "upgraded-v6"),
)
def test_passive_online_learning_crosses_trace_persistence_activation_and_restart(
    ds,
    database_state: str,
) -> None:
    config: dict[str, Any] = dict(DEFAULT_MPC_CONFIG)
    config["enable_online_adaptation"] = True
    stale_transaction_id = _seed_v6_state(config) if database_state == "upgraded-v6" else None
    cook_id = f"mpc-online-learning-{database_state}"

    gate = _FrameBoundaryGate()
    core = Controller(config, "C", dict(_CYCLE))
    runner = ThreadedControllerRunner(
        core,
        controller_type=ControllerType.MPC,
        wait_for_period=gate,
    )
    gate.wait_until_blocked()
    model_store = ControllerModelStore()
    persistence = ModelPersistenceWorker(model_store, _TEST_LOGGER)
    recorder = ControlTraceRecorder(
        monotonic_clock=lambda: 0,
        wall_clock=lambda: RETENTION_PERIOD_MS,
    )
    trace = ControlTraceSession(recorder, warning=_TEST_LOGGER.warning)
    learning = HoldLearningRuntime(
        runner=runner,
        model_store=model_store,
        persistence=persistence,
        trace=trace,
        controller_name="mpc",
        logger=_TEST_LOGGER,
        initial_generation=0,
    )
    learned_snapshot: dict[str, Any] | None = None
    initial_snapshot: dict[str, Any] | None = None
    before_active_boundary: dict[str, Any] | None = None
    durable_active = None
    try:
        learning.restore_model(timestamp_ms=0)
        learning.reconcile_activation()
        gate.advance()
        learning.reconcile_outcomes(0.001)

        restored_or_configured = runner.get_model_snapshot()
        assert isinstance(restored_or_configured, dict)
        identity = trace.ensure_open(
            _trace_context(restored_or_configured, config, cook_id),
            timestamp_ms=0,
        )
        assert identity is not None
        learning.bind_generation(0)
        runner.set_target(_SETPOINT_C)
        runner.submit(MAKGrillSim.AMBIENT_C)
        gate.advance()
        initial_snapshot = runner.get_model_snapshot()
        assert isinstance(initial_snapshot, dict)
        initial_digest, initial_generation = _identity(initial_snapshot)
        assert initial_generation == 0

        plant = MAKGrillSim(seed=7)
        for sequence in range(_FIT_SAMPLES):
            _drive_frame(
                plant=plant,
                sequence=sequence,
                runner=runner,
                gate=gate,
                learning=learning,
            )

        _wait_until(
            lambda: (
                state
                if (state := core.get_learning_diagnostics().state).get("status") == "evaluating"
                and state.get("fit_status") == "succeeded"
                else None
            ),
            timeout_s=90.0,
            description="the real grey fit and candidate preparation",
        )

        for sequence in range(_FIT_SAMPLES, _FIRST_EVALUATION_END + 1):
            _drive_frame(
                plant=plant,
                sequence=sequence,
                runner=runner,
                gate=gate,
                learning=learning,
            )

        first_confidence = _wait_until(
            lambda: next(
                (
                    record
                    for record in read_model_evidence(kind=EvidenceKind.CONFIDENCE_DECISION)
                    if isinstance(record.payload, ConfidenceDecisionEvidence)
                    and record.payload.blocked
                    and record.payload.reason == "confidence-rejected"
                ),
                None,
            ),
            timeout_s=30.0,
            description="the first winning causal confidence window",
        )

        _drive_frame(
            plant=plant,
            sequence=_SECOND_EVALUATION_END,
            runner=runner,
            gate=gate,
            learning=learning,
        )
        second_confidence = _wait_until(
            lambda: next(
                (
                    record
                    for record in read_model_evidence(kind=EvidenceKind.CONFIDENCE_DECISION)
                    if isinstance(record.payload, ConfidenceDecisionEvidence)
                    and not record.payload.blocked
                    and record.payload.decision_id != first_confidence.payload.decision_id
                ),
                None,
            ),
            timeout_s=30.0,
            description="the second winning causal confidence window",
        )
        prepared_state = _wait_until(
            lambda: (
                state
                if (state := read_model_activation()) is not None
                and state.phase == ActivationPhase.PREPARED.value
                and state.transaction_id != stale_transaction_id
                else None
            ),
            timeout_s=30.0,
            description="durable passive activation preparation",
        )
        assert prepared_state.evidence_decision_id == second_confidence.payload.decision_id

        learning.reconcile_activation()
        _drive_frame(
            plant=plant,
            sequence=_EVALUATION_PUBLICATION_SEQUENCE,
            runner=runner,
            gate=gate,
            learning=learning,
        )
        durable_active = _wait_until(
            lambda: (
                state
                if (state := read_model_activation()) is not None
                and state.phase == ActivationPhase.ACTIVE.value
                and state.transaction_id == prepared_state.transaction_id
                else None
            ),
            timeout_s=30.0,
            description="durable active activation phase",
        )

        before_active_boundary = runner.get_model_snapshot()
        assert isinstance(before_active_boundary, dict)
        assert _identity(before_active_boundary) == (initial_digest, initial_generation)
        assert core.activation_output_authorized is False

        learning.reconcile_activation()
        gate.advance()
        learning.reconcile_activation()
        learning.drain_activation_events()
        learned_snapshot = runner.get_model_snapshot()
        assert isinstance(learned_snapshot, dict)
        learned_digest, learned_generation = _identity(learned_snapshot)
        assert learned_generation > initial_generation
        assert learned_digest != initial_digest
        assert core.activation_output_authorized is True
        assert durable_active.role_generation == learned_generation
        assert durable_active.active_pair is not None
        assert durable_active.active_pair.model_digest == learned_digest
        assert learning.submit_online_checkpoint(learned_snapshot)
    finally:
        learning.finish_teardown(generation=0)
        runner.stop()

    assert initial_snapshot is not None
    assert before_active_boundary is not None
    assert learned_snapshot is not None
    assert durable_active is not None
    initial_parameters = cast(
        dict[str, Any],
        cast(dict[str, Any], initial_snapshot["active"])["parameters"],
    )
    learned_active = cast(dict[str, Any], learned_snapshot["active"])
    learned_parameters = cast(dict[str, Any], learned_active["parameters"])
    assert any(
        learned_parameters[name] != initial_parameters[name]
        for name in ("C_c", "K_Q", "theta")
    )

    raw_persisted = ControllerModelStore().load("mpc")
    assert isinstance(raw_persisted, dict)
    persisted: dict[str, Any] = raw_persisted
    assert _identity(persisted) == _identity(learned_snapshot)
    assert cast(dict[str, Any], persisted["active"])["parameters"] == learned_parameters

    cook_trace = read_control_trace_cook(cook_id)
    observation_records = [
        record
        for record in cook_trace
        if record.event_kind is TraceEventKind.MODEL_OBSERVATION
        and isinstance(record.payload, ModelObservationPayload)
    ]
    assert len(observation_records) == _EVALUATION_PUBLICATION_SEQUENCE + 1
    assert all(record.schema_version == 7 for record in observation_records)
    for record in observation_records:
        payload = cast(ModelObservationPayload, record.payload)
        assert payload.eligible
        normalized_load = _load_for(payload.observation_sequence)
        assert payload.requested_combustion_load == pytest.approx(normalized_load)
        assert payload.realized_combustion_load == pytest.approx(normalized_load)
        assert payload.requested_auger_duty == pytest.approx(normalized_load * _U_MAX)
        assert payload.delivered_on_seconds == pytest.approx(normalized_load * _U_MAX * _FRAME_SECONDS)
        assert _OBSOLETE_V6_SCORES.isdisjoint(json.loads(record.to_db_row().payload))
    fit_gate_observation = next(
        cast(ModelObservationPayload, record.payload)
        for record in observation_records
        if cast(ModelObservationPayload, record.payload).observation_sequence == 119
    )
    assert fit_gate_observation.effective_updates == _FIT_SAMPLES
    assert fit_gate_observation.input_levels == 3
    assert fit_gate_observation.input_variance == pytest.approx(0.08166666666666668)

    evaluations = [
        cast(ModelEvaluationPayload, record.payload)
        for record in cook_trace
        if record.event_kind is TraceEventKind.MODEL_EVALUATION
        and isinstance(record.payload, ModelEvaluationPayload)
    ]
    wins = [evaluation.consecutive_wins for evaluation in evaluations]
    assert wins[:2] == [1, 2]
    assert all(earlier < later for earlier, later in zip(wins, wins[1:], strict=False))
    assert all(not evaluation.rejection_reasons for evaluation in evaluations)
    assert all(
        {score.horizon_steps for score in evaluation.horizon_scores} == _REQUIRED_HORIZONS
        and all(
            score.challenger_rmse_c is not None
            and score.incumbent_rmse_c is not None
            and score.challenger_rmse_c < score.incumbent_rmse_c
            for score in evaluation.horizon_scores
        )
        for evaluation in evaluations
    )

    cook_evidence = read_model_evidence(cook_id=cook_id)
    fit_records = [
        record.payload for record in cook_evidence if isinstance(record.payload, FitLifecycleEvidence)
    ]
    assert [record.status for record in fit_records] == ["queued", "succeeded"]
    assert len({record.request_id for record in fit_records}) == 1
    assert all(record.origin == CandidateOrigin.PASSIVE_ONLINE.value for record in fit_records)
    assessments = [
        record.payload for record in cook_evidence if isinstance(record.payload, CandidateAssessmentEvidence)
    ]
    assert len(assessments) >= 2
    assessment_ids = [assessment.decision_id for assessment in assessments]
    evaluation_ids = [evaluation.decision_id for evaluation in evaluations]
    assert evaluation_ids == assessment_ids[: len(evaluation_ids)]
    assert assessments[0].confidence_accepted is False
    assert assessments[0].rejection_reasons == ("confidence-rejected",)
    assert all(assessment.confidence_accepted for assessment in assessments[1:])
    assert all(assessment.rejection_reasons == () for assessment in assessments[1:])
    assert assessments[1].decision_id == second_confidence.payload.decision_id
    assert assessments[1].decision_id == durable_active.evidence_decision_id
    assert all(
        assessment.fit_accepted
        and assessment.identifiability_accepted
        and assessment.native_build == "passed"
        and assessment.native_dry_solve == "passed"
        and assessment.target_timing == "passed"
        for assessment in assessments
    )
    forecast_records = [
        record.payload for record in cook_evidence if isinstance(record.payload, ForecastOriginEvidence)
    ]
    assert {forecast.horizon_steps for forecast in forecast_records} == _REQUIRED_HORIZONS
    assert all(not forecast.calibration_fit for forecast in forecast_records)

    lifecycle = [
        record.payload
        for record in read_model_evidence(kind=EvidenceKind.ACTIVATION_LIFECYCLE)
        if isinstance(record.payload, ActivationLifecycleEvidence)
        and record.payload.decision_id == durable_active.evidence_decision_id
    ]
    assert {record.phase for record in lifecycle} == {"prepared", "active"}
    assert all(record.origin == CandidateOrigin.PASSIVE_ONLINE.value for record in lifecycle)
    assert all(record.policy == ActivationPolicy.PASSIVE_AUTO.value for record in lifecycle)

    if database_state == "upgraded-v6":
        legacy = read_control_trace_session("legacy-v6-session")
        assert len(legacy) == 1
        assert legacy[0].schema_version == 6
        assert _OBSOLETE_V6_SCORES.isdisjoint(json.loads(legacy[0].to_db_row().payload))
        assert not any(
            isinstance(record.payload, ConfidenceDecisionEvidence)
            and record.payload.decision_id == "legacy-undurable-operator-decision"
            for record in read_model_evidence()
        )

    restart_core = Controller(config, "C", dict(_CYCLE))
    restart_runner = SyncControllerRunner(restart_core, controller_type=ControllerType.MPC)
    restart_learning = HoldLearningRuntime(
        runner=restart_runner,
        model_store=ControllerModelStore(),
        persistence=None,
        trace=None,
        controller_name="mpc",
        logger=_TEST_LOGGER,
        initial_generation=0,
    )
    try:
        restart_runner.set_target(_SETPOINT_C)
        restart_learning.restore_model(timestamp_ms=10_000_000)
        restart_learning.reconcile_activation()
        raw_restored = restart_runner.get_model_snapshot()
        assert isinstance(raw_restored, dict)
        restored: dict[str, Any] = raw_restored
        assert _identity(restored) == _identity(learned_snapshot)
        assert cast(dict[str, Any], restored["active"])["parameters"] == learned_parameters
        assert restart_core.activation_output_authorized is True
    finally:
        restart_learning.finish_teardown(generation=0)
        restart_runner.stop()
