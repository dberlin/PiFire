from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from hashlib import sha256
from math import ceil
from types import SimpleNamespace
from typing import Any, Literal, cast

import numpy as np
import pytest

from common.control_trace import ControllerType
from common.learning_trajectory import FrameDeliveryCertainty, TrajectoryBreakReason
from common.persistence.learning_trajectory import SegmentCursor
from controller.acados import GreyBoxMPCConfig
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_core import MpcCore
from controller.mpc_model import EstimatorSeed, GreyBoxEKF
from controller.runtime.actuation_delivery import DeliveredActuationIntegral
from controller.runtime.learning_trajectory import (
    LearningTrajectoryRuntime,
    ModeEntered,
    ModeExited,
    ThermalSample,
    TrajectoryBoundary,
)
from controller.runtime.model_persistence import TrajectoryAppendBatch
from controller.runtime.runner import ControllerUpdateResult, SyncControllerRunner
from tests.fakes.runner import FakeControllerRunner
from tests.unit.mpc._solver_fixtures import CYCLE, _config, _Solver

_FRAME_MS = 20_000
_WALL_OFFSET_MS = 1_700_000_000_000


def _digest(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


class _SegmentIds:
    def __init__(self) -> None:
        self._next = 1

    def __call__(self) -> str:
        value = f"segment-{self._next}"
        self._next += 1
        return value


class _Journal:
    def __init__(self) -> None:
        self._integrals: dict[tuple[int, int], DeliveredActuationIntegral] = {}

    def set_load(
        self,
        start_ms: int,
        end_ms: int,
        load: float,
        *,
        exact: bool = True,
    ) -> None:
        duration_s = (end_ms - start_ms) / 1_000
        certainty = FrameDeliveryCertainty.EXACT if exact else FrameDeliveryCertainty.UNKNOWN
        self._integrals[(start_ms, end_ms)] = DeliveredActuationIntegral(
            monotonic_start_ms=start_ms,
            monotonic_end_ms=end_ms,
            auger_on_seconds=load * duration_s,
            fan_on_seconds=duration_s,
            fan_duty_integral_seconds=duration_s * 0.5,
            auger_start_active=False,
            auger_end_active=False,
            fan_start_active=True,
            fan_end_active=True,
            pwm_start=0.5,
            pwm_end=0.5,
            auger_certainty=certainty,
            fan_certainty=FrameDeliveryCertainty.EXACT,
            unknown_reasons=() if exact else ("delivery-gap",),
        )

    def integrate(self, start_ms: int, end_ms: int) -> DeliveredActuationIntegral:
        return self._integrals[(start_ms, end_ms)]


@dataclass(slots=True)
class _Receipt:
    accepted: bool
    completed: bool
    durable: bool
    cursor: SegmentCursor | None
    error: str | None = None


class _Persistence:
    def __init__(self) -> None:
        self.revision = 0

    def submit_trajectory_batch(self, batch: TrajectoryAppendBatch) -> _Receipt:
        self.revision += 1
        segment = batch.begin_segment or batch.next_segment
        if segment is not None:
            next_ordinal = len(segment.pre_roll_frames) + len(segment.scored_hold_frames)
            segment_id = segment.segment_id
        elif batch.cursor is not None:
            next_ordinal = batch.cursor.next_ordinal + len(batch.pre_roll) + len(batch.scored)
            segment_id = batch.cursor.segment_id
        else:
            next_ordinal = 0
            segment_id = "segment-none"
        cursor = SegmentCursor(
            segment_id=segment_id,
            next_ordinal=next_ordinal,
            chain_digest=_digest(f"{segment_id}:{self.revision}:{next_ordinal}"),
            corpus_revision=self.revision,
        )
        return _Receipt(True, True, True, cursor)

    def barrier(self, timeout: float = 2.0) -> bool:
        del timeout
        return True

    def close(self, timeout: float = 2.0) -> bool:
        del timeout
        return True


def _entered(
    mode: str,
    *,
    at_ms: int = 0,
    units: str = "C",
    identity: str = "compatible",
) -> ModeEntered:
    return ModeEntered(
        effective_mode=mode,
        persisted_mode=mode,
        monotonic_ms=at_ms,
        wall_ms=_WALL_OFFSET_MS + at_ms,
        cook_id="cook-1",
        trajectory_session_id="trajectory-session",
        trace_session_id="trace-session",
        recipe_step_id=None,
        units=units,
        settings_revision=7,
        collection_provenance={"origin": "passive-online"},
        configuration_provenance={"controller": "MPC"},
        cadence_digest=_digest("cadence-20-seconds-v1"),
        model_structure_digest=_digest(f"model:{identity}"),
        held_physics_digest=_digest("held-grey-physics-v1"),
        delay_input_mapping_digest=_digest("normalized-load-v1"),
        actuation_mapping_digest=_digest("framed-pulse-v1"),
        scored_fan_regime_digest=_digest("fan-regime-v1"),
        ambient_semantics_digest=_digest("ambient-v1"),
        source_trace_digest=_digest("source-trace-v1"),
        source_schema_version=7,
        source_row_digest=_digest("source-rows-v1"),
        build_provenance={"revision": 1},
    )


def _sample(
    at_ms: int,
    temperature: float,
    *,
    units: str = "C",
) -> ThermalSample:
    return ThermalSample(
        monotonic_ms=at_ms,
        wall_ms=_WALL_OFFSET_MS + at_ms,
        chamber_temperature=temperature,
        units=units,
        probe_valid=True,
        probe_source="grill-probe-1",
        ambient_temperature=68.0 if units == "F" else 20.0,
        ambient_source="configured",
        ambient_uncertainty=1.0,
        settings_revision=7,
        recipe_step_id=None,
    )


def _runtime() -> tuple[LearningTrajectoryRuntime, _Journal]:
    journal = _Journal()
    return (
        LearningTrajectoryRuntime(
            journal=journal,
            persistence=_Persistence(),
            segment_id_factory=_SegmentIds(),
            sample_age_limit_ms=51,
        ),
        journal,
    )


def _record_smoke(
    loads: tuple[float, ...],
    *,
    temperatures: tuple[float, ...] | None = None,
    units: str = "C",
    identity: str = "compatible",
    uncertain_index: int | None = None,
) -> tuple[LearningTrajectoryRuntime, int]:
    runtime, journal = _runtime()
    runtime.mode_entered(_entered("Smoke", units=units, identity=identity))
    observed_temperatures = temperatures or tuple(
        (212.0 + index if units == "F" else 100.0 + index) for index in range(len(loads))
    )
    for index, (load, temperature) in enumerate(zip(loads, observed_temperatures, strict=True)):
        start_ms = index * _FRAME_MS
        end_ms = start_ms + _FRAME_MS
        journal.set_load(
            start_ms,
            end_ms,
            load,
            exact=index != uncertain_index,
        )
        runtime.observe_temperature(_sample(end_ms, temperature, units=units))
    return runtime, len(loads) * _FRAME_MS


def _transition_to_hold(
    runtime: LearningTrajectoryRuntime,
    at_ms: int,
    *,
    anchor_temperature: float = 110.0,
    measured_temperature_c: float | None = None,
    units: str = "C",
    identity: str = "compatible",
) -> tuple[int, float]:
    runtime.mode_exited(
        ModeExited(
            effective_mode="Smoke",
            next_effective_mode="Hold",
            monotonic_ms=at_ms,
            wall_ms=_WALL_OFFSET_MS + at_ms,
        )
    )
    runtime.mode_entered(_entered("Hold", at_ms=at_ms, units=units, identity=identity))
    anchor_ms = at_ms + 25
    runtime.observe_temperature(_sample(anchor_ms, anchor_temperature, units=units))
    canonical = (
        (anchor_temperature - 32.0) * 5.0 / 9.0
        if measured_temperature_c is None and units == "F"
        else anchor_temperature
        if measured_temperature_c is None
        else measured_temperature_c
    )
    return anchor_ms, canonical


def _seed_from_smoke(
    loads: tuple[float, ...],
    *,
    theta: float,
    n_delay: int = 8,
    temperatures: tuple[float, ...] | None = None,
    units: str = "C",
    anchor_temperature: float = 110.0,
    measured_temperature_c: float | None = None,
    uncertain_index: int | None = None,
) -> EstimatorSeed:
    runtime, end_ms = _record_smoke(
        loads,
        temperatures=temperatures,
        units=units,
        uncertain_index=uncertain_index,
    )
    anchor_ms, canonical = _transition_to_hold(
        runtime,
        end_ms,
        anchor_temperature=anchor_temperature,
        measured_temperature_c=measured_temperature_c,
        units=units,
    )
    return runtime.seed_for(
        theta=theta,
        n_delay=n_delay,
        at_ms=anchor_ms,
        measured_temp_c=canonical,
    )


def _seed(
    *,
    status: Literal["exact", "short", "absent", "uncertain"] = "exact",
    delay_states: tuple[float, ...] | None = None,
    chamber_temperature_c: float = 110.0,
    frame_count: int | None = None,
    required_frame_count: int = 8,
    label: str = "seed",
) -> EstimatorSeed:
    states = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8) if delay_states is None else delay_states
    count = required_frame_count if frame_count is None else frame_count
    return EstimatorSeed(
        delay_states=states,
        chamber_temperature_c=chamber_temperature_c,
        disturbance=0.0,
        segment_id="segment-1",
        pre_roll_digest=_digest(label),
        pre_roll_frame_count=count,
        required_frame_count=required_frame_count,
        status=status,
    )


