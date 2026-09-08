import threading
from dataclasses import asdict
from queue import Queue
from typing import cast

import pytest

import controller.runtime.runner as runner_module
from common.control_trace import ActuationMode, ControllerType, InhibitReason
from common.learning_trajectory import FrameDeliveryCertainty
from controller.mpc_allocator import allocate
from controller.pid_sp import Controller
from controller.runtime.actuation_delivery import ActuationDeliveryJournal, DeliveredGrillPlatform
from controller.runtime.clock import ManualClock
from controller.runtime.framed_pulse import FramedPulseRuntime, FramedPulseSample, PulseControllerState
from controller.runtime.logic.pulse import PulseResetReason
from controller.runtime.runner import (
    ControllerUpdateResult,
    SyncControllerRunner,
    ThreadedControllerRunner,
    build_runner,
)
from controller.runtime.state import ControllerState
from grillplat.actuator_capabilities import AUGER_TIMING
from tests.characterization.fixtures import base_settings
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.runner import FakeControllerRunner


def test_manual_clock_has_independent_wall_and_elapsed_axes():
    clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=100.0)
    clock.advance(3.0)
    clock.jump_wall(-3600.0)
    clock.sleep(2.0)
    assert clock.wall_time() == 1_799_996_405.0
    assert clock.monotonic() == 105.0


def _hold_delivery(hold_cycle, jump):
    outputs = [
        ControllerUpdateResult(
            cycle_ratio=0.5,
            allocation=allocate(0.5, u_max=1.0, fan_min_pct=0.0, fan_max_pct=100.0, enable_fan=False),
            fan=None,
            input_temperature=225.0,
            revision=revision,
            solve_start_monotonic=100.0 + 2.0 * (revision - 1),
            solve_end_monotonic=100.0 + 2.0 * (revision - 1),
            completed_wall_time=(
                1_800_000_000.0 + 2.0 * (revision - 1) + (jump if 2.0 * (revision - 1) >= 10.0 else 0.0)
            ),
            solve_duration_seconds=0.0,
        )
        for revision in range(1, 100)
    ]
    runner = FakeControllerRunner(period=1.0).script(outputs)
    clock = ManualClock(wall_start=1_799_999_998.0, monotonic_start=98.0)
    hold = hold_cycle(runner, clock=clock)
    hold.setup()
    # Start sampling only once the synchronous controller has an accepted
    # result due; an earlier seed-only frame is correctly noncontinuous.
    clock.advance(2.0)
    for second in range(61):
        if second:
            clock.advance(1.0)
        if second == 10:
            clock.jump_wall(jump)
        hold.on_tick(clock.monotonic(), 225.0, hold.grill.get_output_status())
    observations = list(runner.observations)
    applied = list(runner.applied)
    delivered = hold.state.metrics.get("augerontime", 0.0)
    solves = runner._i
    hold.teardown(225.0)
    return observations, applied, delivered, solves


@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_real_hold_delivery_and_solve_cadence_ignore_wall_jumps(hold_cycle, jump):
    baseline, baseline_applied, baseline_delivery, baseline_solves = _hold_delivery(hold_cycle, 0.0)
    observations, applied, delivered, solves = _hold_delivery(hold_cycle, jump)
    assert len(observations) == len(baseline) == 3
    assert delivered == baseline_delivery == 30.0
    assert solves == baseline_solves
    assert applied == baseline_applied
    for observation, original in zip(observations, baseline, strict=True):
        physical = asdict(observation)
        original_physical = asdict(original)
        for key in ("wall_start_ms", "wall_end_ms"):
            physical.pop(key)
            original_physical.pop(key)
        assert physical == original_physical
        assert observation.frame_end_s - observation.frame_start_s == 20.0
        assert observation.continuous
    assert observations[0].frame_start_s == 100.0
    assert observations[0].wall_start_ms == 1_800_000_000_000
    assert observations[0].wall_end_ms == int((1_800_000_020.0 + jump) * 1000)


