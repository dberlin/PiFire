import json
import math
import random
from pathlib import Path

import pytest

from controller.runtime.logic.pulse import PulseReason, PulseResetReason, PulseScheduler
from grillplat.actuator_capabilities import AUGER_TIMING, AugerTiming


ALLOCATOR_FIXED_LOADS = (0.008888888888888889, 0.02277777777777778, 0.045, 0.09, 0.225, 0.45, 0.675, 0.9)


def _advance(scheduler, request, at_s, actual_on=False):
    return scheduler.advance(request=request, at_s=at_s, actual_auger_on=actual_on)


def _frames(scheduler, request, count, actual_on=False):
    return [_advance(scheduler, request, frame * 20, actual_on) for frame in range(count)]


def test_auger_timing_defaults_are_fixed_and_frozen():
    timing = AugerTiming()
    assert timing.pulse_s == 2
    assert timing.frame_s == 20
    with pytest.raises(AttributeError):
        timing.pulse_s = 1


def test_fake_platform_returns_shared_auger_timing():
    from tests.fakes.grill import FakeGrillPlatform

    assert FakeGrillPlatform().auger_timing() is AUGER_TIMING


@pytest.mark.parametrize("pulse_s, frame_s", [(0, 20), (2, 0), (-2, 20), (3, 20), (2.0, 20)])
def test_auger_timing_rejects_nonpositive_or_indivisible_values(pulse_s, frame_s):
    with pytest.raises(ValueError):
        AugerTiming(pulse_s=pulse_s, frame_s=frame_s)


def test_zero_duty_never_commands_auger_on():
    decisions = _frames(PulseScheduler(), 0.0, 4)
    assert [decision.scheduled_on_s for decision in decisions] == [0, 0, 0, 0]
    assert all(not decision.command_on for decision in decisions)


def test_full_duty_uses_the_entire_frame_in_two_second_quanta():
    decisions = _frames(PulseScheduler(), 1.0, 3)
    assert [decision.scheduled_on_s for decision in decisions] == [20, 20, 20]
    assert all(decision.command_on for decision in decisions)


def test_ten_percent_mean_schedules_two_seconds_on_then_eighteen_off():
    scheduler = PulseScheduler()
    start = _advance(scheduler, 0.1, 0.0, actual_on=False)
    during_on = _advance(scheduler, 0.1, 1.0, actual_on=True)
    at_off = _advance(scheduler, 0.9, 2.0, actual_on=True)
    assert start.scheduled_on_s == 2
    assert start.command_on is True
    assert during_on.command_on is True
    assert at_off.command_on is False
    assert at_off.latched_request == 0.1


def test_subframe_duty_carries_credit_until_a_two_second_quantum_is_due():
    scheduler = PulseScheduler()
    decisions = _frames(scheduler, 0.05, 4)
    assert [decision.scheduled_on_s for decision in decisions] == [0, 2, 0, 2]
    assert [decision.credit_s for decision in decisions] == [1.0, 0.0, 1.0, 0.0]


def test_randomized_requests_conserve_scheduled_time_within_one_pulse():
    randomizer = random.Random(20260804)
    requests = [randomizer.random() for _ in range(200)]
    scheduler = PulseScheduler()
    decisions = [_advance(scheduler, request, index * 20.0) for index, request in enumerate(requests)]
    requested_seconds = sum(request * 20.0 for request in requests)
    scheduled_seconds = sum(decision.scheduled_on_s for decision in decisions)
    assert abs(requested_seconds - scheduled_seconds) < scheduler.timing.pulse_s


def test_each_frame_uses_contiguous_quanta_and_at_most_two_transitions():
    scheduler = PulseScheduler()
    first = _advance(scheduler, 0.3, 0.0, actual_on=False)
    still_on = _advance(scheduler, 0.3, 5.0, actual_on=True)
    off = _advance(scheduler, 0.3, 6.0, actual_on=True)
    assert first.scheduled_on_s == 6
    assert first.command_on is True
    assert still_on.command_on is True
    assert off.command_on is False
    assert [transition.command_on for transition in (first.transition, off.transition)] == [True, False]
    assert 0 <= first.scheduled_on_s <= scheduler.timing.frame_s
    assert first.scheduled_on_s % scheduler.timing.pulse_s == 0


def test_steady_scheduler_transitions_match_the_two_per_frame_envelope():
    scheduler = PulseScheduler()
    transitions = []
    for frame in range(180):
        transitions.append(_advance(scheduler, 0.5, frame * 20.0, actual_on=False).transition)
        transitions.append(_advance(scheduler, 0.5, frame * 20.0 + 10.0, actual_on=True).transition)
    assert sum(transition is not None for transition in transitions) == 360