def test_seed_selects_only_the_candidate_suffix_and_digests_exact_prefix_identity() -> None:
    common_suffix = (0.1, 0.25, 0.8, 0.4, 0.7, 0.2)
    first = _seed_from_smoke((0.0, *common_suffix), theta=40.0)
    excluded_changed = _seed_from_smoke((1.0, *common_suffix), theta=40.0)
    selected_changed = _seed_from_smoke(
        (0.0, *common_suffix[:-1], 0.9),
        theta=40.0,
    )

    assert first.status == "exact"
    assert first.required_frame_count == 6
    assert first.pre_roll_frame_count == 6
    assert excluded_changed.delay_states == first.delay_states
    assert excluded_changed.pre_roll_digest != first.pre_roll_digest
    assert selected_changed.pre_roll_digest != first.pre_roll_digest
    assert selected_changed.delay_states != first.delay_states


def test_required_frame_count_is_candidate_specific_and_capped_at_180() -> None:
    loads = tuple((index % 10) / 10 for index in range(180))
    runtime, end_ms = _record_smoke(loads)
    anchor_ms, measured_c = _transition_to_hold(runtime, end_ms)

    maximum = runtime.seed_for(
        theta=1_200.0,
        n_delay=8,
        at_ms=anchor_ms,
        measured_temp_c=measured_c,
    )
    short_path = runtime.seed_for(
        theta=21.0,
        n_delay=8,
        at_ms=anchor_ms,
        measured_temp_c=measured_c,
    )
    alternate_structure = runtime.seed_for(
        theta=21.0,
        n_delay=4,
        at_ms=anchor_ms,
        measured_temp_c=measured_c,
    )

    assert maximum.required_frame_count == 180
    assert maximum.pre_roll_frame_count == 180
    assert maximum.status == "exact"
    assert short_path.required_frame_count == ceil(3 * 21.0 / 20.0) == 4
    assert short_path.pre_roll_frame_count == 4
    assert short_path.status == "exact"
    assert short_path.pre_roll_digest != maximum.pre_roll_digest
    assert alternate_structure.required_frame_count == short_path.required_frame_count
    assert len(alternate_structure.delay_states) == 4
    assert alternate_structure.pre_roll_digest != short_path.pre_roll_digest