@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_hold_factory_pid_sp_shares_injected_frame_and_predictor_axis(hold_cycle, monkeypatch, jump):
    def run(wall_jump):
        clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=100.0)
        hold = hold_cycle(None, clock=clock)
        monkeypatch.setattr(runner_module, "build_runner", build_runner)
        hold.setup()
        runner = hold._runner
        assert isinstance(runner, SyncControllerRunner)
        core = runner._core._core
        assert core.predictor.trust({"K": 100.0, "tau": 100.0, "theta": 20.0})
        frames = []
        solves = []
        observe_frame = core.observe_frame
        latest = runner.latest

        def capture_frame(observation):
            frames.append(observation)
            return observe_frame(observation)

        def capture_solve():
            result = latest()
            solves.append(result)
            return result

        monkeypatch.setattr(core, "observe_frame", capture_frame)
        monkeypatch.setattr(runner, "latest", capture_solve)
        try:
            for second in range(101):
                if second:
                    clock.advance(1.0)
                if second == 50:
                    clock.jump_wall(wall_jump)
                hold.on_tick(clock.monotonic(), 225.0 + second / 100.0, hold.grill.get_output_status())
            assert frames
            assert solves
            assert all(100.0 <= frame.frame_start_s < frame.frame_end_s <= 200.0 for frame in frames)
            assert all(
                result.solve_start_monotonic == result.solve_end_monotonic
                and 100.0 <= result.solve_end_monotonic <= 200.0
                for result in solves
            )
            assert core.last_update == solves[-1].solve_end_monotonic
            assert solves[-1].diagnostics.previous_update_time == solves[-2].solve_end_monotonic
            physical_frames = [
                {key: value for key, value in asdict(frame).items() if key not in {"wall_start_ms", "wall_end_ms"}}
                for frame in frames
            ]
            physical_solves = [
                (result.cycle_ratio, result.solve_start_monotonic, result.solve_end_monotonic, result.diagnostics)
                for result in solves
            ]
            return physical_frames, physical_solves, hold.state.metrics.get("augerontime", 0.0), frames, solves
        finally:
            hold.teardown(226.0)

    baseline = run(0.0)
    changed = run(jump)
    assert changed[:3] == baseline[:3]
    assert changed[3][-1].wall_end_ms - baseline[3][-1].wall_end_ms == int(jump * 1000)
    assert changed[4][-1].completed_wall_time - baseline[4][-1].completed_wall_time == jump


@pytest.mark.parametrize("fallback", [False, True])
def test_factory_and_fallback_reconfigure_keep_pid_sp_clock_sources(monkeypatch, fallback):
    clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=100.0)
    settings = base_settings()
    settings["controller"]["selected"] = "missing-clock-test-controller" if fallback else "pid_sp"
    monkeypatch.setattr(runner_module, "_raise_banner", lambda *args, **kwargs: None)
    runner, status = build_runner(
        settings,
        {"primary_setpoint": 225.0},
        monotonic_clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    assert status == "Active"
    assert isinstance(runner, SyncControllerRunner)
    try:
        assert runner.controller_type() is (ControllerType.PID if fallback else ControllerType.PID_SP)
        runner.submit(225.0)
        first = runner.latest()
        assert first.solve_start_monotonic == first.solve_end_monotonic == 100.0
        assert first.completed_wall_time == clock.wall_time()
        clock.advance(20.0)
        clock.jump_wall(-3600.0)
        settings["controller"]["selected"] = "pid_sp"
        assert runner.reconfigure(settings, {"primary_setpoint": 230.0}) == "Active"
        runner.submit(226.0)
        replacement = runner.latest()
        assert replacement.solve_start_monotonic == replacement.solve_end_monotonic == 120.0
        assert replacement.completed_wall_time == clock.wall_time()
        assert replacement.diagnostics.previous_update_time == 120.0
        runner.set_target(235.0)
        clock.advance(20.0)
        runner.submit(227.0)
        subsequent = runner.latest()
        assert subsequent.diagnostics.previous_update_time == 120.0
        assert subsequent.diagnostics.observed_dt_seconds == 20.0
        assert subsequent.solve_end_monotonic == 140.0
    finally:
        runner.stop()


def test_threaded_factory_reconfigure_retains_pid_sp_elapsed_and_wall_clocks(monkeypatch):
    clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=100.0)
    settings = base_settings()
    settings["controller"]["selected"] = "pid_sp"
    waiting = Queue()
    release = threading.Event()
    stopping = threading.Event()

    def wait_for_period(_period):
        waiting.put(None)
        if not stopping.is_set():
            assert release.wait(5.0)
            release.clear()

    def threaded(core, **kwargs):
        return ThreadedControllerRunner(core, wait_for_period=wait_for_period, **kwargs)

    monkeypatch.setattr(Controller, "wants_async", lambda self: True)
    monkeypatch.setattr(runner_module, "ThreadedControllerRunner", threaded)
    runner, status = build_runner(
        settings,
        {"primary_setpoint": 225.0},
        monotonic_clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    assert status == "Active"
    assert isinstance(runner, ThreadedControllerRunner)
    try:
        waiting.get(timeout=5.0)
        runner.submit(225.0)
        release.set()
        waiting.get(timeout=5.0)
        first = runner.latest()
        assert first.solve_start_monotonic == first.solve_end_monotonic == 100.0
        assert first.completed_wall_time == clock.wall_time()
        clock.advance(20.0)
        clock.jump_wall(3600.0)
        assert runner.reconfigure(settings, {"primary_setpoint": 230.0}) == "Active"
        runner.submit(226.0)
        release.set()
        waiting.get(timeout=5.0)
        replacement = runner.latest()
        assert runner.configuration_revision() == 1
        assert replacement.solve_start_monotonic == replacement.solve_end_monotonic == 120.0
        assert replacement.completed_wall_time == clock.wall_time()
        assert replacement.diagnostics.previous_update_time == 120.0
        clock.advance(20.0)
        runner.submit(227.0)
        release.set()
        waiting.get(timeout=5.0)
        subsequent = runner.latest()
        assert subsequent.diagnostics.previous_update_time == 120.0
        assert subsequent.diagnostics.observed_dt_seconds == 20.0
        assert subsequent.solve_end_monotonic == 140.0
    finally:
        stopping.set()
        release.set()
        runner.stop()


def _pulse(clock):
    state = cast(PulseControllerState, ControllerState())
    runtime = FramedPulseRuntime(wall_clock_ms=lambda: int(clock.wall_time() * 1000))
    runtime.configure(ActuationMode.FRAMED_PULSE, controller=state, timing=AUGER_TIMING, now=clock.monotonic())
    state.pulse_result_revision = 1
    state.pulse_requested_duty = 0.5
    state.pulse_combustion_load = 0.5
    state.pulse_baseline_combustion_load = 0.5
    sample = FramedPulseSample(225.0, 225.0, 20.0, "F", 0)
    runtime.advance(clock.monotonic(), False, sample=sample)
    return runtime, sample


def test_true_monotonic_gap_still_rejects_learning():
    clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=100.0)
    runtime, sample = _pulse(clock)
    clock.advance(60.0)
    result = runtime.advance(clock.monotonic(), True, sample=sample)
    assert len(result.completions) == 3
    assert all(
        completion.observation is not None and not completion.observation.continuous
        for completion in result.completions
    )
    with pytest.raises(ValueError, match="monotone"):
        runtime.advance(159.0, False, sample=sample)


