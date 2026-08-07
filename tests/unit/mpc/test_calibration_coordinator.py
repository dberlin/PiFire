"""Behavioral contract for the pure guarded calibration coordinator."""

from dataclasses import FrozenInstanceError, replace
import math

import pytest

from controller.linear_mpc.calibration import (
    CalibrationCommand,
    CalibrationConfig,
    CalibrationCoordinator,
    CalibrationRuntimeContext,
)


CENTERS = tuple((fahrenheit - 32.0) * 5.0 / 9.0 for fahrenheit in (225.0, 325.0, 425.0))


def context(**changes):
    values = dict(
        now_s=0.0,
        temp_c=CENTERS[0],
        target_c=CENTERS[0],
        baseline_q=0.50,
        realized_q=0.50,
        safety_ceiling_c=260.0,
        allocator_headroom=0.05,
        error_rate_headroom=0.05,
        capability_headroom=0.05,
        saturation_headroom=0.05,
        rank_progress=1.0,
        coverage_progress=1.0,
    )
    values.update(changes)
    return CalibrationRuntimeContext(**values)


def command(**changes):
    values = dict(command_revision=7, maximum_temperature_c=240.0, seed=17)
    values.update(changes)
    return CalibrationCommand(**values)


def start(coordinator=None, **changes):
    coordinator = coordinator or CalibrationCoordinator()
    decision = coordinator.start(command(), context(**changes))
    assert decision.active
    return coordinator, decision


def advance_stage(coordinator, decision, *, stage_context=None):
    current = decision
    for frame in range(38):
        runtime = (
            context(now_s=frame + 1.0, realized_q=0.50 + current.probe_q)
            if stage_context is None
            else replace(
                stage_context,
                now_s=stage_context.now_s + frame,
                realized_q=stage_context.baseline_q + current.probe_q,
            )
        )
        current = coordinator.advance(runtime)
    return current


def test_configuration_is_frozen_slotted_and_converts_three_fahrenheit_bands():
    config = CalibrationConfig()
    assert config.band_centers_c == pytest.approx(CENTERS)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        config.max_probe_q = 0.1
    with pytest.raises(ValueError):
        CalibrationConfig(max_probe_q=math.inf)
    with pytest.raises(ValueError):
        CalibrationConfig(band_centers_c=(CENTERS[0], math.nan, CENTERS[2]))


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("lid_open", True, "lid_open"),
        ("manual_mode", True, "manual_mode"),
        ("manual_output", True, "manual_output"),
        ("safety_inhibited", True, "safety_inhibited"),
        ("temperature_guard", True, "temperature_guard"),
        ("probe_valid", False, "invalid_probe"),
        ("stale_result", True, "stale_result"),
        ("skipped_frame", True, "skipped_frame"),
        ("reset_frame", True, "reset_frame"),
        ("continuous", False, "discontinuity"),
        ("actuation_known", False, "unknown_actuation"),
        ("fallback", True, "fallback"),
        ("allocator_headroom", 0.0, "inadequate_headroom"),
        ("error_rate_headroom", 0.0, "inadequate_headroom"),
        ("capability_headroom", 0.0, "inadequate_headroom"),
        ("saturation_headroom", 0.0, "inadequate_headroom"),
    ],
)
def test_start_rejects_each_operator_and_evidence_precondition(field, value, reason):
    decision = CalibrationCoordinator().start(command(), context(**{field: value}))
    assert not decision.active
    assert decision.probe_q == 0.0
    assert decision.events[-1].kind == "start_rejected"
    assert reason in decision.events[-1].reasons


def test_start_rejects_maximum_at_or_above_safety_ceiling():
    decision = CalibrationCoordinator().start(command(maximum_temperature_c=260.0), context())
    assert not decision.active
    assert decision.probe_q == 0.0
    assert "safety_ceiling" in decision.events[-1].reasons


def test_start_begins_low_stage_and_emits_audit_events():
    _, decision = start()
    assert decision.stage == "low"
    assert [event.kind for event in decision.events[-2:]] == ["start_accepted", "stage_started"]
    assert abs(decision.probe_q) <= 0.05


def test_seed_changes_only_pair_order_and_initial_sign_not_zero_sum_dwell_multiset():
    first = CalibrationCoordinator()
    one = first.start(command(seed=1), context())
    second = CalibrationCoordinator()
    two = second.start(command(seed=2), context())
    assert one.probe_q != 0.0 and two.probe_q != 0.0
    assert first.snapshot()["dwell_counts"] == second.snapshot()["dwell_counts"] == (2, 3, 5, 4, 3, 2)
    assert sum(first.snapshot()["signed_dwell_plan"]) == pytest.approx(0.0)
    assert sum(second.snapshot()["signed_dwell_plan"]) == pytest.approx(0.0)
    assert sorted(map(abs, first.snapshot()["signed_dwell_plan"])) == sorted(map(abs, second.snapshot()["signed_dwell_plan"]))