def test_request_is_latched_until_the_next_frame_boundary():
    scheduler = PulseScheduler()
    initial = _advance(scheduler, 0.1, 0.0)
    mid_frame = _advance(scheduler, 0.9, 10.0)
    next_frame = _advance(scheduler, 0.9, 20.0)
    assert initial.latched_request == 0.1
    assert mid_frame.latched_request == 0.1
    assert mid_frame.scheduled_on_s == 2
    assert next_frame.latched_request == 0.9
    assert next_frame.scheduled_on_s == 18


def test_multiple_missed_frames_are_recorded_and_discard_credit():
    scheduler = PulseScheduler()
    _advance(scheduler, 0.05, 0.0)
    skipped = _advance(scheduler, 0.05, 45.0)
    assert skipped.reason is PulseReason.FRAME_SKIPPED
    assert [(frame.complete, frame.skipped) for frame in skipped.completed_frames] == [(True, False), (False, True)]
    assert skipped.frame_start_s == 40.0
    assert skipped.scheduled_on_s == 0


def test_skipped_on_frame_reports_physical_assumed_delivery_as_scheduled():
    scheduler = PulseScheduler()
    _advance(scheduler, 0.05, 0.0, actual_on=True)
    decision = _advance(scheduler, 0.05, 45.0, actual_on=True)

    skipped = decision.completed_frames[1]
    assert skipped.skipped is True
    assert skipped.scheduled_on_s == 20
    assert skipped.delivered_on_s == 20.0


@pytest.mark.parametrize("at_s", [19.999, 20.0, 20.001])
def test_nominal_boundary_alignment_is_stable_across_loop_jitter(at_s):
    scheduler = PulseScheduler()
    _advance(scheduler, 0.05, 0.0)
    decision = _advance(scheduler, 0.05, at_s)
    if at_s < 20:
        assert decision.completed_frames == ()
        assert decision.frame_start_s == 0.0
    else:
        assert decision.reason is PulseReason.FRAME_STARTED
        assert decision.frame_start_s == 20.0
        assert decision.completed_frames[0].nominal_start_s == 0.0
        assert decision.completed_frames[0].nominal_end_s == 20.0


def test_jittered_boundaries_retain_credit_and_long_window_heat():
    scheduler = PulseScheduler()
    decisions = [_advance(scheduler, 0.05, 0.0)]
    decisions.extend(_advance(scheduler, 0.05, frame * 20.0 + 0.001) for frame in range(1, 5))
    assert [decision.scheduled_on_s for decision in decisions] == [0, 2, 0, 2, 0]
    assert all(decision.frame_start_s == index * 20.0 for index, decision in enumerate(decisions))


def test_completed_frame_exposes_nominal_schedule_and_observed_delivery():
    scheduler = PulseScheduler()
    _advance(scheduler, 0.1, 0.0, actual_on=False)
    _advance(scheduler, 0.1, 1.0, actual_on=True)
    _advance(scheduler, 0.1, 2.0, actual_on=False)
    completed = _advance(scheduler, 0.1, 20.0, actual_on=False).completed_frames[0]
    assert completed.nominal_start_s == 0.0
    assert completed.nominal_end_s == 20.0
    assert completed.complete is True
    assert completed.skipped is False
    assert completed.latched_request == 0.1
    assert completed.scheduled_on_s == 2
    assert completed.delivered_on_s == 1.0
    assert completed.credit_before_s == 0.0
    assert completed.credit_after_s == 0.0
    assert completed.actual_start_on is False
    assert completed.actual_end_on is False
    assert completed.observed_transition_count == 2


def test_completed_frame_records_active_start_and_end():
    scheduler = PulseScheduler()
    _advance(scheduler, 1.0, 0.0, actual_on=False)
    _advance(scheduler, 1.0, 10.0, actual_on=True)
    completed = _advance(scheduler, 1.0, 20.0, actual_on=True).completed_frames[0]
    assert completed.delivered_on_s == 10.0
    assert completed.actual_start_on is False
    assert completed.actual_end_on is True
    assert completed.observed_transition_count == 1


def test_completed_frame_records_observed_active_state_at_both_ends():
    scheduler = PulseScheduler()
    _advance(scheduler, 0.1, 0.0, actual_on=True)
    completed = _advance(scheduler, 0.1, 20.0, actual_on=True).completed_frames[0]
    assert completed.actual_start_on is True
    assert completed.actual_end_on is True
    assert completed.delivered_on_s == 20.0
    assert completed.observed_transition_count == 0