def test_partial_tail_counts_only_its_duration_toward_exact_seed() -> None:
    runtime, journal = _runtime()
    runtime.mode_entered(_entered("Smoke"))
    for index in range(8):
        start_ms = index * _FRAME_MS
        end_ms = start_ms + _FRAME_MS
        journal.set_load(start_ms, end_ms, 0.2 + index * 0.05)
        runtime.observe_temperature(_sample(end_ms, 100.0 + index))
    partial_end_ms = 8 * _FRAME_MS + 1_000
    journal.set_load(8 * _FRAME_MS, partial_end_ms, 0.7)
    runtime.observe_temperature(_sample(partial_end_ms, 109.0))
    anchor_ms, measured_c = _transition_to_hold(runtime, partial_end_ms)

    seed = runtime.seed_for(
        theta=60.0,
        n_delay=8,
        at_ms=anchor_ms,
        measured_temp_c=measured_c,
    )

    assert seed.status == "short"
    assert seed.required_frame_count == 9
    assert seed.pre_roll_frame_count == 8


def test_maximum_theta_partial_tail_promotes_after_one_full_hold_frame() -> None:
    runtime, journal = _runtime()
    runtime.mode_entered(_entered("Smoke"))
    for index in range(179):
        start_ms = index * _FRAME_MS
        end_ms = start_ms + _FRAME_MS
        journal.set_load(start_ms, end_ms, (index % 10) / 10)
        runtime.observe_temperature(_sample(end_ms, 100.0))
    partial_start_ms = 179 * _FRAME_MS
    partial_end_ms = partial_start_ms + 1_000
    journal.set_load(partial_start_ms, partial_end_ms, 0.4)
    runtime.observe_temperature(_sample(partial_end_ms, 100.0))
    anchor_ms, measured_c = _transition_to_hold(runtime, partial_end_ms)
    short = runtime.seed_for(1_200.0, 8, anchor_ms, measured_c)
    assert short.status == "short"
    assert short.required_frame_count - short.pre_roll_frame_count == 1

    warm_end_ms = partial_end_ms + _FRAME_MS
    journal.set_load(partial_end_ms, warm_end_ms, 0.6)
    warm_frame = runtime._frame_from_integral(
        start_ms=partial_end_ms,
        end_ms=warm_end_ms,
        wall_start_ms=_WALL_OFFSET_MS + partial_end_ms,
        wall_end_ms=_WALL_OFFSET_MS + warm_end_ms,
        sample=_sample(warm_end_ms, 101.0),
        integral=journal.integrate(partial_end_ms, warm_end_ms),
        effective_mode="Hold",
        partial=False,
        boundary_reason=None,
        normalized_load=0.6,
    )
    runtime._retain_replay_frame(warm_frame)

    exact = runtime.seed_for(1_200.0, 8, warm_end_ms, 101.0)
    assert exact.status == "exact"
    assert exact.pre_roll_frame_count == exact.required_frame_count == 180
    assert len(runtime._replay_frames) == 181