def test_probes_are_bounded_and_grey_box_overshoot_cancels_without_a_challenger():
    seen = []

    def prediction(baseline_q, probe_q, runtime):
        seen.append((baseline_q, probe_q, runtime.now_s))
        return runtime.safety_ceiling_c

    coordinator = CalibrationCoordinator(predict_max_c=prediction)
    decision = coordinator.start(command(), context())
    assert decision.probe_q == 0.0
    assert "overshoot_prediction" in decision.events[-1].reasons
    assert seen


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("lid_open", True, "lid_open"),
        ("manual_mode", True, "manual_mode"),
        ("manual_output", True, "manual_output"),
        ("safety_inhibited", True, "safety_inhibited"),
        ("temperature_guard", True, "temperature_guard"),
        ("probe_valid", False, "invalid_probe"),
        ("stale_result", True, "stale_result"),
        ("skipped_frame", True, "skipped_frame"),
        ("reset_frame", True, "reset_frame"),
        ("continuous", False, "discontinuity"),
        ("actuation_known", False, "unknown_actuation"),
        ("fallback", True, "fallback"),
        ("allocator_headroom", 0.0, "inadequate_headroom"),
        ("error_rate_headroom", 0.0, "inadequate_headroom"),
        ("capability_headroom", 0.0, "inadequate_headroom"),
        ("saturation_headroom", 0.0, "inadequate_headroom"),
    ],
)
def test_every_advance_cancellation_zeroes_the_probe_immediately(field, value, reason):
    coordinator, decision = start()
    assert decision.probe_q
    decision = coordinator.advance(context(now_s=1.0, **{field: value}))
    assert not decision.active
    assert decision.probe_q == 0.0
    assert decision.events[-1].kind == "safety_aborted"
    assert reason in decision.events[-1].reasons


def test_pause_and_resume_emit_auditable_zero_probe_decisions():
    coordinator, _ = start()
    paused = coordinator.pause()
    assert paused.active and paused.probe_q == 0.0
    assert paused.events[-1].kind == "paused"
    resumed = coordinator.resume(context(now_s=1.0))
    assert resumed.active and resumed.probe_q != 0.0
    assert resumed.events[-1].kind == "resumed"


def test_stop_and_timeout_return_zero_probe_immediately_and_timeout_never_extends():
    coordinator, _ = start()
    stopped = coordinator.stop(context(now_s=1.0))
    assert not stopped.active and stopped.probe_q == 0.0
    assert stopped.events[-1].kind == "stopped"

    coordinator, _ = start()
    timed_out = coordinator.advance(context(now_s=3600.0))
    assert not timed_out.active and timed_out.probe_q == 0.0
    assert timed_out.events[-1].kind == "stage_timeout"
    later = coordinator.advance(context(now_s=7200.0))
    assert later == timed_out


def test_clipped_realization_requests_safe_compensation_and_tracks_realized_signed_sum():
    coordinator, decision = start()
    clipped = coordinator.advance(context(now_s=1.0, realized_q=0.50 - decision.probe_q))
    assert clipped.progress.realized_probe_sum == pytest.approx(-decision.probe_q)
    assert abs(clipped.probe_q) <= 0.05
    assert clipped.events[-1].kind == "probe_changed"


def test_stage_needs_all_evidence_gates_before_transitioning_upward():
    coordinator, decision = start()
    insufficient = advance_stage(
        coordinator,
        decision,
        stage_context=context(now_s=1.0, realized_q=0.5, rank_progress=0.0, coverage_progress=0.0),
    )
    assert insufficient.active and insufficient.stage == "low"
    assert insufficient.events[-1].kind == "incomplete"

    coordinator, decision = start()
    low_done = advance_stage(coordinator, decision)
    assert low_done.active and low_done.stage == "middle"
    assert low_done.events[-2].kind == "stage_completed"
    assert low_done.events[-1].kind == "stage_started"
    middle_done = advance_stage(coordinator, low_done, stage_context=context(now_s=100.0))
    assert middle_done.active and middle_done.stage == "high"
    all_done = advance_stage(coordinator, middle_done, stage_context=context(now_s=200.0))
    assert not all_done.active and all_done.events[-1].kind == "completed"


def test_progress_requires_30_observations_three_levels_variance_signs_rank_coverage_continuity_and_zero_mean():
    coordinator, decision = start()
    current = decision
    for index in range(38):
        probe = current.probe_q
        current = coordinator.advance(
            context(
                now_s=index + 1.0,
                realized_q=0.50 + probe,
                rank_progress=1.0,
                coverage_progress=1.0,
            )
        )
    progress = current.progress
    assert progress.eligible_observations >= 30
    assert progress.realized_levels >= 3
    assert progress.realized_variance >= 0.001
    assert progress.positive_observations >= 6
    assert progress.negative_observations >= 6
    assert progress.rank_progress >= 1.0 and progress.coverage_progress >= 1.0
    assert progress.continuous
    assert abs(progress.realized_probe_sum) <= 0.05


def test_snapshot_restore_is_deterministic_and_immutable():
    coordinator, _ = start()
    coordinator.advance(context(now_s=1.0))
    snapshot = coordinator.snapshot()
    restored = CalibrationCoordinator.from_snapshot(snapshot)
    next_context = context(now_s=2.0)
    assert restored.advance(next_context) == coordinator.advance(next_context)
    with pytest.raises(TypeError):
        snapshot["stage"] = "tampered"


def test_invalid_command_and_runtime_values_are_rejected():
    with pytest.raises(ValueError):
        CalibrationCommand(command_revision=-1, maximum_temperature_c=240.0)
    with pytest.raises(ValueError):
        context(now_s=math.nan)
    with pytest.raises(ValueError):
        context(baseline_q=1.1)
