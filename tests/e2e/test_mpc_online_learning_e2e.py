"""Production-boundary proof for durable passive MPC online learning."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from math import ceil, sqrt
from pathlib import Path
from threading import Condition
from typing import Any, cast

import pytest

from common import datastore
from common.control_trace import (
    TRACE_SCHEMA_VERSION,
    AllocationClampReason,
    AmbientSource,
    AmbientUncertainty,
    ChallengerProgressTracePayload,
    ControllerType,
    ModelObservationPayload,
    TraceEventKind,
)
from common.controller_model_state import ControllerModelStore
from common.learning_trajectory import (
    TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
    FrameDeliveryCertainty,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
    canonical_trajectory_digest,
)
from common.model_evidence import (
    MODEL_EVIDENCE_SCHEMA_VERSION,
    ActivationLifecycleEvidence,
    CandidateAssessmentEvidence,
    ChallengerRoundEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FitLifecycleEvidence,
    ForecastOriginEvidence,
    SessionSummaryEvidence,
)
from common.mpc_learning import MPC_FORECAST_HORIZON_SECONDS, forecast_horizon_spec
from common.persistence.control_trace import read_control_trace_cook, read_control_trace_session
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from common.persistence.model_challenger import (
    ModelChallengerState,
    read_model_challenger,
)
from common.persistence.model_evidence import read_model_activation, read_model_evidence
from controller.applied_output import AppliedOutput, FrameFeedbackDisposition, OutputSource
from controller.grill_sim import MAKGrillSim
from controller.model_learning.activation import (
    ActivationPhase,
    PreparedActivationRecord,
)
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FrameObservation,
)
from controller.model_learning.grey_runtime import GreyLearningProcessOwner
from controller.model_learning.migration import migrate_mpc_learning_authority
from controller.model_learning.report import current_learning_report
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_model import EstimatorSeed, replay_delay_chain_arrays, simulate_grey_box_intervals
from controller.mpc_snapshot import migrate_grey_learning_snapshot
from controller.runtime.control_trace_recorder import RETENTION_PERIOD_MS, ControlTraceRecorder
from controller.runtime.control_trace_session import ControlTraceSession
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.runtime.modes.hold_learning import HoldLearningRuntime
from controller.runtime.runner import SyncControllerRunner, ThreadedControllerRunner
from tests.e2e._mpc_online_learning_helpers import (
    _CYCLE,
    _FIT_SAMPLES,
    _FRAME_SECONDS,
    _LEVEL_DWELL_FRAMES,
    _LOAD_LEVELS,
    _MAK_SUPPORT_PARAMETERS,
    _SETPOINT_C,
    _TEST_LOGGER,
    _U_MAX,
    _mak_grey_corpus_rows,
    _trace_context,
)

_LONGEST_FORECAST_HORIZON = forecast_horizon_spec(max(MPC_FORECAST_HORIZON_SECONDS))
_MAX_FIRST_FORECAST_ORIGIN_SEQUENCE = _FIT_SAMPLES + _LONGEST_FORECAST_HORIZON.observation_frames + 5
_MAX_EVALUATION_PUBLICATION_SEQUENCE = (
    _MAX_FIRST_FORECAST_ORIGIN_SEQUENCE + _LONGEST_FORECAST_HORIZON.observation_frames + 2
)
_REQUIRED_HORIZON_SECONDS = set(MPC_FORECAST_HORIZON_SECONDS)
_OBSOLETE_V6_SCORES = {"incumbent_innovation_c", "challenger_innovation_c"}
_EXACT_V6_FIXTURE = Path(__file__).with_name("fixtures") / "restored_v6_passive_21_140.json"
_EXACT_V6_ROWS_SHA256 = "f9bf8eaa632a68e73586abfd93ef42c07d4bac91a824f4660fcc1af8cd59b0a6"
_EXACT_FOLLOWING_ROWS_SHA256 = "27bb8ed230195e426a88da623a46f05f406188e4dc5ad7fbf6a042c351eff60a"
_EXACT_V6_ACTIVE_DIGEST = "8749cde88b2906c4b8ea59a3ea6247aedef2cef67fcff1a3d4c91dc3e302d0ba"
_EXACT_V6_CANDIDATE_DIGEST = "597586b395cf0678201a19c9b8b10e4bf56b5c2985b5c8cdd4470cf7611cbb57"
_EXACT_PASSIVE_CONFIGURATION_DIGEST = "093cd524af707b4dd5c0ed15e30fdaf3156d0a35d41deaa6459579a47f164c71"
_SUPPORT_MODEL_PARAMETERS = {
    "C_c": 1767.5013593870272,
    "K_Q": 288.2098500448781,
    "T_amb": 20.0,
    "h_amb": 0.5,
    "n_delay": 8,
    "sigma": 1.4e-09,
    "theta": 52.241101540886156,
}
_EXACT_REPLAY_COOK_ID = "mpc-restored-v6-passive-21-140"


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
                timeout=120.0,
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


def _poll_until(
    predicate: Callable[[], Any],
    *,
    timeout_s: float,
) -> Any | None:
    deadline = time.monotonic() + timeout_s
    condition = Condition()
    with condition:
        while True:
            result = predicate()
            if result:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return None
            condition.wait(timeout=min(0.05, remaining))


def _wait_until(
    predicate: Callable[[], Any],
    *,
    timeout_s: float,
    description: str,
) -> Any:
    """Bound asynchronous production workers without using timing sleeps."""

    result = _poll_until(predicate, timeout_s=timeout_s)
    if result is None:
        pytest.fail(f"timed out waiting for {description}")
    return result


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


def _exact_passive_observation(row: dict[str, Any]) -> FrameObservation:
    """Rehydrate one exact v6 frame while removing only calibration authority."""

    return FrameObservation(
        frame_start_s=row["frame_start_ms"] / 1_000.0,
        frame_end_s=row["frame_end_ms"] / 1_000.0,
        temp_c=row["temp_c"],
        setpoint_c=row["setpoint_c"],
        ambient_c=row["ambient_c"],
        requested_q=row["requested_combustion_load"],
        realized_q=row["realized_combustion_load"],
        baseline_q=row["requested_combustion_load"],
        probe_q=0.0,
        allocated_q=row["allocated_combustion_load"],
        requested_auger_duty=row["requested_auger_duty"],
        scheduled_on_s=row["scheduled_on_seconds"],
        delivered_on_s=row["delivered_on_seconds"],
        realized_auger_duty=row["realized_auger_duty"],
        requested_fan_duty=row["requested_fan_duty"],
        actual_fan_duty=row["actual_fan_duty"],
        allocator_revision=row["allocator_revision"],
        allocation_clamp_reasons=(),
        result_revision=row["result_revision"],
        output_source=row["output_source"],
        lid_open=row["lid_open"],
        safety_inhibited=row["safety_inhibited"],
        manual_override=row["manual_override"],
        stale=row["stale"],
        skipped=row["skipped"],
        reset=row["reset"],
        continuous=row["continuous"],
        role_generation=0,
        observation_sequence=row["observation_sequence"],
        probe_valid=True,
        probe_source="chamber",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.UNMEASURED,
        calibration_stage=None,
        calibration_fit=False,
        calibration_command_revision=0,
        calibration_command_action="none",
        calibration_cancellation_reason=None,
        calibration_status="inactive",
        cancellation_command_revision=0,
        cancellation_command_action="none",
        completed_calibration_stages=(),
    )


def _exact_corpus_frame(row: dict[str, Any]) -> LearningTrajectoryFrame:
    frame_start_ms = int(row["frame_start_ms"])
    frame_end_ms = int(row["frame_end_ms"])
    duration_s = (frame_end_ms - frame_start_ms) / 1_000.0
    delivered_on_s = float(row["delivered_on_seconds"])
    return LearningTrajectoryFrame(
        sequence=int(row["observation_sequence"]),
        monotonic_start_ms=frame_start_ms,
        monotonic_end_ms=frame_end_ms,
        wall_start_ms=frame_start_ms,
        wall_end_ms=frame_end_ms,
        chamber_temperature_c=float(row["temp_c"]),
        temperature_sample_monotonic_ms=frame_end_ms,
        temperature_sample_wall_ms=frame_end_ms,
        temperature_sample_age_ms=0,
        temperature_sample_wall_age_ms=0,
        temperature_sample_clock_skew_ms=0,
        source_temperature_units="C",
        settings_revision=0,
        role_generation=0,
        probe_valid=True,
        probe_source="chamber",
        ambient_temperature_c=float(row["ambient_c"]),
        ambient_source=AmbientSource.CONFIGURED.value,
        ambient_uncertainty_c=0.0,
        delivered_auger_on_seconds=delivered_on_s,
        realized_auger_duty=delivered_on_s / duration_s,
        normalized_combustion_load=float(row["realized_combustion_load"]),
        delivered_fan_on_seconds=duration_s,
        fan_duty_integral_seconds=duration_s,
        mean_actual_fan_duty=1.0,
        auger_delivery_certainty=FrameDeliveryCertainty.EXACT,
        fan_delivery_certainty=FrameDeliveryCertainty.EXACT,
        effective_mode="Hold",
        recipe_step_id=None,
        complete=True,
        continuous=True,
        partial=False,
        boundary_reason=None,
    )


def _seed_passive_corpus(
    repository: LearningTrajectoryRepository,
    rows: list[dict[str, Any]],
    *,
    trace_session_id: str,
    source_name: str = "exact-restored-v6-passive-21-140",
    source_cook_id: str = _EXACT_REPLAY_COOK_ID,
    source_trace_digest: str = _EXACT_V6_ROWS_SHA256,
    source_kind: str = "restored-v6-replay",
    support_parameters: Mapping[str, float | int] = _SUPPORT_MODEL_PARAMETERS,
    persist_support_segment: bool = True,
    persist_source_segment: bool = True,
    support_scored_limit: int | None = None,
) -> str:
    scored_frames = tuple(_exact_corpus_frame(row) for row in rows)
    first_scored = scored_frames[0]
    pre_roll_count = first_scored.sequence
    pre_roll_frames = tuple(
        replace(
            first_scored,
            sequence=index,
            monotonic_start_ms=(first_scored.monotonic_start_ms - (pre_roll_count - index) * _FRAME_SECONDS * 1_000),
            monotonic_end_ms=(first_scored.monotonic_start_ms - (pre_roll_count - index - 1) * _FRAME_SECONDS * 1_000),
            wall_start_ms=(first_scored.wall_start_ms - (pre_roll_count - index) * _FRAME_SECONDS * 1_000),
            wall_end_ms=(first_scored.wall_start_ms - (pre_roll_count - index - 1) * _FRAME_SECONDS * 1_000),
            temperature_sample_monotonic_ms=(
                first_scored.monotonic_start_ms - (pre_roll_count - index - 1) * _FRAME_SECONDS * 1_000
            ),
            temperature_sample_wall_ms=(
                first_scored.wall_start_ms - (pre_roll_count - index - 1) * _FRAME_SECONDS * 1_000
            ),
            effective_mode="Smoke",
        )
        for index in range(pre_roll_count)
    )
    first = pre_roll_frames[0]
    last = scored_frames[-1]

    def digest(label: str) -> str:
        return hashlib.sha256(label.encode()).hexdigest()

    segment = LearningTrajectorySegment(
        schema_version=1,
        observation_schema_version=TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
        segment_id=source_name,
        cook_id=source_cook_id,
        trajectory_session_id=f"trajectory-{source_name}",
        trace_session_ids=(trace_session_id,),
        collection_provenance={
            "origin": CandidateOrigin.PASSIVE_ONLINE.value,
            "role_generation": 0,
        },
        configuration_provenance={
            "controller": ControllerType.MPC.value,
            "source": source_kind,
            "pre_roll": "constant-first-observation",
        },
        cadence_digest=digest("cadence-20-seconds-v1"),
        model_structure_digest=digest("grey-one-zone-erlang-v1"),
        held_physics_digest=digest("held-grey-physics-v1"),
        delay_input_mapping_digest=digest("normalized-combustion-load-v1"),
        actuation_mapping_digest=digest("framed-pulse-v1"),
        scored_fan_regime_digest=digest("fixed-fan-v1"),
        ambient_semantics_digest=digest("configured-celsius-v1"),
        pre_roll_frames=pre_roll_frames,
        hold_entry=HoldEntrySample(
            monotonic_ms=first_scored.monotonic_start_ms,
            wall_ms=first_scored.wall_start_ms,
            chamber_temperature_c=first_scored.chamber_temperature_c,
            probe_valid=True,
            probe_source=first_scored.probe_source,
        ),
        scored_hold_frames=scored_frames,
        generation_audit_ranges=(
            {
                "start_sequence": first.sequence,
                "end_sequence": last.sequence,
                "role_generation": 0,
            },
        ),
        start_monotonic_ms=first.monotonic_start_ms,
        end_monotonic_ms=last.monotonic_end_ms,
        start_wall_ms=first.wall_start_ms,
        end_wall_ms=last.wall_end_ms,
        start_sequence=first.sequence,
        end_sequence=last.sequence,
        pre_roll_end_reason=None,
        terminal_break_reason=None,
        state="open",
        source_trace_digest=source_trace_digest,
        source_schema_version=7,
        source_row_digest=digest(f"{source_trace_digest}:constant-first-observation"),
        build_provenance={
            "builder": source_kind,
            "revision": 1,
            "source_rows_sha256": source_trace_digest,
        },
    )

    support_pre_roll_load = (0.2,) * pre_roll_count
    support_scored_load = tuple(
        _LOAD_LEVELS[(index // _LEVEL_DWELL_FRAMES) % len(_LOAD_LEVELS)] for index in range(_FIT_SAMPLES)
    )
    support_delay = replay_delay_chain_arrays(
        (_FRAME_SECONDS,) * pre_roll_count,
        support_pre_roll_load,
        theta=support_parameters["theta"],
        n_delay=int(support_parameters["n_delay"]),
        initial_load=support_pre_roll_load[0],
    )
    support_temperature = simulate_grey_box_intervals(
        (_FRAME_SECONDS,) * _FIT_SAMPLES,
        support_scored_load,
        (support_parameters["T_amb"],) * _FIT_SAMPLES,
        C_c=support_parameters["C_c"],
        h_amb=support_parameters["h_amb"],
        T0=80.0,
        K_Q=support_parameters["K_Q"],
        sigma=support_parameters["sigma"],
        theta=support_parameters["theta"],
        n_delay=int(support_parameters["n_delay"]),
        initial_delay_states=support_delay,
    )
    support_start_ms = first.monotonic_start_ms - 10_000_000

    def support_frame(
        sequence: int,
        load: float,
        temperature_c: float,
        *,
        effective_mode: str,
    ) -> LearningTrajectoryFrame:
        start_ms = support_start_ms + sequence * _FRAME_SECONDS * 1_000
        end_ms = start_ms + _FRAME_SECONDS * 1_000
        return LearningTrajectoryFrame(
            sequence=sequence,
            monotonic_start_ms=start_ms,
            monotonic_end_ms=end_ms,
            wall_start_ms=start_ms,
            wall_end_ms=end_ms,
            chamber_temperature_c=temperature_c,
            temperature_sample_monotonic_ms=end_ms,
            temperature_sample_wall_ms=end_ms,
            temperature_sample_age_ms=0,
            temperature_sample_wall_age_ms=0,
            temperature_sample_clock_skew_ms=0,
            source_temperature_units="C",
            settings_revision=0,
            role_generation=0,
            probe_valid=True,
            probe_source="simulated-grey-support",
            ambient_temperature_c=support_parameters["T_amb"],
            ambient_source=AmbientSource.CONFIGURED.value,
            ambient_uncertainty_c=0.0,
            delivered_auger_on_seconds=load * _FRAME_SECONDS,
            realized_auger_duty=load,
            normalized_combustion_load=load,
            delivered_fan_on_seconds=_FRAME_SECONDS,
            fan_duty_integral_seconds=_FRAME_SECONDS,
            mean_actual_fan_duty=1.0,
            auger_delivery_certainty=FrameDeliveryCertainty.EXACT,
            fan_delivery_certainty=FrameDeliveryCertainty.EXACT,
            effective_mode=effective_mode,
            recipe_step_id=None,
            complete=True,
            continuous=True,
            partial=False,
            boundary_reason=None,
        )

    support_pre_roll = tuple(
        support_frame(index, load, 80.0, effective_mode="Smoke") for index, load in enumerate(support_pre_roll_load)
    )
    support_scored = tuple(
        support_frame(
            pre_roll_count + index,
            load,
            float(support_temperature[index]),
            effective_mode="Hold",
        )
        for index, load in enumerate(support_scored_load)
    )
    retained_support_scored = support_scored if support_scored_limit is None else support_scored[:support_scored_limit]
    if not retained_support_scored:
        raise ValueError("support_scored_limit must retain at least one observation")
    support_first = support_pre_roll[0]
    support_first_scored = support_scored[0]
    support_last = retained_support_scored[-1]
    support_segment = replace(
        segment,
        segment_id="deterministic-grey-support-cook",
        cook_id="deterministic-grey-support-cook",
        trajectory_session_id="trajectory-deterministic-grey-support-cook",
        trace_session_ids=("trace-deterministic-grey-support-cook",),
        collection_provenance={
            "origin": CandidateOrigin.PASSIVE_ONLINE.value,
            "role_generation": 0,
            "source": "deterministic-grey-simulation",
        },
        configuration_provenance={
            "controller": ControllerType.MPC.value,
            "source": "deterministic-grey-simulation",
        },
        pre_roll_frames=support_pre_roll,
        hold_entry=HoldEntrySample(
            monotonic_ms=support_first_scored.monotonic_start_ms,
            wall_ms=support_first_scored.wall_start_ms,
            chamber_temperature_c=80.0,
            probe_valid=True,
            probe_source="simulated-grey-support",
        ),
        scored_hold_frames=retained_support_scored,
        generation_audit_ranges=(
            {
                "start_sequence": support_first.sequence,
                "end_sequence": support_last.sequence,
                "role_generation": 0,
            },
        ),
        start_monotonic_ms=support_first.monotonic_start_ms,
        end_monotonic_ms=support_last.monotonic_end_ms,
        start_wall_ms=support_first.wall_start_ms,
        end_wall_ms=support_last.wall_end_ms,
        start_sequence=support_first.sequence,
        end_sequence=support_last.sequence,
        source_trace_digest=digest("deterministic-grey-support-cook"),
        source_schema_version=TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
        source_row_digest=digest("deterministic-grey-support-cook-rows"),
        build_provenance={
            "builder": "deterministic-grey-simulation",
            "revision": 1,
        },
    )
    assert support_segment.fit_partition_digest == segment.fit_partition_digest
    retained_segments = ()
    if persist_support_segment:
        retained_segments += (support_segment,)
    if persist_source_segment:
        retained_segments += (segment,)
    for value in retained_segments:
        cursor = repository.begin_segment(value)
        finalized = repository.finalize(cursor, TrajectoryBreakReason.STOP)
        assert finalized.segment_id == value.segment_id
    return segment.fit_partition_digest


def _drive_exact_passive_frame(
    *,
    row: dict[str, Any],
    runner: ThreadedControllerRunner,
    gate: _FrameBoundaryGate,
    learning: HoldLearningRuntime,
) -> FrameObservation:
    observation = _exact_passive_observation(row)
    auger_duty = observation.realized_auger_duty
    assert auger_duty is not None
    runner.submit(observation.temp_c)
    learning.submit_completed_observation(
        (row["frame_start_ms"], row["frame_end_ms"]),
        observation,
        AppliedOutput(
            ratio=auger_duty,
            requested=observation.requested_auger_duty,
            source=OutputSource(observation.output_source),
            timestamp=observation.frame_end_s,
            producing_result_revision=observation.result_revision,
            feedback_disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        ),
    )
    gate.advance()
    learning.reconcile_outcomes(observation.frame_end_s)
    learning.reconcile_activation()
    learning.drain_activation_events()
    return observation


def _seed_exact_v6_checkpoint(fixture: dict[str, Any]) -> dict[str, Any]:
    checkpoint = cast(dict[str, Any], fixture["checkpoint"])
    assert ControllerModelStore().save("mpc", checkpoint)

    provenance = cast(dict[str, Any], fixture["provenance"])
    legacy_payload = cast(dict[str, Any], fixture["legacy_v6_observation"])
    with datastore.transaction() as connection:
        connection.execute(
            """
            INSERT INTO control_trace(
                ts_ms, session_id, cook_id, controller, event_kind,
                schema_version, payload
            ) VALUES (?, ?, ?, ?, ?, 6, ?)
            """,
            (
                legacy_payload["frame_end_ms"],
                provenance["source_session_id"],
                provenance["source_cook_id"],
                ControllerType.MPC.value,
                TraceEventKind.MODEL_OBSERVATION.value,
                json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")),
            ),
        )
    return checkpoint


def _durable_candidate_lineage(
    challenger: ModelChallengerState,
) -> dict[str, Any]:
    return {
        "origin": challenger.origin.value,
        "policy": challenger.policy.value,
        "candidate_digest": challenger.candidate.model_digest,
        "candidate_generation": challenger.candidate.candidate_generation,
    }


def _snapshot_candidate_lineage(
    snapshot: dict[str, Any],
    challenger: ModelChallengerState,
) -> dict[str, Any]:
    assert snapshot["challenger_authority"] == {
        "challenger_id": challenger.challenger_id,
        "revision": challenger.revision,
    }
    return _durable_candidate_lineage(challenger)


def _report_candidate_lineage(
    report: dict[str, Any],
    challenger: ModelChallengerState,
) -> dict[str, Any]:
    candidate = cast(dict[str, Any], report["candidate"])
    projected = {
        "origin": candidate["origin"],
        "policy": candidate["policy"],
        "candidate_digest": candidate["digest"],
        "candidate_generation": candidate["candidate_generation"],
    }
    assert projected == _durable_candidate_lineage(challenger)
    return projected


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
        policy=ActivationPolicy.CAUSAL_AUTO,
        decision_id=decision_id,
    )
    aborted = prepared.transition(ActivationPhase.ABORTED, reason="legacy-interrupted-activation")
    snapshot["version"] = 4
    snapshot["schema"] = "pifire-grey-learning/v4"
    snapshot.pop("challenger_authority")

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
    snapshot["policy"] = "operator-reviewed"
    snapshot["identities"]["candidate_digest"] = candidate.model_digest
    snapshot["identities"]["candidate_generation"] = candidate.candidate_generation
    snapshot["activation"] = {
        "phase": ActivationPhase.ABORTED.value,
        "pending_persistence": False,
        "pending_swap": False,
    }
    snapshot["failure"] = None
    assert ControllerModelStore().save("mpc", snapshot)

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
                "operator-reviewed",
                candidate.candidate_generation,
                candidate.model_digest,
                aborted.reason,
            ),
        )
    _legacy_observation(active.model_digest)
    return aborted.transaction_id


def _identity(snapshot: dict[str, Any]) -> tuple[str, int]:
    identities = snapshot["identities"]
    assert isinstance(identities, dict)
    digest = identities["active_digest"]
    generation = identities["active_generation"]
    assert isinstance(digest, str)
    assert isinstance(generation, int)
    return digest, generation


@pytest.mark.slow
@pytest.mark.parametrize(
    "database_state",
    ("fresh-v7", "upgraded-v6"),
    ids=("fresh-v7", "upgraded-v6"),
)
def test_passive_online_learning_rejects_absolute_rmse_failure_and_restart_remains_inactive(
    ds,
    database_state: str,
) -> None:
    config: dict[str, Any] = dict(DEFAULT_MPC_CONFIG)
    config["enable_online_adaptation"] = True
    if database_state == "upgraded-v6":
        _seed_v6_state(config)
    if database_state == "upgraded-v6":
        migration = migrate_mpc_learning_authority(defaults=config)
        assert migration.snapshot["version"] == 7
    cook_id = f"mpc-online-learning-{database_state}"

    cumulative_rows = _mak_grey_corpus_rows()
    cumulative_rows_digest = hashlib.sha256(
        json.dumps(
            cumulative_rows,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    repository = LearningTrajectoryRepository()
    fit_partition_digest = _seed_passive_corpus(
        repository,
        cumulative_rows,
        trace_session_id=f"seeded-cumulative-{database_state}",
        source_name=f"simulated-mak-passive-{database_state}",
        source_cook_id=f"simulated-mak-passive-{database_state}",
        source_trace_digest=cumulative_rows_digest,
        source_kind="deterministic-mak-simulation",
        support_parameters=_MAK_SUPPORT_PARAMETERS,
        persist_source_segment=False,
        support_scored_limit=1,
    )
    model_store = ControllerModelStore()
    persistence = ModelPersistenceWorker(
        model_store,
        _TEST_LOGGER,
        trajectory_repository=repository,
    )
    process_owner = GreyLearningProcessOwner()
    gate = _FrameBoundaryGate()
    core = Controller(
        config,
        "C",
        dict(_CYCLE),
        activation_persistence=persistence,
        trajectory_repository=repository,
        fit_partition_digest=lambda: fit_partition_digest,
        grey_learning_process=process_owner,
    )
    runner = ThreadedControllerRunner(
        core,
        controller_type=ControllerType.MPC,
        wait_for_period=gate,
    )
    gate.wait_until_blocked()
    recorder = ControlTraceRecorder(
        monotonic_clock=lambda: 0,
        wall_clock=lambda: RETENTION_PERIOD_MS,
    )
    trace = ControlTraceSession(recorder, warning=_TEST_LOGGER.warning)
    learning = HoldLearningRuntime(
        runner=runner,
        model_store=model_store,
        persistence=persistence,
        trajectory_repository=repository,
        trace=trace,
        controller_name="mpc",
        logger=_TEST_LOGGER,
        initial_generation=0,
    )
    initial_snapshot: dict[str, Any] | None = None
    fit_challenger: ModelChallengerState | None = None
    rejected_round: ChallengerRoundEvidence | None = None
    rejected_assessment: CandidateAssessmentEvidence | None = None
    rejection_sequence: int | None = None
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

        def candidate_seed(theta: float, n_delay: int) -> EstimatorSeed:
            loads = tuple(_load_for(sequence) for sequence in range(_MAX_EVALUATION_PUBLICATION_SEQUENCE + 1))
            required = 0 if n_delay == 0 else min(180, ceil((3.0 * theta) / _FRAME_SECONDS))
            selected = loads[-required:] if required else ()
            delay_states = replay_delay_chain_arrays(
                (_FRAME_SECONDS,) * len(selected),
                selected,
                theta=theta,
                n_delay=n_delay,
                initial_load=selected[0] if selected else 0.0,
            )
            seed_projection = {
                "theta": theta,
                "n_delay": n_delay,
                "loads": selected,
            }
            seed = EstimatorSeed(
                delay_states=tuple(float(value) for value in delay_states),
                chamber_temperature_c=plant.measured(),
                disturbance=0.0,
                segment_id=f"simulated-mak-passive-{database_state}",
                pre_roll_digest=hashlib.sha256(
                    json.dumps(
                        seed_projection,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                pre_roll_frame_count=required,
                required_frame_count=required,
                status="exact",
            )
            return seed

        runner.bind_estimator_seed_source(candidate_seed)
        gate.advance()
        for sequence in range(_FIT_SAMPLES - 1):
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
                if (state := core.get_learning_diagnostics().state).get("status") == "collecting"
                and state.get("fit_status") == "idle"
                and state.get("origin") is None
                and state.get("failure") is None
                else None
            ),
            timeout_s=30.0,
            description="the final non-ready corpus fit intent to retire",
        )
        completed_partition_digest = _seed_passive_corpus(
            repository,
            cumulative_rows,
            trace_session_id=f"seeded-cumulative-{database_state}",
            source_name=f"simulated-mak-passive-{database_state}",
            source_cook_id=f"simulated-mak-passive-{database_state}",
            source_trace_digest=cumulative_rows_digest,
            source_kind="deterministic-mak-simulation",
            support_parameters=_MAK_SUPPORT_PARAMETERS,
            persist_support_segment=False,
        )
        assert completed_partition_digest == fit_partition_digest
        _drive_frame(
            plant=plant,
            sequence=_FIT_SAMPLES - 1,
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
        fit_challenger = cast(
            ModelChallengerState,
            _wait_until(
                lambda: (
                    challenger
                    if (challenger := read_model_challenger()) is not None
                    and challenger.phase == "evaluating"
                    and challenger.evaluation_round == 0
                    else None
                ),
                timeout_s=30.0,
                description="the initial durable challenger",
            ),
        )

        def first_rejected_round() -> ChallengerRoundEvidence | None:
            return next(
                (
                    record.payload
                    for record in read_model_evidence(kind=EvidenceKind.CHALLENGER_ROUND)
                    if isinstance(record.payload, ChallengerRoundEvidence) and not record.payload.accepted
                ),
                None,
            )

        for sequence in range(
            _FIT_SAMPLES,
            _MAX_EVALUATION_PUBLICATION_SEQUENCE,
        ):
            _drive_frame(
                plant=plant,
                sequence=sequence,
                runner=runner,
                gate=gate,
                learning=learning,
            )
            rejected_round = _poll_until(first_rejected_round, timeout_s=0.1)
            if rejected_round is not None:
                rejection_sequence = sequence
                break
        if rejected_round is None or rejection_sequence is None:
            state = core.get_learning_diagnostics().state
            rounds = tuple(
                (
                    record.payload.challenger_id,
                    record.payload.evaluation_round,
                    record.payload.accepted,
                )
                for record in read_model_evidence(kind=EvidenceKind.CHALLENGER_ROUND)
                if isinstance(record.payload, ChallengerRoundEvidence)
            )
            pytest.fail(
                "the deterministic MAK simulation did not publish a terminal causal round; "
                f"challenger={read_model_challenger()!r}; "
                f"completed_horizon_seconds={state.get('completed_horizon_seconds')!r}; "
                f"pending_origins={state.get('pending_origins')!r}; "
                f"rounds={rounds!r}; "
                f"failure={state.get('failure')!r}"
            )

        rejected_assessment = cast(
            CandidateAssessmentEvidence,
            _wait_until(
                lambda: next(
                    (
                        record.payload
                        for record in read_model_evidence(kind=EvidenceKind.CANDIDATE_ASSESSMENT)
                        if isinstance(record.payload, CandidateAssessmentEvidence)
                        and record.payload.decision_id == rejected_round.decision_id
                    ),
                    None,
                ),
                timeout_s=30.0,
                description="the durable absolute-RMSE rejection",
            ),
        )

        assert _identity(cast(dict[str, Any], runner.get_model_snapshot())) == (
            initial_digest,
            initial_generation,
        )
    finally:
        learning.finish_teardown(generation=0)
        runner.stop()
        assert persistence.close(timeout=30.0)
        process_owner.close()

    assert initial_snapshot is not None
    assert fit_challenger is not None
    assert rejected_round is not None
    assert rejected_assessment is not None
    assert rejection_sequence is not None

    expected_absolute_blockers = tuple(
        f"absolute-rmse-{horizon_seconds}" for horizon_seconds in MPC_FORECAST_HORIZON_SECONDS
    )
    assert rejected_round.accepted is False
    assert rejected_round.evaluation_round == 1
    assert rejected_round.required_horizon_seconds == MPC_FORECAST_HORIZON_SECONDS
    assert rejected_round.completed_horizon_seconds == MPC_FORECAST_HORIZON_SECONDS
    assert rejected_assessment.decision_id == rejected_round.decision_id
    assert rejected_assessment.rejection_reasons == expected_absolute_blockers
    assert rejected_assessment.fit_accepted
    assert rejected_assessment.identifiability_accepted
    assert rejected_assessment.native_build == "passed"
    assert rejected_assessment.native_dry_solve == "passed"
    assert rejected_assessment.target_timing == "passed"
    assert rejected_assessment.confidence_accepted is False

    cook_trace = read_control_trace_cook(cook_id)
    observation_records = [
        record
        for record in cook_trace
        if record.event_kind is TraceEventKind.MODEL_OBSERVATION and isinstance(record.payload, ModelObservationPayload)
    ]
    assert observation_records
    assert all(record.schema_version == TRACE_SCHEMA_VERSION for record in observation_records)
    fit_gate_observation = next(
        cast(ModelObservationPayload, record.payload)
        for record in observation_records
        if cast(ModelObservationPayload, record.payload).observation_sequence == _FIT_SAMPLES - 1
    )
    assert fit_gate_observation.effective_updates == _FIT_SAMPLES
    assert all(_OBSOLETE_V6_SCORES.isdisjoint(json.loads(record.to_db_row().payload)) for record in observation_records)

    cook_evidence = read_model_evidence(cook_id=cook_id)
    assert cook_evidence
    assert all(record.schema_version == MODEL_EVIDENCE_SCHEMA_VERSION for record in cook_evidence)
    forecast_records = [
        record.payload for record in cook_evidence if isinstance(record.payload, ForecastOriginEvidence)
    ]
    assert {forecast.horizon_seconds for forecast in forecast_records} == _REQUIRED_HORIZON_SECONDS
    assert all(
        (
            forecast.prediction_steps,
            forecast.observation_frames,
        )
        == (
            forecast_horizon_spec(forecast.horizon_seconds).prediction_steps,
            forecast_horizon_spec(forecast.horizon_seconds).observation_frames,
        )
        and not forecast.calibration_fit
        for forecast in forecast_records
    )
    rejected_forecasts = [
        forecast for forecast in forecast_records if forecast.challenger_digest == rejected_round.candidate_digest
    ]
    assert {forecast.horizon_seconds for forecast in rejected_forecasts} == _REQUIRED_HORIZON_SECONDS
    for horizon_seconds in MPC_FORECAST_HORIZON_SECONDS:
        horizon_forecasts = [forecast for forecast in rejected_forecasts if forecast.horizon_seconds == horizon_seconds]
        challenger_rmse_c = sqrt(
            sum(forecast.challenger_error_c**2 for forecast in horizon_forecasts) / len(horizon_forecasts)
        )
        assert challenger_rmse_c > forecast_horizon_spec(horizon_seconds).maximum_rmse_c
    assert not [
        record
        for record in read_model_evidence(kind=EvidenceKind.ACTIVATION_LIFECYCLE)
        if isinstance(record.payload, ActivationLifecycleEvidence)
        and record.payload.decision_id == rejected_round.decision_id
    ]

    progress = [
        cast(ChallengerProgressTracePayload, record.payload)
        for record in cook_trace
        if record.event_kind is TraceEventKind.CHALLENGER_PROGRESS
        and isinstance(record.payload, ChallengerProgressTracePayload)
        and record.payload.challenger_id == rejected_round.challenger_id
    ]
    assert progress
    assert (progress[0].phase, progress[0].evaluation_round, progress[0].consecutive_wins) == (
        "evaluating",
        0,
        0,
    )
    assert (
        progress[-1].phase,
        progress[-1].evaluation_round,
        progress[-1].consecutive_wins,
        progress[-1].reset_reason,
    ) == ("retired", 1, 0, "evaluation-lost")

    activation_state = read_model_activation()
    assert activation_state is None or activation_state.phase != ActivationPhase.ACTIVE.value
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
        restored = restart_runner.get_model_snapshot()
        assert isinstance(restored, dict)
        assert _identity(restored) == _identity(initial_snapshot)
    finally:
        restart_learning.finish_teardown(generation=0)
        restart_runner.stop()


def test_restored_v6_checkpoint_rebinds_exact_passive_candidate_provenance(ds) -> None:
    fixture = cast(
        dict[str, Any],
        json.loads(_EXACT_V6_FIXTURE.read_text(encoding="utf-8")),
    )
    provenance = cast(dict[str, Any], fixture["provenance"])
    fields = cast(list[str], fixture["frame_fields"])
    encoded_rows = cast(list[list[Any]], fixture["frame_rows"])
    rows = [dict(zip(fields, values, strict=True)) for values in encoded_rows]
    encoded_following_rows = cast(list[list[Any]], fixture["following_frame_rows"])
    following_rows = [dict(zip(fields, values, strict=True)) for values in encoded_following_rows]
    canonical_rows = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    canonical_following_rows = json.dumps(
        following_rows,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    assert fixture["schema"] == "pifire-e2e-restored-v6-passive-replay/v1"
    assert len(fields) == 25
    assert all(len(values) == len(fields) for values in encoded_rows)
    assert all(len(values) == len(fields) for values in encoded_following_rows)
    assert len(rows) == _FIT_SAMPLES
    assert [row["observation_sequence"] for row in rows] == list(range(21, 141))
    assert rows[0]["frame_start_ms"] == 1_787_871_688_267
    assert rows[-1]["frame_end_ms"] == 1_787_874_088_267
    assert len(following_rows) == 1
    assert following_rows[0]["observation_sequence"] == 141
    assert following_rows[0]["frame_start_ms"] == rows[-1]["frame_end_ms"]
    assert following_rows[0]["frame_end_ms"] == 1_787_874_108_267
    assert hashlib.sha256(canonical_rows).hexdigest() == fixture["frame_rows_sha256"]
    assert fixture["frame_rows_sha256"] == _EXACT_V6_ROWS_SHA256
    assert hashlib.sha256(canonical_following_rows).hexdigest() == fixture["following_frame_rows_sha256"]
    assert fixture["following_frame_rows_sha256"] == _EXACT_FOLLOWING_ROWS_SHA256
    assert provenance == {
        "cook_archive": {
            "member": "learning_diagnostics.json",
            "name": "2026-08-27--2015-CookFile.pifire",
            "sha256": "e60243b901d0244344954d1e93ed86e449cbee0b40320f7b37f146fbaab981a5",
        },
        "diagnostics_archive": {
            "member": "pifire.db",
            "name": "PiFire_Diagnostics_20260827-202055 (1).zip",
            "sha256": "9c296ca2d70121d9ed62d9950680b24e6dbfcb41ab83894662c41b2ea80fc60d",
        },
        "source_checkpoint_key": "controller_model_state",
        "source_checkpoint_revision": 2,
        "source_cook_id": "0ad52abe-a266-11f1-9074-88a29e57b0e5",
        "source_sequence_range": [21, 140],
        "source_session_id": "0236ccee-7931-4723-a5ba-776773109fa9",
        "source_trace_schema_version": 6,
    }

    source_checkpoint = _seed_exact_v6_checkpoint(fixture)
    source_identities = cast(dict[str, Any], source_checkpoint["identities"])
    source_active = cast(dict[str, Any], source_checkpoint["active"])
    source_challenger = cast(dict[str, Any], source_checkpoint["challenger"])
    source_active_pair = cast(dict[str, Any], source_checkpoint["active_pair"])
    source_candidate_pair = cast(dict[str, Any], source_checkpoint["candidate_pair"])
    assert source_checkpoint["schema"] == "pifire-grey-learning/v4"
    assert source_checkpoint["revision"] == 2
    assert source_checkpoint["origin"] == CandidateOrigin.OPERATOR_CALIBRATION.value
    assert source_checkpoint["policy"] is None
    assert source_checkpoint["activation"] == {
        "pending_persistence": False,
        "pending_swap": False,
        "phase": ActivationPhase.ABORTED.value,
    }
    assert source_identities == {
        "active_digest": _EXACT_V6_ACTIVE_DIGEST,
        "active_generation": 0,
        "candidate_digest": _EXACT_V6_CANDIDATE_DIGEST,
        "candidate_generation": 1,
        "rollback_digest": None,
        "rollback_generation": None,
    }
    assert source_active["parameters"] == {
        "C_c": 320.0,
        "K_Q": 350.0,
        "T_amb": 20.0,
        "h_amb": 0.5,
        "n_delay": 8,
        "sigma": 1.4e-09,
        "theta": 50.0,
    }
    assert source_challenger["parameters"] == {
        "C_c": 1767.4962464102664,
        "K_Q": 288.20982775716607,
        "T_amb": 20.0,
        "h_amb": 0.5,
        "n_delay": 8,
        "sigma": 1.4e-09,
        "theta": 52.24105678450995,
    }
    assert source_active_pair["model_digest"] == _EXACT_V6_ACTIVE_DIGEST
    assert source_active_pair["ownership_digest"] == (
        "f0eb4c8212c0419201e540bf40d2124ec48ad6d9bcd571ff3d8a7ee7cfd9a207"
    )
    assert source_candidate_pair["model_digest"] == _EXACT_V6_CANDIDATE_DIGEST
    assert source_candidate_pair["ownership_digest"] == (
        "7d0bed9087979a5b39de36329ba8e1934572ec3a121042feb6e7f60af83fc5c6"
    )
    assert source_checkpoint["window"] == {
        "configuration_digest": "2f49eeaca6aecc624162d29933246072c34a52ff583773a8449a394982d7ba43",
        "cook_id": provenance["source_cook_id"],
        "first_observation_sequence": 21,
        "incumbent_digest": _EXACT_V6_ACTIVE_DIGEST,
        "last_observation_sequence": 140,
        "role_generation": 0,
        "session_id": provenance["source_session_id"],
    }
    assert ControllerModelStore().load_strict("mpc") == source_checkpoint

    legacy_source = cast(dict[str, Any], fixture["legacy_v6_observation"])
    raw_legacy_payload = json.loads(
        datastore.connection()
        .execute(
            "SELECT payload FROM control_trace WHERE session_id=?",
            (provenance["source_session_id"],),
        )
        .fetchone()[0]
    )
    assert _OBSOLETE_V6_SCORES.issubset(raw_legacy_payload)
    legacy_records = read_control_trace_session(provenance["source_session_id"])
    assert len(legacy_records) == 1
    assert legacy_records[0].schema_version == 6
    assert isinstance(legacy_records[0].payload, ModelObservationPayload)
    expected_migrated_v6 = dict(legacy_source)
    for obsolete_key in _OBSOLETE_V6_SCORES:
        expected_migrated_v6.pop(obsolete_key)
    migrated_v6 = json.loads(legacy_records[0].to_db_row().payload)
    assert migrated_v6 == expected_migrated_v6
    assert _OBSOLETE_V6_SCORES.isdisjoint(migrated_v6)

    config: dict[str, Any] = dict(DEFAULT_MPC_CONFIG)
    config["enable_online_adaptation"] = True
    migration = migrate_mpc_learning_authority(defaults=config)
    assert migration.snapshot["version"] == 7
    assert ControllerModelStore().load_strict("mpc") == migration.snapshot
    first_row = rows[0]
    repository = LearningTrajectoryRepository()
    fit_partition_digest = _seed_passive_corpus(
        repository,
        rows,
        trace_session_id=provenance["source_session_id"],
        persist_source_segment=False,
    )
    assert repository.read_cook_segments(provenance["source_cook_id"]) == ()
    assert repository.read_cook_segments(_EXACT_REPLAY_COOK_ID) == ()
    support_segments = repository.read_cook_segments("deterministic-grey-support-cook")
    assert len(support_segments) == 1
    assert support_segments[0].source_schema_version == TRAJECTORY_OBSERVATION_SCHEMA_VERSION
    assert support_segments[0].source_trace_digest != _EXACT_V6_ROWS_SHA256
    assert support_segments[0].trace_session_ids == ("trace-deterministic-grey-support-cook",)
    model_store = ControllerModelStore()
    persistence = ModelPersistenceWorker(
        model_store,
        _TEST_LOGGER,
        trajectory_repository=repository,
    )
    gate = _FrameBoundaryGate()
    core = Controller(
        config,
        "C",
        dict(_CYCLE),
        activation_persistence=persistence,
        trajectory_repository=repository,
        fit_partition_digest=lambda: fit_partition_digest,
    )
    runner = ThreadedControllerRunner(
        core,
        controller_type=ControllerType.MPC,
        wait_for_period=gate,
    )
    gate.wait_until_blocked()
    trace = ControlTraceSession(
        ControlTraceRecorder(
            monotonic_clock=lambda: 0,
            wall_clock=lambda: RETENTION_PERIOD_MS,
        ),
        warning=_TEST_LOGGER.warning,
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
    )
    trace_identity = None
    fit_state: dict[str, Any] | None = None
    fit_evidence_records = []
    live_checkpoint: dict[str, Any] | None = None
    persisted_checkpoint: dict[str, Any] | None = None
    normalized_report: dict[str, Any] | None = None
    durable_challenger: ModelChallengerState | None = None
    try:
        learning.restore_model(timestamp_ms=first_row["frame_start_ms"] - 1)
        learning.reconcile_activation()
        gate.advance()
        learning.reconcile_outcomes(first_row["frame_start_ms"] / 1_000.0 - 0.001)
        runner.set_target(first_row["setpoint_c"])
        runner.submit(first_row["ambient_c"])
        gate.advance()

        def restored_v6_projection():
            snapshot = runner.get_model_snapshot()
            if not isinstance(snapshot, dict):
                return None
            identities = snapshot.get("identities")
            if not isinstance(identities, dict):
                return None
            return (
                snapshot
                if snapshot.get("version") == 7
                and identities.get("active_digest") == _EXACT_V6_ACTIVE_DIGEST
                and snapshot.get("challenger_authority") is None
                else None
            )

        restored_v6 = cast(
            dict[str, Any],
            _wait_until(
                restored_v6_projection,
                timeout_s=5.0,
                description="the exact restored v7 active projection",
            ),
        )
        assert _identity(restored_v6) == (_EXACT_V6_ACTIVE_DIGEST, 0)
        assert restored_v6["schema"] == "pifire-grey-learning/v7"
        assert {"challenger", "window", "candidate_pair"}.isdisjoint(restored_v6)
        assert read_model_challenger() is None
        trace_identity = trace.ensure_open(
            _trace_context(
                restored_v6,
                config,
                _EXACT_REPLAY_COOK_ID,
                setpoint_c=first_row["setpoint_c"],
                ambient_c=first_row["ambient_c"],
            ),
            timestamp_ms=first_row["frame_start_ms"],
        )
        assert trace_identity is not None
        learning.bind_generation(0)

        for row in rows:
            _drive_exact_passive_frame(
                row=row,
                runner=runner,
                gate=gate,
                learning=learning,
            )

        fit_state = cast(
            dict[str, Any],
            _wait_until(
                lambda: (
                    state
                    if (state := dict(core.get_learning_diagnostics().state)).get("status") == "evaluating"
                    and state.get("fit_status") == "succeeded"
                    and isinstance(state.get("candidate_digest"), str)
                    else None
                ),
                timeout_s=90.0,
                description="the exact cumulative passive grey fit",
            ),
        )
        for following_row in following_rows:
            _drive_exact_passive_frame(
                row=following_row,
                runner=runner,
                gate=gate,
                learning=learning,
            )
        fit_state = dict(core.get_learning_diagnostics().state)
        assert fit_state["status"] == "evaluating"
        assert fit_state["fit_status"] == "succeeded"
        assert tuple(fit_state["required_horizon_seconds"]) == MPC_FORECAST_HORIZON_SECONDS
        assert tuple(fit_state["completed_horizon_seconds"]) == ()
        fit_pending_origins = cast(list[Mapping[str, Any]], fit_state["pending_origins"])
        assert {origin["horizon_seconds"] for origin in fit_pending_origins} == _REQUIRED_HORIZON_SECONDS
        assert all(
            (
                origin["prediction_steps"],
                origin["observation_frames"],
            )
            == (
                forecast_horizon_spec(origin["horizon_seconds"]).prediction_steps,
                forecast_horizon_spec(origin["horizon_seconds"]).observation_frames,
            )
            for origin in fit_pending_origins
        )

        def durable_fit_lifecycle():
            records = [
                record
                for record in read_model_evidence(cook_id=_EXACT_REPLAY_COOK_ID)
                if isinstance(record.payload, FitLifecycleEvidence)
            ]
            statuses = [cast(FitLifecycleEvidence, record.payload).status for record in records]
            return records if statuses == ["queued", "succeeded"] else None

        fit_evidence_records = _wait_until(
            durable_fit_lifecycle,
            timeout_s=30.0,
            description="durable exact passive fit lifecycle",
        )
        durable_challenger = cast(
            ModelChallengerState,
            _wait_until(
                lambda: (
                    challenger
                    if (
                        (challenger := read_model_challenger()) is not None
                        and challenger.phase == "evaluating"
                        and challenger.candidate.model_digest == fit_state["candidate_digest"]
                    )
                    else None
                ),
                timeout_s=30.0,
                description="the exact passive durable challenger authority",
            ),
        )
        assert durable_challenger.origin is CandidateOrigin.PASSIVE_ONLINE
        assert durable_challenger.policy is ActivationPolicy.CAUSAL_AUTO
        assert durable_challenger.evaluation_epoch == 0
        assert durable_challenger.evaluation_round == 0
        assert durable_challenger.consecutive_wins == 0
        candidate_configuration = durable_challenger.candidate.configuration
        candidate_parameters = {
            name: candidate_configuration[name] for name in ("C_c", "K_Q", "T_amb", "h_amb", "sigma", "theta")
        }
        candidate_parameters["n_delay"] = candidate_configuration.get(
            "n_delay",
            candidate_configuration.get("delay_states"),
        )
        assert durable_challenger.candidate.model_digest != _EXACT_V6_ACTIVE_DIGEST
        assert candidate_parameters == pytest.approx(_SUPPORT_MODEL_PARAMETERS)
        raw_live_checkpoint = runner.get_model_snapshot()
        assert isinstance(raw_live_checkpoint, dict)
        live_checkpoint = raw_live_checkpoint
        assert learning.submit_online_checkpoint(live_checkpoint)
        assert persistence.close(timeout=30.0)
        raw_persisted_checkpoint = ControllerModelStore().load_strict("mpc")
        assert isinstance(raw_persisted_checkpoint, dict)
        persisted_checkpoint = raw_persisted_checkpoint
        normalized_report = current_learning_report(
            tuple(read_model_evidence()),
            activation_state=read_model_activation(),
            live_status=fit_state,
            checkpoint_required=True,
            calibration_command_high_water=0,
            checkpoint=persisted_checkpoint,
            challenger_state=durable_challenger,
        ).as_dict()
    finally:
        gate.close()
        learning.finish_teardown(generation=0)
        runner.stop()

    assert trace_identity is not None
    assert fit_state is not None
    assert live_checkpoint is not None
    assert persisted_checkpoint is not None
    assert normalized_report is not None
    assert durable_challenger is not None
    retained_segments = repository.read_cook_segments(
        "deterministic-grey-support-cook"
    ) + repository.read_cook_segments(_EXACT_REPLAY_COOK_ID)
    assert retained_segments
    assert all(
        segment.source_schema_version != provenance["source_trace_schema_version"]
        and provenance["source_session_id"] not in segment.trace_session_ids
        and segment.source_trace_digest != _EXACT_V6_ROWS_SHA256
        for segment in retained_segments
    )
    observation_records = [
        record
        for record in read_control_trace_cook(_EXACT_REPLAY_COOK_ID)
        if record.event_kind is TraceEventKind.MODEL_OBSERVATION and isinstance(record.payload, ModelObservationPayload)
    ]
    assert len(observation_records) == _FIT_SAMPLES + 1
    assert all(record.schema_version == TRACE_SCHEMA_VERSION for record in observation_records)
    replay_rows = []
    for record in observation_records:
        payload = cast(ModelObservationPayload, record.payload)
        payload_dict = json.loads(record.to_db_row().payload)
        replay_rows.append({field: payload_dict.get(field) for field in fields})
        assert payload.eligible
        assert payload.rejection_reasons == ()
        assert payload.calibration_fit is False
        assert payload.calibration_stage is None
        assert payload.calibration_probe_load == 0.0
        assert payload.baseline_combustion_load == payload.requested_combustion_load
        assert payload.calibration_status == "inactive"
        assert payload.calibration_command_revision == 0
        assert payload.calibration_command_action == "none"
        assert payload.cancellation_command_revision == 0
        assert payload.cancellation_command_action == "none"
    replay_bytes = json.dumps(
        replay_rows[:_FIT_SAMPLES],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    replay_following_bytes = json.dumps(
        replay_rows[_FIT_SAMPLES:],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert hashlib.sha256(replay_bytes).hexdigest() == _EXACT_V6_ROWS_SHA256
    assert hashlib.sha256(replay_following_bytes).hexdigest() == _EXACT_FOLLOWING_ROWS_SHA256
    fit_gate_observation = cast(ModelObservationPayload, observation_records[-2].payload)
    assert fit_gate_observation.observation_sequence == 140
    assert fit_gate_observation.effective_updates == _FIT_SAMPLES
    assert cast(ModelObservationPayload, observation_records[-1].payload).observation_sequence == 141

    fit_payloads = [cast(FitLifecycleEvidence, record.payload) for record in fit_evidence_records]
    assert all(record.schema_version == MODEL_EVIDENCE_SCHEMA_VERSION for record in fit_evidence_records)
    assert [payload.status for payload in fit_payloads] == ["queued", "succeeded"]
    assert len({payload.request_id for payload in fit_payloads}) == 1
    assert all(
        payload.origin == CandidateOrigin.PASSIVE_ONLINE.value
        and payload.policy == ActivationPolicy.CAUSAL_AUTO.value
        and payload.fit_corpus_digest == durable_challenger.fit_corpus.corpus_digest
        for payload in fit_payloads
    )
    assert fit_evidence_records[0].model_digest == _EXACT_V6_ACTIVE_DIGEST
    assert fit_evidence_records[1].model_digest == durable_challenger.candidate.model_digest
    assert not any(
        isinstance(
            record.payload,
            (
                CandidateAssessmentEvidence,
                ConfidenceDecisionEvidence,
                ActivationLifecycleEvidence,
            ),
        )
        for record in read_model_evidence(cook_id=_EXACT_REPLAY_COOK_ID)
    )
    assert read_model_activation() is None

    restart_migration = migrate_mpc_learning_authority(defaults=config)
    assert restart_migration.snapshot["version"] == 7
    restart_repository = LearningTrajectoryRepository()
    restart_model_store = ControllerModelStore()
    restart_persistence = ModelPersistenceWorker(
        restart_model_store,
        _TEST_LOGGER,
        trajectory_repository=restart_repository,
    )
    restart_core = Controller(
        config,
        "C",
        dict(_CYCLE),
        activation_persistence=restart_persistence,
        trajectory_repository=restart_repository,
        fit_partition_digest=lambda: fit_partition_digest,
    )
    restart_runner = SyncControllerRunner(restart_core, controller_type=ControllerType.MPC)
    restart_learning = HoldLearningRuntime(
        runner=restart_runner,
        model_store=restart_model_store,
        persistence=restart_persistence,
        trajectory_repository=restart_repository,
        trace=None,
        controller_name="mpc",
        logger=_TEST_LOGGER,
        initial_generation=0,
    )
    restart_challenger: ModelChallengerState | None = None
    try:
        restart_runner.set_target(first_row["setpoint_c"])
        restart_learning.restore_model(timestamp_ms=following_rows[-1]["frame_end_ms"] + 1)
        restart_learning.reconcile_activation()
        restart_status = dict(restart_core.get_learning_diagnostics().state)
        assert restart_status["resumed_from_previous_cook"] is True
        assert restart_status["pending_origins"] == ()
        raw_restart_checkpoint = restart_runner.get_model_snapshot()
        assert isinstance(raw_restart_checkpoint, dict)
        restart_checkpoint: dict[str, Any] = raw_restart_checkpoint
        restart_challenger = read_model_challenger()
        assert restart_challenger is not None
    finally:
        restart_learning.finish_teardown(generation=0)
        restart_runner.stop()
        assert restart_persistence.close(timeout=30.0)
    assert restart_challenger is not None

    expected_corpus = {
        "digest": durable_challenger.fit_corpus.corpus_digest,
        "revision": durable_challenger.fit_corpus.corpus_revision,
        "fit_partition_digest": durable_challenger.fit_corpus.fit_partition_digest,
        "slices": [
            {
                "segment_id": corpus_slice.segment_id,
                "through_ordinal": corpus_slice.through_ordinal,
                "prefix_digest": corpus_slice.prefix_digest,
                "segment_content_digest": corpus_slice.segment_content_digest,
                "pre_roll_count": corpus_slice.pre_roll_count,
                "scored_count": corpus_slice.scored_count,
            }
            for corpus_slice in durable_challenger.fit_corpus.slices
        ],
    }
    expected_lineage = {
        "origin": CandidateOrigin.PASSIVE_ONLINE.value,
        "policy": ActivationPolicy.CAUSAL_AUTO.value,
        "candidate_digest": durable_challenger.candidate.model_digest,
        "candidate_generation": 1,
    }
    lineage_by_surface = {
        "checkpoint": _snapshot_candidate_lineage(live_checkpoint, durable_challenger),
        "normalized_report": _report_candidate_lineage(normalized_report, durable_challenger),
        "persistence": _snapshot_candidate_lineage(persisted_checkpoint, durable_challenger),
        "restart": _snapshot_candidate_lineage(restart_checkpoint, restart_challenger),
    }
    assert lineage_by_surface == {surface: expected_lineage for surface in lineage_by_surface}
    assert normalized_report["corpus"] == expected_corpus
    assert durable_challenger.controller_configuration_digest == _EXACT_PASSIVE_CONFIGURATION_DIGEST
    assert restart_challenger.revision == durable_challenger.revision + 1
    assert restart_challenger.evaluation_epoch == durable_challenger.evaluation_epoch + 1
    assert restart_challenger.evaluation_round == 0
    assert restart_challenger.consecutive_wins == durable_challenger.consecutive_wins
    assert restart_challenger.incumbent == durable_challenger.incumbent
    assert restart_challenger.candidate == durable_challenger.candidate
    assert restart_challenger.fit_corpus == durable_challenger.fit_corpus
    assert restart_challenger.fit_lineage == durable_challenger.fit_lineage

    assert normalized_report["status"] == "evaluating", normalized_report
    report_evaluation = cast(dict[str, Any], normalized_report["evaluation"])
    assert report_evaluation["required_horizon_seconds"] == list(MPC_FORECAST_HORIZON_SECONDS)
    assert report_evaluation["completed_horizon_seconds"] == []
    report_pending_origins = cast(list[Mapping[str, Any]], report_evaluation["pending_origins"])
    assert {origin["horizon_seconds"] for origin in report_pending_origins} == _REQUIRED_HORIZON_SECONDS
    assert all(
        (
            origin["prediction_steps"],
            origin["observation_frames"],
        )
        == (
            forecast_horizon_spec(origin["horizon_seconds"]).prediction_steps,
            forecast_horizon_spec(origin["horizon_seconds"]).observation_frames,
        )
        for origin in report_pending_origins
    )
    assert normalized_report["mode"] == CandidateOrigin.PASSIVE_ONLINE.value
    assert normalized_report["blockers"] == []
    for checkpoint, challenger, expected_revision in (
        (
            live_checkpoint,
            durable_challenger,
            source_checkpoint["revision"] + 1,
        ),
        (
            persisted_checkpoint,
            durable_challenger,
            source_checkpoint["revision"] + 1,
        ),
        (
            restart_checkpoint,
            restart_challenger,
            source_checkpoint["revision"] + 2,
        ),
    ):
        assert _identity(checkpoint) == (_EXACT_V6_ACTIVE_DIGEST, 0)
        assert checkpoint["version"] == 7
        assert checkpoint["schema"] == "pifire-grey-learning/v7"
        assert checkpoint["revision"] == expected_revision
        assert checkpoint["activation"] == {
            "pending_persistence": False,
            "pending_swap": False,
            "phase": ActivationPhase.ABORTED.value,
        }
        assert {"challenger", "window", "candidate_pair"}.isdisjoint(checkpoint)
        assert checkpoint["challenger_authority"] == {
            "challenger_id": challenger.challenger_id,
            "revision": challenger.revision,
        }