def test_seed_reports_short_absent_and_uncertain_without_fabricating_history() -> None:
    short = _seed_from_smoke((0.2, 0.6), theta=60.0)

    absent_runtime, _journal = _runtime()
    absent_runtime.mode_entered(_entered("Hold"))
    absent_runtime.observe_temperature(_sample(25, 110.0))
    absent = absent_runtime.seed_for(
        theta=60.0,
        n_delay=8,
        at_ms=25,
        measured_temp_c=110.0,
    )

    uncertain = _seed_from_smoke(
        (0.5,),
        theta=60.0,
        uncertain_index=0,
    )

    assert short.status == "short"
    assert short.pre_roll_frame_count == 2
    assert short.required_frame_count == 9
    assert short.required_frame_count - short.pre_roll_frame_count == 7
    assert absent.status == "absent"
    assert absent.pre_roll_frame_count == 0
    assert absent.delay_states == ()
    assert uncertain.status == "uncertain"
    assert uncertain.pre_roll_frame_count == 0
    assert uncertain.delay_states == ()


@pytest.mark.parametrize(
    "reason",
    (
        TrajectoryBreakReason.PROBE_GAP,
        TrajectoryBreakReason.MANUAL,
        TrajectoryBreakReason.SAFETY,
    ),
)
def test_gap_manual_and_safety_boundaries_exclude_all_earlier_pre_roll(
    reason: TrajectoryBreakReason,
) -> None:
    runtime, at_ms = _record_smoke((0.2, 0.4, 0.6))
    replacement = _entered("Hold", at_ms=at_ms)
    runtime.intervention(
        TrajectoryBoundary(
            reason=reason,
            monotonic_ms=at_ms,
            wall_ms=_WALL_OFFSET_MS + at_ms,
            detail=f"test-{reason.value}",
            replacement_mode=replacement,
        )
    )
    anchor_ms = at_ms + 25
    runtime.observe_temperature(_sample(anchor_ms, 110.0))

    seed = runtime.seed_for(
        theta=40.0,
        n_delay=8,
        at_ms=anchor_ms,
        measured_temp_c=110.0,
    )

    assert seed.status == "absent"
    assert seed.pre_roll_frame_count == 0
    assert seed.delay_states == ()


def test_identity_mismatch_excludes_physical_pre_roll_as_uncertain() -> None:
    runtime, at_ms = _record_smoke((0.2, 0.4, 0.6), identity="original")
    anchor_ms, measured_c = _transition_to_hold(
        runtime,
        at_ms,
        identity="different-model-structure",
    )

    seed = runtime.seed_for(
        theta=40.0,
        n_delay=8,
        at_ms=anchor_ms,
        measured_temp_c=measured_c,
    )

    assert seed.status == "uncertain"
    assert seed.pre_roll_frame_count == 0
    assert seed.delay_states == ()