def test_repeated_command_corrections_do_not_create_observed_edges():
    scheduler = PulseScheduler()
    first = _advance(scheduler, 0.1, 0.0, actual_on=False)
    lag_one = _advance(scheduler, 0.1, 1.0, actual_on=False)
    lag_two = _advance(scheduler, 0.1, 1.5, actual_on=False)
    completed = _advance(scheduler, 0.1, 20.0, actual_on=False).completed_frames[0]
    assert [decision.transition is not None for decision in (first, lag_one, lag_two)] == [True, True, True]
    assert completed.observed_transition_count == 0


def test_reset_returns_the_interrupted_frame_as_incomplete():
    scheduler = PulseScheduler()
    _advance(scheduler, 0.1, 0.0, actual_on=False)
    _advance(scheduler, 0.1, 1.0, actual_on=True)
    interrupted = scheduler.reset(PulseResetReason.SAFETY)
    assert interrupted is not None
    assert interrupted.complete is False
    assert interrupted.skipped is False
    assert interrupted.ended_at_s == 1.0
    assert interrupted.actual_start_on is False
    assert interrupted.actual_end_on is True
    assert interrupted.observed_transition_count == 1


def test_multi_frame_gap_accounts_observed_state_without_replaying_schedule():
    scheduler = PulseScheduler()
    _advance(scheduler, 1.0, 0.0, actual_on=False)
    _advance(scheduler, 1.0, 1.0, actual_on=True)
    decision = _advance(scheduler, 1.0, 65.0, actual_on=True)
    assert [(frame.complete, frame.skipped, frame.delivered_on_s) for frame in decision.completed_frames] == [
        (True, False, 19.0),
        (False, True, 20.0),
        (False, True, 20.0),
    ]
    assert decision.delivered_on_s == 64.0


def test_reset_clears_credit_and_starts_the_next_frame_cleanly():
    scheduler = PulseScheduler()
    _advance(scheduler, 0.05, 0.0)
    scheduler.reset(PulseResetReason.SAFETY)
    restarted = _advance(scheduler, 0.05, 100.0)
    assert restarted.reason is PulseReason.RESET
    assert restarted.reset_reason is PulseResetReason.SAFETY
    assert restarted.scheduled_on_s == 0
    assert restarted.credit_s == 1.0


def test_delivered_accounting_uses_observed_actual_state_not_the_command():
    scheduler = PulseScheduler()
    _advance(scheduler, 0.1, 0.0, actual_on=False)
    measured_on = _advance(scheduler, 0.1, 1.0, actual_on=True)
    measured_off = _advance(scheduler, 0.1, 2.0, actual_on=True)
    assert measured_on.delivered_on_s == 0.0
    assert measured_off.delivered_on_s == 1.0
    assert measured_off.scheduled_on_s == 2


def test_scheduler_caps_each_frame_by_its_explicit_authority():
    scheduler = PulseScheduler(maximum_request=0.8)
    assert _advance(scheduler, 0.8, 0.0).scheduled_on_s == 16
    with pytest.raises(ValueError):
        _advance(scheduler, 0.81, 20.0)


@pytest.mark.parametrize("requested_duty", [math.nan, math.inf, -0.01, 1.01])
def test_invalid_requests_are_rejected(requested_duty):
    with pytest.raises(ValueError):
        _advance(PulseScheduler(), requested_duty, 0.0)


@pytest.mark.parametrize("at_s", [math.nan, math.inf, -math.inf])
def test_nonfinite_timestamps_are_rejected(at_s):
    with pytest.raises(ValueError):
        _advance(PulseScheduler(), 0.1, at_s)


def test_nonmonotone_timestamp_is_rejected_without_mutating_schedule():
    scheduler = PulseScheduler()
    _advance(scheduler, 0.1, 10.0)
    with pytest.raises(ValueError):
        _advance(scheduler, 0.1, 9.0)
    assert _advance(scheduler, 0.1, 12.0).latched_request == 0.1


def test_committed_allocator_has_two_exact_fixed_load_sweeps():
    artifact = Path("docs/superpowers/experiments/_mpc_pulse_allocator.json")
    records = json.loads(artifact.read_text())["open_loop"]
    loads = tuple(record["mean_duty"] for record in records if record["arm"] == "linear_coupled_pulse_1s")
    assert loads == ALLOCATOR_FIXED_LOADS * 2


@pytest.mark.parametrize("requested_duty", ALLOCATOR_FIXED_LOADS)
def test_committed_allocator_fixed_loads_preserve_mean_and_transition_envelope(requested_duty):
    scheduler = PulseScheduler()
    decisions = _frames(scheduler, requested_duty, 180)
    scheduled_seconds = sum(decision.scheduled_on_s for decision in decisions)
    error_s = abs(scheduled_seconds - requested_duty * 180 * 20)
    assert error_s <= math.nextafter(float(scheduler.timing.pulse_s), math.inf)
    assert sum(decision.transition is not None for decision in decisions) <= 360