@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_pulse_reset_reports_real_wall_end_and_physical_duration(jump):
    clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=100.0)
    runtime, sample = _pulse(clock)
    clock.advance(5.0)
    clock.jump_wall(jump)
    result = runtime.reset(
        PulseResetReason.SAFETY,
        clock.monotonic(),
        InhibitReason.SAFETY,
        actual_auger_on=True,
        sample=sample,
        terminal_feedback=True,
    )
    completion = result.completions[0]
    assert completion.frame.nominal_start_s == 100.0
    assert completion.frame.ended_at_s == 105.0
    assert completion.wall_start_ms == 1_800_000_000_000
    assert completion.wall_end_ms == int((1_800_000_005.0 + jump) * 1000)
    assert completion.applied is not None
    assert completion.observation is not None
    assert completion.applied.timestamp == 105.0
    assert not completion.observation.continuous


@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_pid_sp_update_target_and_predictor_use_frame_axis(jump):
    def run(wall_jump):
        clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=100.0)
        core = Controller(
            {"PB": 60.0, "Ti": 180.0, "Td": 45.0},
            "F",
            {},
            monotonic_clock=clock.monotonic,
            clock_ms=lambda: int(clock.wall_time() * 1000),
        )
        assert core.predictor.trust({"K": 100.0, "tau": 100.0, "theta": 20.0})
        core.set_target(225.0)
        runtime, sample = _pulse(clock)
        outputs = [core.update(225.0)]
        actual_on = True
        for second in range(1, 41):
            clock.advance(1.0)
            if second == 10:
                clock.jump_wall(wall_jump)
            result = runtime.advance(clock.monotonic(), actual_on, sample=sample)
            actual_on = result.decision.command_on
            for completion in result.completions:
                assert completion.observation is not None
                core.observe_frame(completion.observation)
            if second % 20 == 0:
                outputs.append(core.update(226.0))
        diagnostics = core.trace_diagnostics()
        assert diagnostics is not None
        core.set_target(230.0)
        return outputs, diagnostics, core.last_update, core.last_set_time, core.identifier.status()

    baseline = run(0.0)
    changed = run(jump)
    assert changed == baseline
    assert changed[1].observed_dt_seconds == 20.0
    assert changed[1].previous_update_time == 120.0
    assert changed[2:4] == (140.0, 140.0)
    assert changed[1].predicted_temperature != 226.0


@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_observed_delivery_edges_remain_exact_across_wall_jump(jump):
    clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=100.0)
    journal = ActuationDeliveryJournal(
        monotonic_clock=lambda: int(clock.monotonic() * 1000),
        wall_clock=lambda: int(clock.wall_time() * 1000),
    )
    grill = DeliveredGrillPlatform(FakeGrillPlatform(dc_fan=True), journal=journal, readback_authoritative=True)
    grill.fan_on()
    grill.auger_on()
    clock.advance(5.0)
    clock.jump_wall(jump)
    grill.auger_off()
    clock.advance(5.0)
    delivery = journal.integrate(100_000, 110_000)
    assert delivery.auger_on_seconds == 5.0
    assert delivery.fan_on_seconds == 10.0
    assert delivery.auger_certainty is FrameDeliveryCertainty.EXACT
    assert delivery.fan_certainty is FrameDeliveryCertainty.EXACT
    assert journal.edges[-1].monotonic_ms == 105_000
    assert journal.edges[-1].wall_ms == int((1_800_000_005.0 + jump) * 1000)