def test_mpc_core_seed_resets_delay_chamber_disturbance_and_high_covariance() -> None:
    config = _config(theta=80.0, n_delay=8)
    estimator = GreyBoxEKF(
        C_c=float(config["C_c"]),
        h_amb=float(config["h_amb"]),
        T_amb=float(config["T_amb"]),
        t_step=float(config["control_period"]),
        q_temp=float(config["est_q_temp"]),
        q_dist=float(config["est_q_dist"]),
        r_meas=float(config["est_r_meas"]),
        theta=float(config["theta"]),
        n_delay=int(config["n_delay"]),
        K_Q=float(config["K_Q"]),
        sigma=float(config["sigma"]),
    )
    estimator.P[:] = 0.0
    core = MpcCore(
        config,
        "C",
        dict(CYCLE),
        components=(estimator, _Solver(GreyBoxMPCConfig())),  # type: ignore[arg-type]
    )
    seed = _seed(chamber_temperature_c=137.25)

    core.seed_from_trajectory(seed)

    state = core.capture_operating_state()
    assert state.delay_states == seed.delay_states
    assert state.measured_temperature_c == pytest.approx(137.25)
    assert state.disturbance == pytest.approx(0.0)
    np.testing.assert_allclose(estimator.P, np.eye(10) * 5.0, rtol=0, atol=0)
    core.close()


def test_first_solve_consumes_seed_anchor_without_predicting_one_period(
    monkeypatch,
) -> None:
    config = _config(theta=80.0, n_delay=8)
    estimator = GreyBoxEKF(
        C_c=float(config["C_c"]),
        h_amb=float(config["h_amb"]),
        T_amb=float(config["T_amb"]),
        t_step=float(config["control_period"]),
        q_temp=float(config["est_q_temp"]),
        q_dist=float(config["est_q_dist"]),
        r_meas=float(config["est_r_meas"]),
        theta=float(config["theta"]),
        n_delay=int(config["n_delay"]),
        K_Q=float(config["K_Q"]),
        sigma=float(config["sigma"]),
    )
    core = MpcCore(
        config,
        "C",
        dict(CYCLE),
        components=(estimator, _Solver(GreyBoxMPCConfig())),  # type: ignore[arg-type]
    )
    updates = 0
    original_update = estimator.update

    def counted_update(load: float, temperature: float):
        nonlocal updates
        updates += 1
        return original_update(load, temperature)

    monkeypatch.setattr(estimator, "update", counted_update)
    core.seed_from_trajectory(_seed(chamber_temperature_c=137.25))
    core.set_target(225.0)

    core.update(137.25)
    assert updates == 0
    core.update(137.25)
    assert updates == 1
    core.close()


class _OrderedCore:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def seed_from_trajectory(self, seed: EstimatorSeed) -> None:
        assert isinstance(seed, EstimatorSeed)
        self.events.append("core:seed")

    def set_target(self, setpoint: float) -> None:
        del setpoint
        self.events.append("core:target")

    def update(self, temperature: float) -> float:
        del temperature
        self.events.append("core:solve")
        return 0.25

    def get_control_period(self) -> float:
        return 1.0

    def get_status(self) -> dict[str, object]:
        return {}


class _SeedSource:
    def __init__(self, events: list[str], seed: EstimatorSeed) -> None:
        self.events = events
        self.seed = seed
        self.calls: list[dict[str, Any]] = []
        self.anchor: tuple[int, float] | None = None

    def estimator_seed_anchor(self) -> tuple[int, float] | None:
        return self.anchor

    def seed_for(
        self,
        theta: float,
        n_delay: int,
        at_ms: int,
        measured_temp_c: float,
    ) -> EstimatorSeed:
        self.events.append("trajectory:seed")
        self.calls.append(
            {
                "theta": theta,
                "n_delay": n_delay,
                "at_ms": at_ms,
                "measured_temp_c": measured_temp_c,
            }
        )
        return self.seed

    def bind_trace_session(
        self,
        session_id,
        cook_id,
        publish_segment,
        *,
        failure_handler=None,
    ) -> bool:
        del session_id, cook_id, publish_segment, failure_handler
        return True

    def mark_trace_unavailable(self, reason: str) -> None:
        self.events.append(f"trajectory:unavailable:{reason}")

    def observe_hold_frame(
        self,
        observation,
        *,
        replay_only: bool = False,
    ) -> None:
        del observation, replay_only

    def barrier(self, timeout: float = 2.0) -> bool:
        del timeout
        return True


