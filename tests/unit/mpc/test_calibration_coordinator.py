"""Behavioral contract for the pure guarded calibration coordinator."""

import math
from dataclasses import FrozenInstanceError, replace

import pytest

from controller.model_learning.calibration import (
    CalibrationCommand,
    CalibrationConfig,
    CalibrationCoordinator,
    CalibrationRuntimeContext,
)

CENTERS = tuple((fahrenheit - 32.0) * 5.0 / 9.0 for fahrenheit in (225.0, 325.0, 425.0))


def context(**changes):
    values = {
        "now_s": 0.0,
        "temp_c": CENTERS[0],
        "target_c": CENTERS[0],
        "baseline_q": 0.50,
        "realized_q": 0.50,
        "safety_ceiling_c": 260.0,
        "allocator_headroom": 0.05,
        "error_rate_headroom": 0.05,
        "capability_headroom": 0.05,
        "saturation_headroom": 0.05,
        "rank_progress": 1.0,
        "coverage_progress": 1.0,
    }
    values.update(changes)
    return CalibrationRuntimeContext(**values)


def command(**changes):
    values = {"command_revision": 7, "seed": 17}
    values.update(changes)
    return CalibrationCommand(**values)


def safe_prediction(baseline_q, probe_q, runtime):
    return runtime.temp_c + 1.0


def start(coordinator=None, **changes):
    coordinator = coordinator or CalibrationCoordinator(predict_max_c=safe_prediction)
    decision = coordinator.start(command(), context(**changes))
    assert decision.active
    return coordinator, decision


def advance_stage(coordinator, decision, *, stage_context=None):
    current = decision
    for frame in range(44):
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


def test_start_begins_low_stage_and_emits_audit_events():
    _, decision = start()
    assert decision.stage == "low"
    assert [event.kind for event in decision.events[-2:]] == ["start_accepted", "stage_started"]
    assert abs(decision.probe_q) <= 0.05


def test_decision_carries_measured_completed_stage_names() -> None:
    coordinator, decision = start()

    coast = advance_stage(coordinator, decision)

    assert coast.stage == "coast"
    assert coast.completed_stages == ("low",)


def test_start_fails_closed_when_no_active_grey_box_prediction_is_available():
    decision = CalibrationCoordinator().start(command(), context())
    assert not decision.active
    assert decision.probe_q == 0.0
    assert decision.events[-1].reasons == ("prediction_unavailable",)


def test_seed_changes_only_pair_order_and_initial_sign_not_zero_sum_dwell_multiset():
    first = CalibrationCoordinator(predict_max_c=safe_prediction)
    one = first.start(command(seed=1), context())
    second = CalibrationCoordinator(predict_max_c=safe_prediction)
    two = second.start(command(seed=2), context())
    assert one.probe_q != 0.0 and two.probe_q != 0.0
    assert first.snapshot()["dwell_counts"] == second.snapshot()["dwell_counts"] == (2, 3, 5, 4, 3, 2)
    assert sum(first.snapshot()["signed_dwell_plan"]) == pytest.approx(0.0)
    assert sum(second.snapshot()["signed_dwell_plan"]) == pytest.approx(0.0)
    assert sorted(map(abs, first.snapshot()["signed_dwell_plan"])) == sorted(
        map(abs, second.snapshot()["signed_dwell_plan"])
    )


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


def test_stage_completion_coasts_to_each_upward_band_before_starting_the_next_excitation():
    coordinator, decision = start()
    coast = advance_stage(coordinator, decision)
    assert coast.active and coast.stage == "coast"
    assert coast.probe_q == 0.0
    assert coast.target_c == pytest.approx(CENTERS[1])
    assert coast.progress.eligible_observations == 0
    assert coast.events[-1].kind == "stage_completed"

    waiting = coordinator.advance(context(now_s=45.0))
    assert waiting.active and waiting.stage == "coast"
    assert waiting.probe_q == 0.0
    assert all(event.kind != "stage_started" for event in waiting.events)

    middle = coordinator.advance(context(now_s=46.0, temp_c=CENTERS[1], target_c=CENTERS[1]))
    assert middle.active and middle.stage == "middle"
    assert middle.probe_q != 0.0
    assert middle.events[-1].kind == "stage_started"

    coast = advance_stage(
        coordinator,
        middle,
        stage_context=context(now_s=47.0, temp_c=CENTERS[1], target_c=CENTERS[1]),
    )
    assert coast.active and coast.stage == "coast" and coast.target_c == pytest.approx(CENTERS[2])

    high = coordinator.advance(context(now_s=100.0, temp_c=CENTERS[2], target_c=CENTERS[2]))
    assert high.active and high.stage == "high" and high.probe_q != 0.0


