"""Independent learned-model closed-loop campaigns on both production simulators.

The plants are advanced only with one-second boolean auger states.  Learning
observations, trajectory rows, persistence receipts, fits, qualification,
activation, and restore all cross their production boundaries.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from math import ceil
from typing import Any

import pytest

from common.control_trace import (
    AllocationClampReason,
    AmbientSource,
    AmbientUncertainty,
    ControllerType,
    ModelObservationPayload,
    TraceEventKind,
)
from common.controller_model_state import ControllerModelStore
from common.learning_trajectory import TrajectoryBreakReason
from common.persistence.control_trace import read_control_trace_cook
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from common.persistence.model_challenger import read_model_challenger
from common.persistence.model_evidence import read_model_activation
from controller.acados import GreyBoxMPCConfig
from controller.applied_output import AppliedOutput, FrameFeedbackDisposition, OutputSource
from controller.grey_box import GreyBoxPredictionAdapter
from controller.grill_sim import GrillSim, MAKGrillSim
from controller.model_learning.contracts import CandidateOrigin, FitRequest, FrameObservation
from controller.model_learning.evaluation import (
    CausalForecastEvaluator,
    EvaluationConfig,
    EvaluationDecision,
    ForecastOrigin,
    evaluate_forecasts,
)
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_model import EstimatorSeed, replay_delay_chain_arrays
from controller.runtime.actuation_delivery import ActuationDeliveryJournal, DeliveredGrillPlatform
from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.runtime.control_trace_session import ControlTraceSession
from controller.runtime.learning_trajectory import LearningTrajectoryRuntime, ModeEntered, ModeExited, ThermalSample
from controller.runtime.model_fitting import GreyFitSuccess, fit_segmented_grey, segmented_corpus_fit_job
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.runtime.modes.hold_learning import HoldLearningRuntime
from controller.runtime.runner import SyncControllerRunner
from tests.e2e._mpc_online_learning_helpers import (
    _CYCLE,
    _FRAME_SECONDS,
    _SETPOINT_C,
    _TEST_LOGGER,
    _U_MAX,
    _step_exact_frame,
    _trace_context,
)
from tests.fakes.grill import FakeGrillPlatform

_TRAINING_SEEDS = tuple(range(5))
_HELD_OUT_SEEDS = tuple(range(5, 10))
_REQUIRED_HORIZONS = (3, 15, 45, 90, 180)
_TRAINING_FRAMES = 120
_FRAME_MS = _FRAME_SECONDS * 1_000
_WALL_OFFSET_MS = 1_800_000_000_000
_PULSE_LEVELS = (4, 10, 16)
_LEVEL_DWELL_FRAMES = 8


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _Timeline:
    monotonic_ms: int

    def wall_ms(self) -> int:
        return _WALL_OFFSET_MS + self.monotonic_ms


class _JournaledPlant:
    """Keep the production delivery journal and simulator on the same 1 s edge."""

    def __init__(self, plant: GrillSim, grill: DeliveredGrillPlatform, timeline: _Timeline) -> None:
        self.plant = plant
        self.grill = grill
        self.timeline = timeline
        self._auger_on = False

    def step(self, auger_on: bool, fan_frac: float, lid_open: bool = False) -> None:
        assert type(auger_on) is bool
        if auger_on != self._auger_on:
            (self.grill.auger_on if auger_on else self.grill.auger_off)()
            self._auger_on = auger_on
        self.plant.step(auger_on=auger_on, fan_frac=fan_frac, lid_open=lid_open)
        self.timeline.monotonic_ms += 1_000

    def finish_frame(self) -> None:
        if self._auger_on:
            self.grill.auger_off()
            self._auger_on = False


def _mode_entered(kind: str, *, at_ms: int, family: str, seed: int) -> ModeEntered:
    physics = _digest("grey-one-zone-erlang-production-v1")
    return ModeEntered(
        effective_mode=kind,
        persisted_mode=kind,
        monotonic_ms=at_ms,
        wall_ms=_WALL_OFFSET_MS + at_ms,
        cook_id=f"{family}-training",
        trajectory_session_id=f"{family}-training-trajectory",
        trace_session_id="",
        recipe_step_id=None,
        units="C",
        settings_revision=1,
        collection_provenance={"origin": "passive-online", "seed": seed},
        configuration_provenance={"controller": "mpc", "campaign": "independent-simulator"},
        cadence_digest=_digest("twenty-second-cadence"),
        model_structure_digest=physics,
        held_physics_digest=physics,
        delay_input_mapping_digest=_digest("normalized-combustion-load"),
        actuation_mapping_digest=_digest("one-second-boolean-pulses-u-max-0.9"),
        scored_fan_regime_digest=_digest("fan-fixed-one"),
        ambient_semantics_digest=_digest("simulator-configured-ambient"),
        source_trace_digest=_digest(f"{family}:seed:{seed}"),
        source_schema_version=1,
        source_row_digest=_digest(f"{family}:seed:{seed}:rows"),
        build_provenance={"suite": "learned-model-closed-loop", "revision": 1},
        auger_duty_ceiling=_U_MAX,
    )


def _thermal_sample(timeline: _Timeline, plant: GrillSim, family: str) -> ThermalSample:
    return ThermalSample(
        monotonic_ms=timeline.monotonic_ms,
        wall_ms=timeline.wall_ms(),
        chamber_temperature=plant.measured(),
        units="C",
        probe_valid=True,
        probe_source=f"{family}-simulator-probe",
        ambient_temperature=plant.T_amb,
        ambient_source=AmbientSource.CONFIGURED.value,
        ambient_uncertainty=0.0,
        settings_revision=1,
        recipe_step_id=None,
    )


def _frame_observation(
    *,
    plant: GrillSim,
    family: str,
    frame_start_ms: int,
    on_seconds: int,
    sequence: int,
    setpoint_c: float = _SETPOINT_C,
    result_revision: int | None = None,
    role_generation: int = 0,
) -> FrameObservation:
    realized_duty = on_seconds / _FRAME_SECONDS
    normalized_load = realized_duty / _U_MAX
    return FrameObservation(
        frame_start_s=frame_start_ms / 1_000,
        frame_end_s=(frame_start_ms + _FRAME_MS) / 1_000,
        temp_c=plant.measured(),
        setpoint_c=setpoint_c,
        ambient_c=plant.T_amb,
        requested_q=normalized_load,
        realized_q=normalized_load,
        baseline_q=normalized_load,
        allocated_q=normalized_load,
        requested_auger_duty=realized_duty,
        scheduled_on_s=float(on_seconds),
        delivered_on_s=float(on_seconds),
        realized_auger_duty=realized_duty,
        requested_fan_duty=None,
        actual_fan_duty=1.0,
        allocator_revision=2,
        allocation_clamp_reasons=(AllocationClampReason.NONE,),
        result_revision=sequence + 1 if result_revision is None else result_revision,
        output_source=OutputSource.CONTROLLER.value,
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=role_generation,
        observation_sequence=sequence,
        probe_source=f"{family}-simulator-probe",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.UNMEASURED,
    )


@dataclass(slots=True)
class _TrainingCorpus:
    family: str
    repository: LearningTrajectoryRepository
    persistence: ModelPersistenceWorker
    trajectory: LearningTrajectoryRuntime
    learning: HoldLearningRuntime
    runner: SyncControllerRunner
    core: Controller
    partition: dict[str, str | None]
    trace: ControlTraceSession
    final_time_ms: int
    next_sequence: int


def _collect_training_corpus(plant_type: type[GrillSim], family: str) -> _TrainingCorpus:
    repository = LearningTrajectoryRepository()
    model_store = ControllerModelStore()
    persistence = ModelPersistenceWorker(model_store, _TEST_LOGGER, trajectory_repository=repository)
    timeline = _Timeline(1_000_000)
    journal = ActuationDeliveryJournal(
        monotonic_clock=lambda: timeline.monotonic_ms,
        wall_clock=timeline.wall_ms,
    )
    grill = DeliveredGrillPlatform(
        FakeGrillPlatform(dc_fan=True),
        journal=journal,
        readback_authoritative=True,
    )
    segment_ids = iter(f"{family}-training-seed-{seed}" for seed in _TRAINING_SEEDS)
    trajectory = LearningTrajectoryRuntime(
        journal=journal,
        persistence=persistence,
        segment_id_factory=lambda: next(segment_ids),
        trajectory_session_id_factory=lambda: f"{family}-training-trajectory",
    )
    partition: dict[str, str | None] = {"digest": None}
    config: dict[str, Any] = dict(DEFAULT_MPC_CONFIG)
    config.update({"enable_online_adaptation": True, "control_period": float(_FRAME_SECONDS)})
    core = Controller(
        config,
        "C",
        dict(_CYCLE),
        activation_persistence=persistence,
        trajectory_repository=repository,
        fit_partition_digest=lambda: partition["digest"],
        installation_identity_provider=lambda: "closed-loop-source-installation",
    )
    runner = SyncControllerRunner(
        core,
        controller_type=ControllerType.MPC,
        model_persistence=persistence,
    )
    recorder = ControlTraceRecorder(
        monotonic_clock=lambda: timeline.monotonic_ms,
        wall_clock=timeline.wall_ms,
    )
    trace = ControlTraceSession(recorder, warning=_TEST_LOGGER.warning)
    initial_snapshot = runner.get_model_snapshot()
    assert isinstance(initial_snapshot, dict)
    identity = trace.ensure_open(
        _trace_context(initial_snapshot, config, f"{family}-training", ambient_c=20.0),
        timestamp_ms=timeline.monotonic_ms,
    )
    assert identity is not None
    assert trajectory.bind_trace_session(
        identity.session_id,
        f"{family}-training",
        trace.trajectory_segment_publisher(identity),
    )
    learning = HoldLearningRuntime(
        runner=runner,
        model_store=model_store,
        persistence=persistence,
        trajectory_repository=repository,
        trace=trace,
        controller_name="mpc",
        logger=_TEST_LOGGER,
        initial_generation=0,
        learning_trajectory=trajectory,
    )
    learning.bind_generation(0)
    runner.set_target(_SETPOINT_C)
    grill.fan_on()
    grill.auger_off()
    sequence = 0

    for seed in _TRAINING_SEEDS:
        plant = plant_type(seed=seed)
        journaled = _JournaledPlant(plant, grill, timeline)
        trajectory.mode_entered(_mode_entered("Hold", at_ms=timeline.monotonic_ms, family=family, seed=seed))
        assert trajectory.bind_trace_session(
            identity.session_id,
            f"{family}-training",
            trace.trajectory_segment_publisher(identity),
        )
        trajectory.observe_temperature(_thermal_sample(timeline, plant, family))
        for frame_index in range(_TRAINING_FRAMES):
            frame_start_ms = timeline.monotonic_ms
            on_seconds = _PULSE_LEVELS[(frame_index // _LEVEL_DWELL_FRAMES) % len(_PULSE_LEVELS)]
            delivered = _step_exact_frame(journaled, on_seconds=on_seconds, fan_frac=1.0)
            journaled.finish_frame()
            assert delivered == on_seconds
            trajectory.observe_temperature(_thermal_sample(timeline, plant, family))
            observation = _frame_observation(
                plant=plant,
                family=family,
                frame_start_ms=frame_start_ms,
                on_seconds=on_seconds,
                sequence=sequence,
            )
            runner.submit(observation.temp_c)
            corpus_result = runner.latest()
            assert corpus_result.revision == observation.result_revision
            learning.submit_completed_observation(
                (frame_start_ms // 1_000, timeline.monotonic_ms // 1_000),
                observation,
                AppliedOutput(
                    ratio=on_seconds / _FRAME_SECONDS,
                    requested=on_seconds / _FRAME_SECONDS,
                    source=OutputSource.CONTROLLER,
                    timestamp=observation.frame_end_s,
                    producing_result_revision=observation.result_revision,
                    feedback_disposition=FrameFeedbackDisposition.COMPLETE,
                    sample_complete=True,
                ),
            )
            learning.reconcile_outcomes(observation.frame_end_s + 0.001)
            learning.reconcile_activation()
            learning.drain_activation_events()
            assert trajectory.barrier(timeout=30.0), trajectory.status()
            sequence += 1
        trajectory.mode_exited(
            ModeExited(
                effective_mode="Hold",
                next_effective_mode="Stop",
                monotonic_ms=timeline.monotonic_ms,
                wall_ms=timeline.wall_ms(),
                reason=TrajectoryBreakReason.STOP,
            )
        )
        if not trajectory.barrier(timeout=30.0):
            trace.flush_pending()
            observations = [
                record.payload
                for record in read_control_trace_cook(f"{family}-training")
                if record.event_kind is TraceEventKind.MODEL_OBSERVATION
                and isinstance(record.payload, ModelObservationPayload)
            ]
            observed = [
                (item.observation_sequence, item.eligible, item.rejection_reasons) for item in observations[:12]
            ]
            pytest.fail(f"trajectory failed: {trajectory.status()!r}; observations={observed!r}")

    segments = tuple(repository.read_cook_segments(f"{family}-training"))
    assert len(segments) == len(_TRAINING_SEEDS)
    assert all(segment.terminal_break_reason is TrajectoryBreakReason.STOP for segment in segments)
    assert all(not segment.pre_roll_frames for segment in segments)
    assert all(len(segment.scored_hold_frames) == _TRAINING_FRAMES for segment in segments)
    digests = {segment.fit_partition_digest for segment in segments}
    assert len(digests) == 1
    partition["digest"] = digests.pop()
    return _TrainingCorpus(
        family=family,
        repository=repository,
        persistence=persistence,
        trajectory=trajectory,
        learning=learning,
        runner=runner,
        core=core,
        partition=partition,
        trace=trace,
        final_time_ms=timeline.monotonic_ms,
        next_sequence=sequence,
    )


def _close_training(corpus: _TrainingCorpus) -> None:
    corpus.learning.finish_teardown(generation=0)
    corpus.runner.stop()
    corpus.trace.close()
    corpus.trajectory.close()


def _fit_training_corpus(corpus: _TrainingCorpus) -> GreyFitSuccess:
    snapshot = corpus.repository.snapshot_fit_corpus(corpus.partition["digest"] or "")
    request = FitRequest(
        request_id=f"{corpus.family}-five-seed-fit",
        origin=CandidateOrigin.PASSIVE_ONLINE,
        fit_corpus=snapshot.identity,
        configuration_digest=_digest(f"{corpus.family}:fit-configuration"),
        parent_incumbent_digest=corpus.core.active_control_pair.descriptor.model_digest,
        parent_incumbent_generation=0,
        candidate_generation=1,
    )
    result = fit_segmented_grey(segmented_corpus_fit_job(snapshot, request, GreyBoxMPCConfig()))
    assert isinstance(result, GreyFitSuccess), result
    return result


def _held_out_prediction_decision(
    plant_type: type[GrillSim],
    family: str,
    fit: GreyFitSuccess,
) -> EvaluationDecision:
    candidate_config: dict[str, Any] = dict(DEFAULT_MPC_CONFIG)
    candidate_config.update(
        {
            "control_period": float(_FRAME_SECONDS),
            "C_c": fit.config.C_c,
            "K_Q": fit.config.K_Q,
            "theta": fit.config.theta,
        }
    )
    incumbent_config: dict[str, Any] = dict(DEFAULT_MPC_CONFIG)
    incumbent_config["control_period"] = float(_FRAME_SECONDS)
    candidate = Controller(candidate_config, "C", dict(_CYCLE))
    incumbent = Controller(incumbent_config, "C", dict(_CYCLE))
    candidate.set_target(_SETPOINT_C)
    incumbent.set_target(_SETPOINT_C)
    candidate_digest = candidate.active_control_pair.descriptor.model_digest
    incumbent_digest = incumbent.active_control_pair.descriptor.model_digest
    completed = []
    try:
        for seed in _HELD_OUT_SEEDS:
            plant = plant_type(seed=seed)
            applied = AppliedOutput(
                ratio=10 / _FRAME_SECONDS,
                requested=10 / _FRAME_SECONDS,
                source=OutputSource.CONTROLLER,
                timestamp=0.0,
            )
            for index in range(30):
                candidate.update(plant.measured())
                incumbent.update(plant.measured())
                on_seconds = _PULSE_LEVELS[(index // _LEVEL_DWELL_FRAMES) % len(_PULSE_LEVELS)]
                _step_exact_frame(plant, on_seconds=on_seconds, fan_frac=1.0)
                applied = AppliedOutput(
                    ratio=on_seconds / _FRAME_SECONDS,
                    requested=on_seconds / _FRAME_SECONDS,
                    source=OutputSource.CONTROLLER,
                    timestamp=(index + 1) * _FRAME_SECONDS,
                )
                candidate.set_output(applied)
                incumbent.set_output(applied)
            candidate.update(plant.measured())
            incumbent.update(plant.measured())
            candidate_adapter = GreyBoxPredictionAdapter.from_estimator(
                candidate.active_control_pair.core.estimator,
                config=candidate.active_control_pair.core.config,
            )
            incumbent_adapter = GreyBoxPredictionAdapter.from_estimator(
                incumbent.active_control_pair.core.estimator,
                config=incumbent.active_control_pair.core.config,
            )
            normalized_load = (10 / _FRAME_SECONDS) / _U_MAX
            candidate_forecast = candidate_adapter.forecast(
                [normalized_load] * max(_REQUIRED_HORIZONS),
                [plant.T_amb] * max(_REQUIRED_HORIZONS),
            )
            incumbent_forecast = incumbent_adapter.forecast(
                [normalized_load] * max(_REQUIRED_HORIZONS),
                [plant.T_amb] * max(_REQUIRED_HORIZONS),
            )
            origin_sequence = seed * 1_000
            evaluator = CausalForecastEvaluator(role_generation=0, candidate_generation=1)
            for horizon in _REQUIRED_HORIZONS:
                evaluator.register(
                    ForecastOrigin(
                        origin_sequence=origin_sequence,
                        origin_time_s=float(origin_sequence * _FRAME_SECONDS),
                        horizon_steps=horizon,
                        role_generation=0,
                        candidate_generation=1,
                        incumbent_digest=incumbent_digest,
                        challenger_digest=candidate_digest,
                        incumbent_prediction_c=float(incumbent_forecast[horizon - 1]),
                        challenger_prediction_c=float(candidate_forecast[horizon - 1]),
                        temperature_band="held-out",
                        phase=f"{family}-seed-{seed}",
                        ambient_source=AmbientSource.CONFIGURED,
                        calibration_fit=False,
                    )
                )
            for offset in range(1, max(_REQUIRED_HORIZONS) + 1):
                frame_start_ms = (origin_sequence + offset - 1) * _FRAME_MS
                _step_exact_frame(plant, on_seconds=10, fan_frac=1.0)
                observation = _frame_observation(
                    plant=plant,
                    family=family,
                    frame_start_ms=frame_start_ms,
                    on_seconds=10,
                    sequence=origin_sequence + offset,
                )
                completed.extend(evaluator.observe(observation))
            assert not evaluator.pending_origins
    finally:
        candidate.close()
        incumbent.close()
    return evaluate_forecasts(
        tuple(completed),
        role_generation=0,
        candidate_generation=1,
        prior_consecutive_wins=1,
        config=EvaluationConfig(),
    )


def test_exact_frame_expands_fractional_duty_to_boolean_seconds() -> None:
    class RecordingPlant:
        def __init__(self) -> None:
            self.auger: list[bool] = []

        def step(self, auger_on: bool, fan_frac: float, lid_open: bool = False) -> None:
            assert type(auger_on) is bool
            self.auger.append(auger_on)

    plant = RecordingPlant()
    delivered = _step_exact_frame(plant, on_seconds=7, fan_frac=1.0, frame_seconds=20)

    assert delivered == 7
    assert plant.auger == [True] * 7 + [False] * 13


@dataclass(frozen=True, slots=True)
class _Qualification:
    active_snapshot: dict[str, Any] | None
    blocker: str | None
    completed_horizons: tuple[int, ...]
    held_out_seeds: tuple[int, ...]


def _attempt_production_qualification(
    corpus: _TrainingCorpus,
    plant_type: type[GrillSim],
    held_out_decision: EvaluationDecision,
) -> _Qualification:
    fit_partition_digest = corpus.partition["digest"]
    assert fit_partition_digest is not None
    corpus.partition["digest"] = None
    learning = HoldLearningRuntime(
        runner=corpus.runner,
        model_store=ControllerModelStore(),
        persistence=corpus.persistence,
        trajectory_repository=corpus.repository,
        trace=corpus.trace,
        controller_name="mpc",
        logger=_TEST_LOGGER,
        initial_generation=0,
        learning_trajectory=None,
    )
    learning.bind_generation(0)
    corpus.learning = learning
    plant = plant_type(seed=_HELD_OUT_SEEDS[0])
    history: list[float] = []

    def estimator_seed(theta: float, n_delay: int) -> EstimatorSeed:
        required = 0 if n_delay == 0 else min(180, ceil((3.0 * theta) / _FRAME_SECONDS))
        selected = history[-required:] if required else []
        if required and len(selected) < required:
            selected = [selected[0] if selected else 0.0] * (required - len(selected)) + selected
        delay_states = replay_delay_chain_arrays(
            (_FRAME_SECONDS,) * len(selected),
            selected,
            theta=theta,
            n_delay=n_delay,
            initial_load=selected[0] if selected else 0.0,
        )
        seed_projection = {"theta": theta, "n_delay": n_delay, "loads": selected}
        return EstimatorSeed(
            delay_states=tuple(float(value) for value in delay_states),
            chamber_temperature_c=plant.measured(),
            disturbance=0.0,
            segment_id=f"{corpus.family}-held-out-seed-5",
            pre_roll_digest=hashlib.sha256(
                json.dumps(seed_projection, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            pre_roll_frame_count=required,
            required_frame_count=required,
            status="exact",
        )

    corpus.runner.bind_estimator_seed_source(estimator_seed)
    sequence = corpus.next_sequence
    frame_start_ms = corpus.final_time_ms

    def drive(on_seconds: int, *, poll_off_path: bool = True) -> None:
        nonlocal sequence, frame_start_ms
        _step_exact_frame(plant, on_seconds=on_seconds, fan_frac=1.0)
        observation = _frame_observation(
            plant=plant,
            family=corpus.family,
            frame_start_ms=frame_start_ms,
            on_seconds=on_seconds,
            sequence=sequence,
        )
        corpus.runner.submit(observation.temp_c)
        held_out_result = corpus.runner.latest()
        assert held_out_result.revision == observation.result_revision
        learning.submit_completed_observation(
            (frame_start_ms // 1_000, (frame_start_ms + _FRAME_MS) // 1_000),
            observation,
            AppliedOutput(
                ratio=on_seconds / _FRAME_SECONDS,
                requested=on_seconds / _FRAME_SECONDS,
                source=OutputSource.CONTROLLER,
                timestamp=observation.frame_end_s,
                producing_result_revision=observation.result_revision,
                feedback_disposition=FrameFeedbackDisposition.COMPLETE,
                sample_complete=True,
            ),
        )
        learning.reconcile_outcomes(observation.frame_end_s + 0.001)
        learning.reconcile_activation()
        learning.drain_activation_events()
        if poll_off_path:
            corpus.core.poll_learning_off_path()
            assert corpus.persistence.barrier(timeout=30.0)
            learning.reconcile_activation()
            learning.drain_activation_events()
            assert corpus.persistence.barrier(timeout=30.0)
        history.append((on_seconds / _FRAME_SECONDS) / _U_MAX)
        sequence += 1
        frame_start_ms += _FRAME_MS

    for index in range(30):
        drive(
            _PULSE_LEVELS[(index // _LEVEL_DWELL_FRAMES) % len(_PULSE_LEVELS)],
            poll_off_path=False,
        )
    challenger = None
    corpus.partition["digest"] = fit_partition_digest
    assert corpus.runner.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
    candidate_ready = False
    for _ in range(300):
        drive(10)
        corpus.core.poll_learning_off_path()
        challenger = read_model_challenger()
        if challenger is not None and challenger.phase == "evaluating":
            candidate_ready = True
            break
        diagnostics = corpus.core.get_learning_diagnostics().state
        if diagnostics.get("failure") is not None:
            break
        time.sleep(0.01)
    if candidate_ready:
        for _ in range(400):
            drive(10)
            challenger = read_model_challenger()
            if challenger is not None and challenger.phase == "activating":
                break
    if held_out_decision.accepted:
        for _ in range(5):
            learning.reconcile_activation()
            corpus.core.advance_activation()
            assert corpus.persistence.barrier(timeout=30.0)
            learning.reconcile_activation()
            if corpus.core.activation_output_authorized:
                break
    diagnostics = corpus.core.get_learning_diagnostics().state
    active = (
        corpus.runner.get_model_snapshot()
        if corpus.core.activation_output_authorized and corpus.core.active_control_pair.descriptor.role_generation > 0
        else None
    )
    blocker = None
    if active is None:
        activation = read_model_activation()
        blocker = json.dumps(
            {
                "diagnostics": {
                    key: diagnostics.get(key)
                    for key in (
                        "status",
                        "fit_status",
                        "activation_phase",
                        "failure",
                        "completed_horizons",
                    )
                },
                "challenger": (
                    None
                    if challenger is None
                    else {
                        "phase": challenger.phase,
                        "round": challenger.evaluation_round,
                        "wins": challenger.consecutive_wins,
                        "retirement_reason": challenger.retirement_reason,
                    }
                ),
                "activation": (
                    None
                    if activation is None
                    else {
                        "phase": activation.phase,
                        "reason": activation.reason,
                        "transaction_id": activation.transaction_id,
                    }
                ),
            },
            sort_keys=True,
            default=str,
        )
    raw_completed = diagnostics.get("completed_horizons", ())
    assert isinstance(raw_completed, tuple | list)
    completed_values: list[int] = []
    for value in raw_completed:
        assert isinstance(value, int)
        completed_values.append(value)
    completed = tuple(completed_values)
    return _Qualification(
        active_snapshot=active if isinstance(active, dict) else None,
        blocker=blocker,
        completed_horizons=completed,
        held_out_seeds=_HELD_OUT_SEEDS,
    )


def _snapshot_identity(snapshot: dict[str, Any]) -> tuple[str, int]:
    identities = snapshot["identities"]
    assert isinstance(identities, dict)
    digest = identities["active_digest"]
    generation = identities["active_generation"]
    assert isinstance(digest, str)
    assert isinstance(generation, int)
    return digest, generation


@dataclass(frozen=True, slots=True)
class _Scenario:
    name: str
    duration_s: int
    initial_target_c: float
    final_target_c: float
    transition_s: int | None = None

    def target_at(self, second: int) -> float:
        return (
            self.final_target_c
            if self.transition_s is not None and second >= self.transition_s
            else self.initial_target_c
        )


@dataclass(frozen=True, slots=True)
class _ClosedLoopMetrics:
    iae_c_seconds: float
    overshoot_c: float
    fuel_on_seconds: int
    maximum_temperature_c: float
    safety_events: int
    ceiling_violations: int
    actuator_violations: int
    stale_authorizations: int
    mismatched_digest_authorizations: int
    persistence_restore_errors: int


_SCENARIOS = (
    _Scenario("steady-225f", 12_600, (225.0 - 32.0) * 5.0 / 9.0, (225.0 - 32.0) * 5.0 / 9.0),
    _Scenario("steady-450f", 12_600, (450.0 - 32.0) * 5.0 / 9.0, (450.0 - 32.0) * 5.0 / 9.0),
    _Scenario(
        "step-225f-to-275f",
        14_400,
        (225.0 - 32.0) * 5.0 / 9.0,
        (275.0 - 32.0) * 5.0 / 9.0,
        7_200,
    ),
)
_SAFETY_CEILING_C = (500.0 - 32.0) * 5.0 / 9.0


def _controller_config(*, learning: bool) -> dict[str, Any]:
    config: dict[str, Any] = dict(DEFAULT_MPC_CONFIG)
    config.update(
        {
            "enable_online_adaptation": learning,
            "control_period": float(_FRAME_SECONDS),
        }
    )
    return config


def _new_restored_runner(
    checkpoint_identity: tuple[str, int],
    *,
    installation_identity: str = "closed-loop-source-installation",
    expect_active: bool = True,
) -> tuple[Controller, SyncControllerRunner, HoldLearningRuntime]:
    model_store = ControllerModelStore()
    persistence = ModelPersistenceWorker(model_store, _TEST_LOGGER)
    core = Controller(
        _controller_config(learning=True),
        "C",
        dict(_CYCLE),
        activation_persistence=persistence,
        installation_identity_provider=lambda: installation_identity,
    )
    runner = SyncControllerRunner(
        core,
        controller_type=ControllerType.MPC,
        model_persistence=persistence,
    )
    learning = HoldLearningRuntime(
        runner=runner,
        model_store=model_store,
        persistence=persistence,
        trace=None,
        controller_name="mpc",
        logger=_TEST_LOGGER,
        initial_generation=0,
    )
    learning.restore_model(timestamp_ms=20_000_000)
    learning.reconcile_activation()
    raw = runner.get_model_snapshot()
    assert isinstance(raw, dict)
    if expect_active:
        assert _snapshot_identity(raw) == checkpoint_identity
    else:
        assert _snapshot_identity(raw) != checkpoint_identity
    assert core.activation_output_authorized
    return core, runner, learning


def _run_closed_loop(
    *,
    plant_type: type[GrillSim],
    seed: int,
    scenario: _Scenario,
    checkpoint_identity: tuple[str, int] | None,
) -> _ClosedLoopMetrics:
    if checkpoint_identity is None:
        core = Controller(_controller_config(learning=False), "C", dict(_CYCLE))
        runner = SyncControllerRunner(core, controller_type=ControllerType.MPC)
        learning = None
        expected_digest = _snapshot_identity(runner.get_model_snapshot())[0]
    else:
        core, runner, learning = _new_restored_runner(checkpoint_identity)
        expected_digest = checkpoint_identity[0]
    runner.set_safety_ceiling_c(_SAFETY_CEILING_C)
    plant = plant_type(seed=seed)
    iae = 0.0
    overshoot = 0.0
    fuel = 0
    maximum = plant.measured()
    safety_events = 0
    ceiling_violations = 0
    actuator_violations = 0
    stale_authorizations = 0
    mismatched_authorizations = 0
    solved_revisions: set[int] = set()
    feedback_revisions: set[int] = set()
    try:
        for frame_start in range(0, scenario.duration_s, _FRAME_SECONDS):
            target = scenario.target_at(frame_start)
            runner.set_target(target)
            result = runner.latest_from(plant.measured())
            assert result.revision not in solved_revisions
            solved_revisions.add(result.revision)
            raw_ratio = float(result.cycle_ratio)
            if not 0.0 <= raw_ratio <= _U_MAX:
                actuator_violations += 1
            ratio = min(max(raw_ratio, 0.0), _U_MAX)
            on_seconds = round(ratio * _FRAME_SECONDS)
            if not 0 <= on_seconds <= round(_U_MAX * _FRAME_SECONDS):
                actuator_violations += 1
            fan = result.fan or {}
            fan_frac = float(fan.get("duty", 100.0)) / 100.0
            raw_snapshot = runner.get_model_snapshot()
            assert isinstance(raw_snapshot, dict)
            active_digest = _snapshot_identity(raw_snapshot)[0]
            if result.stale_state.value != "fresh" and core.activation_output_authorized:
                stale_authorizations += 1
            if active_digest != expected_digest and core.activation_output_authorized:
                mismatched_authorizations += 1
            frame_safety_event = False
            for offset in range(_FRAME_SECONDS):
                second = frame_start + offset
                target = scenario.target_at(second)
                auger_on = offset < on_seconds
                assert type(auger_on) is bool
                plant.step(auger_on=auger_on, fan_frac=fan_frac)
                temperature = plant.measured()
                fuel += int(auger_on)
                iae += abs(temperature - target)
                overshoot = max(overshoot, temperature - target)
                maximum = max(maximum, temperature)
                if temperature > _SAFETY_CEILING_C:
                    ceiling_violations += 1
                    frame_safety_event = True
            assert result.revision not in feedback_revisions
            feedback_revisions.add(result.revision)
            runner.set_output(
                AppliedOutput(
                    ratio=on_seconds / _FRAME_SECONDS,
                    requested=raw_ratio,
                    source=OutputSource.CONTROLLER,
                    timestamp=float(frame_start + _FRAME_SECONDS),
                    producing_result_revision=result.revision,
                    feedback_disposition=FrameFeedbackDisposition.COMPLETE,
                    sample_complete=True,
                )
            )
            safety_events += int(frame_safety_event)
        expected_frames = scenario.duration_s // _FRAME_SECONDS
        assert len(solved_revisions) == expected_frames
        assert feedback_revisions == solved_revisions
    finally:
        if learning is not None:
            learning.finish_teardown(generation=0)
        runner.stop()
    return _ClosedLoopMetrics(
        iae_c_seconds=iae,
        overshoot_c=max(0.0, overshoot),
        fuel_on_seconds=fuel,
        maximum_temperature_c=maximum,
        safety_events=safety_events,
        ceiling_violations=ceiling_violations,
        actuator_violations=actuator_violations,
        stale_authorizations=stale_authorizations,
        mismatched_digest_authorizations=mismatched_authorizations,
        persistence_restore_errors=0,
    )


def _matched_closed_loop_failures(
    plant_type: type[GrillSim],
    checkpoint_identity: tuple[str, int],
) -> list[str]:
    failures: list[str] = []
    for scenario in _SCENARIOS:
        learned = [
            _run_closed_loop(
                plant_type=plant_type,
                seed=seed,
                scenario=scenario,
                checkpoint_identity=checkpoint_identity,
            )
            for seed in _HELD_OUT_SEEDS
        ]
        fallback = [
            _run_closed_loop(
                plant_type=plant_type,
                seed=seed,
                scenario=scenario,
                checkpoint_identity=None,
            )
            for seed in _HELD_OUT_SEEDS
        ]
        for seed, metric, fallback_metric in zip(
            _HELD_OUT_SEEDS,
            learned,
            fallback,
            strict=True,
        ):
            contract_failures = (
                metric.safety_events
                + metric.ceiling_violations
                + metric.actuator_violations
                + metric.stale_authorizations
                + metric.mismatched_digest_authorizations
                + metric.persistence_restore_errors
            )
            if contract_failures:
                failures.append(
                    f"{scenario.name} seed {seed}: {contract_failures} safety/actuator/"
                    "authorization/persistence contract failures"
                )
            if metric.maximum_temperature_c > _SAFETY_CEILING_C:
                failures.append(
                    f"{scenario.name} seed {seed}: maximum {metric.maximum_temperature_c}C "
                    f"exceeded {_SAFETY_CEILING_C}C ceiling"
                )
            if metric.iae_c_seconds > fallback_metric.iae_c_seconds:
                failures.append(
                    f"{scenario.name} seed {seed}: learned IAE {metric.iae_c_seconds} "
                    f"exceeded fallback {fallback_metric.iae_c_seconds}"
                )
            if metric.overshoot_c > fallback_metric.overshoot_c:
                failures.append(
                    f"{scenario.name} seed {seed}: learned overshoot {metric.overshoot_c} "
                    f"exceeded fallback {fallback_metric.overshoot_c}"
                )
            if (
                metric.fuel_on_seconds > fallback_metric.fuel_on_seconds * 1.05
                and metric.iae_c_seconds > fallback_metric.iae_c_seconds * 0.90
            ):
                failures.append(
                    f"{scenario.name} seed {seed}: learned fuel {metric.fuel_on_seconds} "
                    f"exceeded 105% of fallback {fallback_metric.fuel_on_seconds} "
                    "without 10% IAE improvement"
                )
        learned_iae = sum(metric.iae_c_seconds for metric in learned)
        fallback_iae = sum(metric.iae_c_seconds for metric in fallback)
        learned_overshoot = sum(metric.overshoot_c for metric in learned)
        fallback_overshoot = sum(metric.overshoot_c for metric in fallback)
        learned_fuel = sum(metric.fuel_on_seconds for metric in learned)
        fallback_fuel = sum(metric.fuel_on_seconds for metric in fallback)
        if learned_iae > fallback_iae:
            failures.append(f"{scenario.name}: learned IAE {learned_iae} exceeded fallback {fallback_iae}")
        if learned_overshoot > fallback_overshoot:
            failures.append(
                f"{scenario.name}: learned overshoot {learned_overshoot} exceeded fallback {fallback_overshoot}"
            )
        if learned_fuel > fallback_fuel * 1.05 and learned_iae > fallback_iae * 0.90:
            failures.append(
                f"{scenario.name}: learned fuel {learned_fuel} exceeded 105% of fallback "
                f"{fallback_fuel} without 10% IAE improvement"
            )
    return failures


def _transplant_failure(
    *,
    source_family: str,
    plant_type: type[GrillSim],
    checkpoint_identity: tuple[str, int],
) -> str | None:
    initial_challenger = read_model_challenger()
    core, runner, learning = _new_restored_runner(
        checkpoint_identity,
        installation_identity="closed-loop-target-installation",
        expect_active=False,
    )
    try:
        scenario = _SCENARIOS[0]
        plant = plant_type(seed=_HELD_OUT_SEEDS[0])
        runner.set_target(scenario.initial_target_c)
        first = runner.latest_from(plant.measured())
        raw = runner.get_model_snapshot()
        assert isinstance(raw, dict)
        active_digest = _snapshot_identity(raw)[0]
        if core.activation_output_authorized and active_digest == checkpoint_identity[0]:
            return (
                "restored learned checkpoint became output-authorized before passive "
                "current-plant prediction revalidation: "
                f"{source_family} digest {checkpoint_identity[0]} authorized on "
                f"{plant_type.__name__} at result revision {first.revision}"
            )
        result = first
        for sequence, frame_start in enumerate(range(0, scenario.duration_s, _FRAME_SECONDS)):
            if frame_start:
                runner.set_target(scenario.target_at(frame_start))
                result = runner.latest_from(plant.measured())
            ratio = min(max(float(result.cycle_ratio), 0.0), _U_MAX)
            on_seconds = round(ratio * _FRAME_SECONDS)
            fan_frac = float((result.fan or {}).get("duty", 100.0)) / 100.0
            _step_exact_frame(
                plant,
                on_seconds=on_seconds,
                frame_seconds=_FRAME_SECONDS,
                fan_frac=fan_frac,
            )
            observation = _frame_observation(
                plant=plant,
                family=f"transplant-{source_family}-to-{plant_type.__name__}",
                frame_start_ms=20_000_000 + frame_start * 1_000,
                on_seconds=on_seconds,
                sequence=sequence,
                setpoint_c=scenario.target_at(frame_start),
                result_revision=result.revision,
                role_generation=core.active_control_pair.descriptor.role_generation,
            )
            learning.submit_completed_observation(
                (
                    round(observation.frame_start_s),
                    round(observation.frame_end_s),
                ),
                observation,
                AppliedOutput(
                    ratio=on_seconds / _FRAME_SECONDS,
                    requested=float(result.cycle_ratio),
                    source=OutputSource.CONTROLLER,
                    timestamp=observation.frame_end_s,
                    producing_result_revision=result.revision,
                    feedback_disposition=FrameFeedbackDisposition.COMPLETE,
                    sample_complete=True,
                ),
            )
            learning.reconcile_outcomes(observation.frame_end_s + 0.001)
            core.poll_learning_off_path()
            learning.reconcile_activation()
            learning.drain_activation_events()
        state = runner.controller_state()
        learning_state = state.get("learning", {})
        required = tuple(learning_state.get("required_horizons", ()))
        completed = tuple(learning_state.get("completed_horizons", ()))
        if set(required) != set(_REQUIRED_HORIZONS) or (completed and set(completed) != set(_REQUIRED_HORIZONS)):
            return (
                "transplant revalidation did not use the production horizon contract: "
                f"completed={completed!r}, required={required!r}"
            )
        final = runner.get_model_snapshot()
        assert isinstance(final, dict)
        final_digest = _snapshot_identity(final)[0]
        durable = read_model_challenger()
        progressed = (
            durable is not None
            and durable.candidate.model_digest == checkpoint_identity[0]
            and durable.evaluation_round >= 1
            and durable.last_decision_id is not None
            and (
                initial_challenger is None
                or durable.challenger_id != initial_challenger.challenger_id
                or durable.revision > initial_challenger.revision
            )
        )
        organically_rejected = (
            progressed
            and durable is not None
            and durable.phase == "retired"
            and durable.retirement_reason == "evaluation-lost"
        )
        if not progressed:
            return (
                "installation-mismatched checkpoint was discarded instead of retained "
                "as an inert challenger for passive current-plant revalidation"
            )
        if final_digest != checkpoint_identity[0] and not organically_rejected:
            return (
                "passive transplant revalidation completed without either activating "
                "the qualified learned digest or recording a durable organic rejection"
            )
    finally:
        learning.finish_teardown(generation=0)
        runner.stop()


@pytest.mark.parametrize("plant_type,family", [(GrillSim, "grill"), (MAKGrillSim, "mak")])
def test_production_gates_qualify_only_on_strict_held_out_prediction(
    ds,
    plant_type: type[GrillSim],
    family: str,
) -> None:
    corpus = _collect_training_corpus(plant_type, family)
    try:
        snapshot = corpus.repository.snapshot_fit_corpus(corpus.partition["digest"] or "")
        assert len(snapshot.identity.slices) == len(_TRAINING_SEEDS)
        assert sum(item.pre_roll_count for item in snapshot.identity.slices) == 0
        assert sum(item.scored_count for item in snapshot.identity.slices) == len(_TRAINING_SEEDS) * _TRAINING_FRAMES
        assert not set(_TRAINING_SEEDS) & set(_HELD_OUT_SEEDS)
        fit = _fit_training_corpus(corpus)
        prediction = _held_out_prediction_decision(plant_type, family, fit)
        qualification = _attempt_production_qualification(corpus, plant_type, prediction)
        assert {score.horizon_steps for score in prediction.scores} == set(_REQUIRED_HORIZONS)
        assert all(score.sample_count == len(_HELD_OUT_SEEDS) for score in prediction.scores)
        if not prediction.accepted:
            assert prediction.blockers == (
                "challenger-horizon-15",
                "challenger-horizon-45",
                "challenger-horizon-90",
                "challenger-horizon-180",
            )
            assert all(
                score.challenger_rmse_c >= score.incumbent_rmse_c
                for score in prediction.scores
                if score.horizon_steps in {15, 45, 90, 180}
            )
            assert qualification.active_snapshot is None, (
                f"{family} production authorizer accepted a held-out-rejected candidate: "
                f"{qualification.active_snapshot}"
            )
            assert qualification.blocker is not None
            assert set(qualification.completed_horizons) == set(_REQUIRED_HORIZONS)
            assert corpus.core.active_control_pair.descriptor.role_generation == 0
            assert ControllerModelStore().load("mpc") is None
            return
        assert qualification.active_snapshot is not None, (
            f"{family} produced no trusted checkpoint; blocker={qualification.blocker}; "
            f"completed_horizons={qualification.completed_horizons}"
        )
        assert set(qualification.completed_horizons) == set(_REQUIRED_HORIZONS)
        assert qualification.blocker is None
        learned_snapshot = qualification.active_snapshot
        assert corpus.core.activation_output_authorized
        assert corpus.learning.submit_online_checkpoint(learned_snapshot)
        assert corpus.persistence.barrier(timeout=30.0)
        persisted = ControllerModelStore().load_strict("mpc")
        assert isinstance(persisted, dict)
        checkpoint_identity = _snapshot_identity(learned_snapshot)
        assert _snapshot_identity(persisted) == checkpoint_identity
        failures = _matched_closed_loop_failures(plant_type, checkpoint_identity)
        assert not failures, "\n".join(failures)
    finally:
        _close_training(corpus)


def test_transplanted_checkpoint_requires_passive_restore_revalidation(ds) -> None:
    corpus = _collect_training_corpus(MAKGrillSim, "mak")
    try:
        fit = _fit_training_corpus(corpus)
        prediction = _held_out_prediction_decision(MAKGrillSim, "mak", fit)
        assert prediction.accepted
        qualification = _attempt_production_qualification(corpus, MAKGrillSim, prediction)
        assert qualification.active_snapshot is not None, qualification.blocker
        learned_snapshot = qualification.active_snapshot
        assert corpus.learning.submit_online_checkpoint(learned_snapshot)
        assert corpus.persistence.barrier(timeout=30.0)
        checkpoint_identity = _snapshot_identity(learned_snapshot)
        transplant_failure = _transplant_failure(
            source_family="mak",
            plant_type=GrillSim,
            checkpoint_identity=checkpoint_identity,
        )
        assert transplant_failure is None, transplant_failure
    finally:
        _close_training(corpus)