class _OrderedSeedRunner(FakeControllerRunner):
    def __init__(self, events: list[str], *, period: float = 1.0) -> None:
        super().__init__(
            period=period,
            controller_type=ControllerType.MPC,
        )
        self.events = events
        self.seeds: list[EstimatorSeed] = []

    def seed_operating_state(self, seed: EstimatorSeed) -> None:
        self.events.append("runner:seed")
        self.seeds.append(seed)

    def set_target(self, setpoint: float) -> None:
        self.events.append("runner:target")
        super().set_target(setpoint)

    def submit(self, temp: float) -> None:
        self.events.append("runner:submit")
        super().submit(temp)

    def latest(self) -> ControllerUpdateResult:
        self.events.append("runner:solve")
        result = super().latest()
        assert isinstance(result, ControllerUpdateResult)
        return result

    def set_output(self, applied: object) -> None:
        self.events.append("runner:output")
        super().set_output(applied)


class _PidToMpcRunner(_OrderedSeedRunner):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self._controller_type = ControllerType.PID

    def reconfigure(self, settings, control, logger=None):
        self._controller_type = ControllerType.MPC
        return super().reconfigure(settings, control, logger=logger)


def _ordered_runner_result() -> ControllerUpdateResult:
    return ControllerUpdateResult(
        cycle_ratio=0.25,
        fan=None,
        input_temperature=110.0,
        revision=1,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.125,
        solve_duration_seconds=0.125,
        completed_wall_time=1.125,
    )


def _assert_order(events: list[str], expected: tuple[str, ...]) -> None:
    assert all(item in events for item in expected), events
    positions = tuple(events.index(item) for item in expected)
    assert positions == tuple(sorted(positions)), events


def test_hold_rejects_an_incomplete_mpc_seed_source(hold_cycle) -> None:
    runner = _OrderedSeedRunner([]).script([_ordered_runner_result()])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.ctx.learning_trajectory = SimpleNamespace(
        seed_for=lambda **_kwargs: _seed(),
    )

    try:
        with pytest.raises(
            TypeError,
            match="learning trajectory is missing the estimator seed capability",
        ):
            hold.on_tick(10.0, 110.0, hold.grill.get_output_status())
    finally:
        hold.teardown(110.0)


def test_hold_accepts_none_mpc_seed_source_as_cold_start(hold_cycle) -> None:
    runner = _OrderedSeedRunner([]).script([_ordered_runner_result()])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.ctx.learning_trajectory = None

    try:
        hold.on_tick(10.0, 110.0, hold.grill.get_output_status())

        assert len(runner.seeds) == 1
        assert runner.seeds[0].status == "absent"
    finally:
        hold.teardown(110.0)


def test_sync_runner_forwards_seed_before_target_and_first_solve() -> None:
    events: list[str] = []
    runner = SyncControllerRunner(_OrderedCore(events))

    runner.seed_operating_state(_seed())
    runner.set_target(225.0)
    runner.submit(110.0)
    runner.latest()

    assert events == ["core:seed", "core:target", "core:solve"]


def test_hold_seeds_before_first_submit_solve_or_controller_output(
    hold_cycle,
) -> None:
    events: list[str] = []
    runner = _OrderedSeedRunner(events).script([_ordered_runner_result()])
    seed_source = _SeedSource(
        events,
        _seed(chamber_temperature_c=(110.0 - 32.0) * 5.0 / 9.0),
    )
    seed_source.anchor = (9_000, (110.0 - 32.0) * 5.0 / 9.0)
    hold = hold_cycle(runner, controller="mpc")
    hold.ctx.learning_trajectory = seed_source

    try:
        hold.setup()
        assert not any(
            event in events
            for event in (
                "trajectory:seed",
                "runner:seed",
                "runner:submit",
                "runner:solve",
                "runner:output",
            )
        )

        hold.on_tick(10.0, 110.0, hold.grill.get_output_status())

        _assert_order(
            events,
            (
                "trajectory:seed",
                "runner:seed",
                "runner:target",
                "runner:submit",
                "runner:solve",
                "runner:output",
            ),
        )
        assert runner.seeds == [seed_source.seed]
        assert seed_source.calls[0]["measured_temp_c"] == pytest.approx((110.0 - 32.0) * 5.0 / 9.0)
        assert seed_source.calls[0]["theta"] == pytest.approx(DEFAULT_MPC_CONFIG["theta"])
        assert seed_source.calls[0]["n_delay"] == DEFAULT_MPC_CONFIG["n_delay"]
        assert seed_source.calls[0]["at_ms"] == 9_000
    finally:
        hold.teardown(110.0)