def test_progress_requires_30_observations_three_levels_variance_signs_rank_coverage_continuity_and_zero_mean():
    coordinator, decision = start()
    current = decision
    for index in range(44):
        probe = current.probe_q
        current = coordinator.advance(
            context(
                now_s=index + 1.0,
                realized_q=0.50 + probe,
                rank_progress=1.0,
                coverage_progress=1.0,
            )
        )
    progress = coordinator.snapshot()["completed_stages"][-1]
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
    restored = CalibrationCoordinator.from_snapshot(snapshot, safe_prediction)
    next_context = context(now_s=2.0)
    assert restored.advance(next_context) == coordinator.advance(next_context)
    with pytest.raises(TypeError):
        snapshot["stage"] = "tampered"


def test_invalid_command_and_runtime_values_are_rejected():
    with pytest.raises(ValueError):
        CalibrationCommand(command_revision=-1)
    with pytest.raises(ValueError):
        context(now_s=math.nan)
    with pytest.raises(ValueError):
        context(baseline_q=1.1)


def test_later_predictor_exception_aborts_active_probe_without_leaking_its_prior_value():
    calls = 0

    def failing_after_start(baseline_q, probe_q, runtime):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("grey box failed")
        return runtime.temp_c + 1.0

    coordinator, decision = start(CalibrationCoordinator(predict_max_c=failing_after_start))
    assert decision.probe_q != 0.0
    aborted = coordinator.advance(context(now_s=1.0, realized_q=0.5 + decision.probe_q))
    assert not aborted.active and aborted.probe_q == 0.0
    assert aborted.events[-1].kind == "safety_aborted"
    assert aborted.events[-1].reasons == ("prediction_invalid",)


def test_completed_history_survives_terminal_coast_stop_and_snapshot_restore():
    coordinator, decision = start()
    coast = advance_stage(coordinator, decision)
    completed = coordinator.snapshot()["completed_stages"]
    assert len(completed) == 1
    stopped = coordinator.stop()
    assert stopped.probe_q == 0.0
    snapshot = coordinator.snapshot()
    restored = CalibrationCoordinator.from_snapshot(snapshot, safe_prediction)
    assert restored.snapshot()["completed_stages"] == completed


@pytest.mark.parametrize(
    ("gate", "config_changes", "runtime_changes"),
    [
        ("observations", {"min_stage_observations": 45}, {}),
        ("levels", {"min_realized_levels": 4}, {}),
        ("variance", {"min_realized_variance": 0.01}, {}),
        ("positive", {"min_positive_observations": 100}, {}),
        ("negative", {"min_negative_observations": 100}, {}),
        ("rank", {}, {"rank_progress": 0.0}),
        ("coverage", {}, {"coverage_progress": 0.0}),
    ],
)
def test_each_numeric_ready_gate_below_its_threshold_keeps_the_stage_incomplete(gate, config_changes, runtime_changes):
    coordinator, decision = start(CalibrationCoordinator(CalibrationConfig(**config_changes), safe_prediction))
    result = advance_stage(
        coordinator,
        decision,
        stage_context=context(now_s=1.0, **runtime_changes),
    )
    assert result.active and result.stage == "low", gate
    assert result.events[-1].kind == "incomplete"


def test_continuity_gate_aborts_and_zero_sum_debt_is_safely_compensated_before_completion():
    coordinator, decision = start()
    discontinuous = coordinator.advance(context(now_s=1.0, continuous=False))
    assert not discontinuous.active and discontinuous.probe_q == 0.0

    coordinator, decision = start()
    current = decision
    for index in range(44):
        realized_q = 0.5 + current.probe_q + (0.06 if index == 43 else 0.0)
        current = coordinator.advance(context(now_s=index + 1.0, realized_q=realized_q))
    assert current.active and current.stage == "low"
    assert current.probe_q == pytest.approx(-0.05)
    settled = coordinator.advance(context(now_s=45.0, realized_q=0.5 + current.probe_q))
    assert settled.active and settled.stage == "coast"
    assert abs(settled.events[-1].realized_probe_sum) <= 0.05


