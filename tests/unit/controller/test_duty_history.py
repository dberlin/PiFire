"""Duty is a step function; its integral is exact, not approximated."""

import numpy as np
import pytest

from controller.fopdt_identifier import DutyHistory


def _history(pairs, max_delay=120.0):
    h = DutyHistory(max_delay)
    for t, u in pairs:
        h.record(t, u)
    return h


def test_integral_is_exact_for_a_constant_duty():
    h = _history([(0.0, 0.4)])
    assert h.integral(np.array([0.0, 10.0, 25.0])) == pytest.approx([0.0, 4.0, 10.0])


def test_integral_accumulates_across_segments():
    h = _history([(0.0, 0.4), (10.0, 0.8), (20.0, 0.0)])
    # 0-10 at 0.4 = 4.0; 10-20 at 0.8 = 8.0 -> 12.0; then flat
    assert h.integral(np.array([10.0, 20.0, 30.0])) == pytest.approx([4.0, 12.0, 12.0])


def test_integral_interpolates_inside_a_segment():
    h = _history([(0.0, 0.4), (10.0, 0.8)])
    assert h.integral(np.array([15.0])) == pytest.approx([4.0 + 0.8 * 5.0])


def test_the_last_duty_stays_in_force_after_the_last_record():
    h = _history([(0.0, 0.5)])
    assert h.integral(np.array([100.0])) == pytest.approx([50.0])


def test_average_over_a_window_that_straddles_several_segments():
    h = _history([(0.0, 1.0), (10.0, 0.0), (20.0, 1.0), (30.0, 0.0)])
    # window [5, 35) with zero delay: 5s at 1.0, 10s at 0.0, 10s at 1.0, 5s at 0.0
    values, valid = h.average(5.0, 35.0, np.array([0.0]))
    assert valid.all()
    assert values == pytest.approx([(5.0 + 0.0 + 10.0 + 0.0) / 30.0])


def test_average_shifts_the_window_by_each_delay():
    h = _history([(0.0, 0.0), (100.0, 1.0)])
    values, valid = h.average(150.0, 160.0, np.array([0.0, 60.0, 120.0]))
    # 150-120=30, which is inside retained history (earliest is t=0)
    assert valid.tolist() == [True, True, True]
    assert values[0] == pytest.approx(1.0)  # [150,160) is entirely at duty 1.0
    assert values[1] == pytest.approx(0.0)  # [90,100) is entirely at duty 0.0


def test_a_window_reaching_before_retained_history_is_invalid():
    h = _history([(100.0, 0.5)])
    _values, valid = h.average(150.0, 160.0, np.array([0.0, 60.0]))
    # 150-60 = 90 < 100, the earliest thing we know
    assert valid.tolist() == [True, False]


def test_segments_splits_a_window_at_every_duty_change():
    h = _history([(0.0, 0.2), (10.0, 0.8), (25.0, 0.5)])
    assert h.segments(5.0, 30.0) == [
        pytest.approx((5.0, 0.2)),
        pytest.approx((15.0, 0.8)),
        pytest.approx((5.0, 0.5)),
    ]


def test_segments_of_an_empty_window_is_empty():
    h = _history([(0.0, 0.4)])
    assert h.segments(10.0, 10.0) == []


def test_a_repeated_duty_does_not_create_a_segment():
    h = _history([(0.0, 0.4), (10.0, 0.4), (20.0, 0.4)])
    assert len(h) == 1


def test_pruning_bounds_memory():
    h = DutyHistory(120.0)
    for t in range(0, 100_000, 10):
        h.record(float(t), (t // 10) % 2 * 0.5)
        h.prune(float(t))
    # max_delay 120s at one change per 10s is ~13 segments; allow slack, forbid growth
    assert len(h) < 40


def test_pruning_keeps_enough_history_for_the_largest_delay():
    h = DutyHistory(120.0)
    for t in range(0, 1000, 10):
        h.record(float(t), (t // 10) % 2 * 0.5)
        h.prune(float(t))
    assert h.earliest() <= 990.0 - 120.0


def test_a_non_advancing_timestamp_is_ignored():
    h = _history([(10.0, 0.4)])
    h.record(5.0, 0.9)
    h.record(10.0, 0.9)
    assert len(h) == 1
    assert h.integral(np.array([20.0])) == pytest.approx([4.0])