def test_controller_update_adoption_reuses_valid_trajectory_for_reseed(
    hold_cycle,
) -> None:
    events: list[str] = []
    runner = _OrderedSeedRunner(events).script([_ordered_runner_result()])
    measured_c = (110.0 - 32.0) * 5.0 / 9.0
    initial_seed = _seed(
        label="initial-controller-generation",
        chamber_temperature_c=measured_c,
    )
    replacement_seed = _seed(
        label="replacement-controller-generation",
        chamber_temperature_c=measured_c,
    )
    seed_source = _SeedSource(events, initial_seed)
    hold = hold_cycle(runner, controller="mpc")
    hold.ctx.learning_trajectory = seed_source

    try:
        hold.setup()
        hold.on_tick(10.0, 110.0, hold.grill.get_output_status())
        assert runner.seeds == [initial_seed]

        seed_source.seed = replacement_seed
        hold.control["controller_update"] = True
        hold.on_tick(20.0, 110.0, hold.grill.get_output_status())

        assert runner.seeds == [initial_seed, replacement_seed]
        assert len(seed_source.calls) == 2
        assert hold._estimator_seed_status == "exact"
    finally:
        hold.teardown(110.0)


def test_pid_to_mpc_controller_update_uses_valid_trajectory_seed(
    hold_cycle,
) -> None:
    events: list[str] = []
    runner = _PidToMpcRunner(events).script([_ordered_runner_result()])
    seed = _seed(
        label="pid-to-mpc-controller-generation",
        chamber_temperature_c=(110.0 - 32.0) * 5.0 / 9.0,
    )
    seed_source = _SeedSource(events, seed)
    hold = hold_cycle(runner, controller="pid")
    hold.ctx.learning_trajectory = seed_source

    try:
        hold.setup()
        hold.on_tick(10.0, 110.0, hold.grill.get_output_status())
        assert runner.seeds == []

        hold.ctx.store._settings["controller"]["selected"] = "mpc"
        hold.control["controller_update"] = True
        hold.on_tick(20.0, 110.0, hold.grill.get_output_status())

        assert runner.seeds == [seed]
        assert len(seed_source.calls) == 1
        assert hold._estimator_seed_status == "exact"
    finally:
        hold.teardown(110.0)


def test_pid_to_mpc_controller_update_rejects_incomplete_trajectory_before_trace(
    hold_cycle,
) -> None:
    runner = _PidToMpcRunner([]).script([_ordered_runner_result()])
    hold = hold_cycle(runner, controller="pid")
    hold.setup()
    hold.ctx.learning_trajectory = SimpleNamespace(
        seed_for=lambda **_kwargs: _seed(),
    )
    hold.ctx.store._settings["controller"]["selected"] = "mpc"
    hold.control["controller_update"] = True

    try:
        with pytest.raises(
            TypeError,
            match="learning trajectory is missing the estimator seed capability",
        ):
            hold.on_tick(20.0, 110.0, hold.grill.get_output_status())
    finally:
        hold.teardown(110.0)


def test_first_seeded_tick_bypasses_normal_controller_cadence(hold_cycle) -> None:
    events: list[str] = []
    runner = _OrderedSeedRunner(events, period=10.0).script([_ordered_runner_result()])
    seed_source = _SeedSource(
        events,
        _seed(chamber_temperature_c=(110.0 - 32.0) * 5.0 / 9.0),
    )
    hold = hold_cycle(runner, controller="mpc")
    hold.ctx.learning_trajectory = seed_source

    try:
        hold.setup()
        hold.on_tick(1.0, 110.0, hold.grill.get_output_status())

        assert "runner:seed" in events
        assert "runner:submit" in events
        assert "runner:solve" in events
    finally:
        hold.teardown(110.0)