def test_final_high_completion_and_terminal_snapshot_preserve_every_completed_stage():
    coordinator, decision = start()
    coast = advance_stage(coordinator, decision)
    middle = coordinator.advance(context(now_s=45.0, temp_c=CENTERS[1], target_c=CENTERS[1]))
    coast = advance_stage(
        coordinator, middle, stage_context=context(now_s=46.0, temp_c=CENTERS[1], target_c=CENTERS[1])
    )
    high = coordinator.advance(context(now_s=100.0, temp_c=CENTERS[2], target_c=CENTERS[2]))
    final = advance_stage(coordinator, high, stage_context=context(now_s=101.0, temp_c=CENTERS[2], target_c=CENTERS[2]))
    assert not final.active and final.events[-1].kind == "completed"
    snapshot = coordinator.snapshot()
    assert len(snapshot["completed_stages"]) == 3
    assert (
        CalibrationCoordinator.from_snapshot(snapshot, safe_prediction).snapshot()["completed_stages"]
        == snapshot["completed_stages"]
    )


def test_rejected_restart_preserves_completed_history_until_an_accepted_start():
    fail = False

    def predictor(baseline_q, probe_q, runtime):
        if fail:
            raise RuntimeError("unavailable")
        return runtime.temp_c + 1.0

    coordinator, decision = start(CalibrationCoordinator(predict_max_c=predictor))
    advance_stage(coordinator, decision)
    history = coordinator.snapshot()["completed_stages"]
    fail = True
    rejected = coordinator.start(command(command_revision=8), context())
    assert not rejected.active and rejected.probe_q == 0.0
    assert coordinator.snapshot()["completed_stages"] == history


def test_coast_predictor_exception_and_timeout_preserve_history_and_restore_terminal_snapshot():
    fail = False

    def predictor(baseline_q, probe_q, runtime):
        if fail:
            raise RuntimeError("unavailable")
        return runtime.temp_c + 1.0

    coordinator, decision = start(CalibrationCoordinator(predict_max_c=predictor))
    advance_stage(coordinator, decision)
    history = coordinator.snapshot()["completed_stages"]
    fail = True
    aborted = coordinator.advance(context(now_s=45.0, temp_c=CENTERS[1], target_c=CENTERS[1]))
    assert not aborted.active and aborted.probe_q == 0.0
    snapshot = coordinator.snapshot()
    assert CalibrationCoordinator.from_snapshot(snapshot, predictor).snapshot()["completed_stages"] == history


def test_snapshot_restored_historical_discontinuity_blocks_only_readiness():
    coordinator, decision = start()
    incomplete = advance_stage(coordinator, decision, stage_context=context(now_s=1.0, rank_progress=0.0))
    snapshot = dict(coordinator.snapshot())
    snapshot["state"] = replace(snapshot["state"], rank_progress=1.0, continuous=False)
    restored = CalibrationCoordinator.from_snapshot(snapshot, safe_prediction)
    result = restored.advance(context(now_s=45.0))
    assert result.active and result.stage == "low"
    assert result.events[-1].kind == "incomplete"
    assert result.events[-1].reasons == ("discontinuity",)


@pytest.mark.parametrize(
    ("predictor", "runtime"),
    [
        (safe_prediction, context(lid_open=True)),
        (None, context()),
        (lambda baseline_q, probe_q, runtime: (_ for _ in ()).throw(RuntimeError()), context()),
        (lambda baseline_q, probe_q, runtime: math.nan, context()),
        (lambda baseline_q, probe_q, runtime: runtime.safety_ceiling_c, context()),
    ],
)
def test_every_rejected_start_preserves_history_then_accepted_start_clears_it(predictor, runtime):
    original, decision = start()
    advance_stage(original, decision)
    snapshot = original.snapshot()
    history = snapshot["completed_stages"]
    rejected = CalibrationCoordinator.from_snapshot(snapshot, predictor)
    decision = rejected.start(command(command_revision=9), runtime)
    assert not decision.active and decision.probe_q == 0.0
    assert rejected.snapshot()["completed_stages"] == history
    accepted = CalibrationCoordinator.from_snapshot(snapshot, safe_prediction)
    assert accepted.start(command(command_revision=10), context()).active
    assert accepted.snapshot()["completed_stages"] == ()


def test_timeout_and_post_completion_safety_abort_terminal_snapshots_round_trip_exactly():
    coordinator, decision = start()
    advance_stage(coordinator, decision)
    coordinator.advance(context(now_s=3644.0))
    timeout_snapshot = coordinator.snapshot()
    assert CalibrationCoordinator.from_snapshot(timeout_snapshot, safe_prediction).snapshot() == timeout_snapshot

    coordinator, decision = start()
    coast = advance_stage(coordinator, decision)
    middle = coordinator.advance(context(now_s=45.0, temp_c=CENTERS[1], target_c=CENTERS[1]))
    coast = advance_stage(
        coordinator, middle, stage_context=context(now_s=46.0, temp_c=CENTERS[1], target_c=CENTERS[1])
    )
    high = coordinator.advance(context(now_s=100.0, temp_c=CENTERS[2], target_c=CENTERS[2]))
    advance_stage(coordinator, high, stage_context=context(now_s=101.0, temp_c=CENTERS[2], target_c=CENTERS[2]))
    coordinator.cancel_probe("post_complete_safety")
    safety_snapshot = coordinator.snapshot()
    assert CalibrationCoordinator.from_snapshot(safety_snapshot, safe_prediction).snapshot() == safety_snapshot


def test_reset_progress_clears_active_progress_and_history_but_stop_retains_them():
    coordinator, decision = start()
    coordinator.advance(context(now_s=1.0, realized_q=0.5 + decision.probe_q))
    stopped = coordinator.stop()

    assert stopped.command_revision == 0
    assert stopped.command_action == "stop"
    assert stopped.progress.eligible_observations == 1

    reset = coordinator.reset_progress()

    assert reset.command_revision == 0
    assert reset.command_action == "reset-progress"
    assert reset.progress == type(reset.progress)()
    assert reset.events[-1].kind == "progress_reset"
    assert coordinator.snapshot()["completed_stages"] == ()


def _starve(coordinator, frames=12, **changes):
    """Advance until a probe finds no room at the operating point.

    The dwell plan's signs are seeded, so which slot first asks to move down is
    not fixed; drive frames until one does.
    """
    current = None
    for frame in range(1, frames + 1):
        current = coordinator.advance(context(now_s=float(frame), baseline_q=0.0, realized_q=0.0, **changes))
        if current.events and current.events[-1].kind == "probe_skipped":
            return current
    return current


def test_a_probe_with_no_room_at_the_operating_point_is_skipped_not_fatal():
    """A hold whose commanded load has fallen to zero leaves a downward probe
    nothing to subtract from. That is a transient fact about the operating
    point, not a safety event, so the stage waits rather than dying."""
    coordinator, _ = start()

    skipped = _starve(coordinator)

    assert skipped.events[-1].kind == "probe_skipped"
    assert skipped.events[-1].reasons == ("no_probe_room",)
    assert skipped.active
    assert skipped.probe_q == 0.0
    assert skipped.stage == "low"


def test_a_skipped_probe_is_retried_at_the_same_schedule_slot_once_room_returns():
    coordinator, _ = start()
    skipped = _starve(coordinator)
    assert skipped.events[-1].kind == "probe_skipped"
    position = coordinator.snapshot()["state"].schedule_position

    resumed = coordinator.advance(context(now_s=99.0, baseline_q=0.50, realized_q=0.50))

    assert resumed.active
    assert resumed.probe_q != 0.0
    assert coordinator.snapshot()["state"].schedule_position == position + 1


def test_start_with_no_room_begins_the_stage_waiting_instead_of_rejecting():
    """The operator asked for a calibration cook. Starting at an operating point
    that cannot take the first probe yet is a wait, not a refusal."""
    coordinator = CalibrationCoordinator(predict_max_c=safe_prediction)

    decision = coordinator.start(command(), context(baseline_q=0.0, realized_q=0.0))

    assert decision.active
    assert decision.stage == "low"
    assert decision.probe_q == 0.0
    assert [event.kind for event in decision.events[-3:]] == ["start_accepted", "stage_started", "probe_skipped"]


def test_explicit_headroom_signals_still_abort_rather_than_waiting():
    """A reported loss of allocator/capability/saturation authority is not the
    same as an operating point with no room, and stays fatal."""
    coordinator, _ = start()

    decision = coordinator.advance(context(now_s=1.0, capability_headroom=0.0))

    assert not decision.active
    assert decision.events[-1].kind == "safety_aborted"
    assert "inadequate_headroom" in decision.events[-1].reasons