def test_first_solve_remains_pending_until_runner_has_completed_result(
    hold_cycle,
) -> None:
    events: list[str] = []

    class DelayedResultRunner(_OrderedSeedRunner):
        def __init__(self) -> None:
            super().__init__(events, period=10.0)
            self.latest_calls = 0

        def latest(self) -> ControllerUpdateResult | None:
            self.events.append("runner:solve")
            self.latest_calls += 1
            return None if self.latest_calls == 1 else _ordered_runner_result()

    runner = DelayedResultRunner()
    seed_source = _SeedSource(
        events,
        _seed(chamber_temperature_c=(110.0 - 32.0) * 5.0 / 9.0),
    )
    hold = hold_cycle(runner, controller="mpc")
    hold.ctx.learning_trajectory = seed_source

    try:
        hold.setup()
        hold.on_tick(1.0, 110.0, hold.grill.get_output_status())
        assert hold._first_solve_pending
        assert runner.applied == []

        hold.on_tick(2.0, 110.0, hold.grill.get_output_status())
        assert not hold._first_solve_pending
        assert runner.applied[0].source.value == "seed"
    finally:
        hold.teardown(110.0)


def test_absent_seed_keeps_active_incumbent_control_but_withholds_learning(
    hold_cycle,
) -> None:
    events: list[str] = []
    runner = _OrderedSeedRunner(events).script([_ordered_runner_result()])
    absent = _seed(
        status="absent",
        delay_states=(),
        frame_count=0,
        required_frame_count=8,
        label="absent",
        chamber_temperature_c=(110.0 - 32.0) * 5.0 / 9.0,
    )
    seed_source = _SeedSource(events, absent)
    hold = hold_cycle(runner, controller="mpc")
    hold.ctx.learning_trajectory = seed_source

    try:
        hold.setup()
        hold.on_tick(10.0, 110.0, hold.grill.get_output_status())

        assert runner.seeds == [absent]
        assert runner.submitted_temps == [110.0]
        assert "runner:solve" in events
        assert "runner:output" in events
        assert hold._hold_learning is not None
        assert hold._hold_learning.evidence_available is False
    finally:
        hold.teardown(110.0)


def test_estimator_seed_is_deterministic_recursively_immutable_and_digest_shaped() -> None:
    first = _seed_from_smoke((0.1, 0.6, 0.3), theta=20.0)
    second = _seed_from_smoke((0.1, 0.6, 0.3), theta=20.0)

    assert first == second
    assert isinstance(first.delay_states, tuple)
    assert len(first.pre_roll_digest) == 64
    assert set(first.pre_roll_digest) <= set("0123456789abcdef")
    with pytest.raises(FrozenInstanceError):
        first.status = "absent"  # type: ignore[misc]
    with pytest.raises(TypeError):
        cast(Any, first.delay_states)[0] = 0.0


def test_smoke_temperature_is_irrelevant_but_selected_load_changes_seed() -> None:
    loads = (0.1, 0.6, 0.3)
    cool = _seed_from_smoke(
        loads,
        theta=20.0,
        temperatures=(70.0, 71.0, 72.0),
    )
    hot = _seed_from_smoke(
        loads,
        theta=20.0,
        temperatures=(370.0, 371.0, 372.0),
    )
    different_load = _seed_from_smoke(
        (0.1, 0.9, 0.3),
        theta=20.0,
        temperatures=(70.0, 71.0, 72.0),
    )

    assert cool == hot
    assert different_load.delay_states != cool.delay_states
    assert different_load.pre_roll_digest != cool.pre_roll_digest


def test_celsius_and_fahrenheit_hold_anchors_seed_the_same_physical_state() -> None:
    loads = (0.1, 0.6, 0.3)
    celsius = _seed_from_smoke(
        loads,
        theta=20.0,
        temperatures=(100.0, 101.0, 102.0),
        units="C",
        anchor_temperature=110.0,
        measured_temperature_c=110.0,
    )
    fahrenheit = _seed_from_smoke(
        loads,
        theta=20.0,
        temperatures=(212.0, 213.8, 215.6),
        units="F",
        anchor_temperature=230.0,
        measured_temperature_c=110.0,
    )

    assert fahrenheit.delay_states == celsius.delay_states
    assert fahrenheit.chamber_temperature_c == pytest.approx(celsius.chamber_temperature_c)
    assert fahrenheit.disturbance == pytest.approx(celsius.disturbance)
    assert fahrenheit.status == celsius.status == "exact"
