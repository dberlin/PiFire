"""Fidelity-driven history downsampling.

prepare_chartdata used to keep every Nth sample (step = num_items // data_points).
At the shipped default (60 points over a 480-minute window) that is step ~= 160,
so a two-minute lid-open dip -- the exact event a cook chart exists to show --
could be sampled straight past and never appear. These tests pin the replacement:
LTTB selection driven by a shape target, not a point count.
"""

import hashlib
import json
import math
import random

from file_mgmt.downsample import exceeds_tolerance, lttb_indices, max_interpolation_error, select_indices


def _cook_with_dip(n=30000, dip_at=15000, dip_len=120, dip_depth=60.0):
    """Flat 225F hold with one sharp, narrow dip (a lid-open event)."""
    times = [float(i) for i in range(n)]
    values = [225.0 for _ in range(n)]
    for i in range(dip_at, dip_at + dip_len):
        phase = (i - dip_at) / dip_len
        values[i] = 225.0 - dip_depth * math.sin(math.pi * phase)
    return values, times


def test_passthrough_below_the_threshold():
    values, times = _cook_with_dip(n=5000, dip_at=2500)
    idx = select_indices([values], times, tolerance=2.0, min_points=10000)
    assert idx == list(range(5000))  # nothing dropped -- fidelity is exact


def test_downsamples_above_the_threshold():
    values, times = _cook_with_dip(n=30000)
    idx = select_indices([values], times, tolerance=2.0, min_points=10000)
    assert len(idx) < len(times)
    assert idx[0] == 0 and idx[-1] == len(times) - 1  # endpoints always kept


def test_meets_the_shape_target():
    values, times = _cook_with_dip(n=30000)
    idx = select_indices([values], times, tolerance=2.0, min_points=10000)
    assert max_interpolation_error(values, idx) <= 2.0


def test_preserves_a_narrow_dip_that_every_nth_would_miss():
    """The regression this whole change exists for."""
    values, times = _cook_with_dip(n=30000, dip_at=15000, dip_len=120, dip_depth=60.0)

    # Naive decimation at the OLD default budget: step = 30000 // 60 = 500, so
    # the 120-sample dip falls entirely between two kept samples.
    every_nth = list(range(0, len(times), len(times) // 60))
    assert max_interpolation_error(values, every_nth) > 20.0  # dip essentially erased

    idx = select_indices([values], times, tolerance=2.0, min_points=10000)
    kept_in_dip = [i for i in idx if 15000 <= i < 15120]
    assert kept_in_dip, "LTTB dropped the entire dip"
    assert max_interpolation_error(values, idx) <= 2.0


def test_budget_grows_until_every_series_is_within_tolerance():
    """One shared x-axis serves all probes, so the WORST series sets the budget."""
    calm, times = _cook_with_dip(n=30000, dip_len=120, dip_depth=1.0)  # nearly flat
    spiky, _ = _cook_with_dip(n=30000, dip_len=120, dip_depth=80.0)

    calm_only = select_indices([calm], times, tolerance=2.0, min_points=10000)
    both = select_indices([calm, spiky], times, tolerance=2.0, min_points=10000)

    assert len(both) >= len(calm_only)
    assert max_interpolation_error(spiky, both) <= 2.0
    assert max_interpolation_error(calm, both) <= 2.0


def test_max_points_caps_the_budget():
    values, times = _cook_with_dip(n=30000)
    idx = select_indices([values], times, tolerance=0.001, min_points=10000, max_points=2000)
    assert len(idx) <= 2000  # tolerance unreachable, but the cap holds


def test_lttb_keeps_endpoints_and_respects_budget():
    values, times = _cook_with_dip(n=20000)
    idx = lttb_indices(values, times, 500)
    assert len(idx) == 500
    assert idx[0] == 0 and idx[-1] == len(values) - 1
    assert idx == sorted(idx)


def test_unreachable_tolerance_without_a_cap_falls_back_to_every_point():
    """CRITICAL regression: `select_indices`'s old fallback line --
    `capped if max_points else best if len(best) <= n else all_indices` --
    returned the last still-failing LTTB result (`best`) instead of
    `all_indices`, because `len(best) <= n` is always true for an LTTB
    result (an LTTB budget is by construction < n on this path). With
    `max_points=None` the tolerance contract has no escape hatch to spend
    -- full fidelity is always available -- so an unreachable tolerance must
    fall back to sending every point, not a downsample that still violates
    the tolerance it was supposed to guarantee.

    High-frequency alternating jitter (amplitude far past `tolerance`, period
    2) is chosen because no LTTB budget below `n` can represent it within
    2.0 degrees -- confirmed empirically: budgets 1000/2000/4000/8000/16000
    over n=20000 all leave `max_interpolation_error` in the 65-100 range.
    """
    n = 20000
    values = [100.0 if i % 2 == 0 else 0.0 for i in range(n)]
    times = [float(i) for i in range(n)]

    idx = select_indices([values], times, tolerance=2.0, min_points=10000)

    assert idx == list(range(n))
    assert max_interpolation_error(values, idx) <= 2.0


def test_max_points_cap_still_returns_the_capped_lttb_result_when_unreachable():
    """The `max_points` branch is deliberately untouched by the fallback
    fix above: a caller who set a cap is explicitly asking to accept
    degradation rather than pay for every point, so an unreachable
    tolerance there still returns the LTTB result AT the cap (not
    `all_indices`, which would silently ignore the cap)."""
    n = 20000
    values = [100.0 if i % 2 == 0 else 0.0 for i in range(n)]
    times = [float(i) for i in range(n)]

    idx = select_indices([values], times, tolerance=2.0, min_points=10000, max_points=2000)

    assert len(idx) <= 2000
    assert idx != list(range(n))


def test_lttb_budget_of_two_returns_just_the_endpoints():
    """`lttb_indices`'s old `budget <= 2` branch returned ALL n indices,
    contradicting its own "budget" parameter and docstring -- a budget of 2
    got everything instead of 2 points. It's unreachable from
    `select_indices` today (whose budget floor is 1000) but this is an
    independently-exported, independently-tested function; the contract it
    ships needs to be honest on its own."""
    values, times = _cook_with_dip(n=1000, dip_at=500)
    idx = lttb_indices(values, times, 2)
    assert idx == [0, 999]


def test_lttb_budget_of_one_or_zero_still_returns_the_two_endpoints():
    """A budget below 2 doesn't have a smaller-than-2-points meaning to
    honor -- LTTB can't draw less than a line -- so it collapses to the
    same two-endpoint result as budget == 2 rather than special-casing."""
    values, times = _cook_with_dip(n=1000, dip_at=500)
    assert lttb_indices(values, times, 1) == [0, 999]
    assert lttb_indices(values, times, 0) == [0, 999]


def test_lttb_empty_series_returns_no_indices():
    assert lttb_indices([], [], 500) == []


def test_lttb_single_point_series_returns_that_one_index():
    assert lttb_indices([225.0], [0.0], 500) == [0]


# ---------------------------------------------------------------------------
# Fix pass 2: union LTTB across series, instead of one elected "driver".
#
# `select_indices` used to pick ONE driver series -- the one with the widest
# value range -- run LTTB on it alone, and only use the other series to
# VERIFY the resulting budget, never to influence WHERE points land. A
# setpoint/target series (PSP, NT) is a step function: nearly flat between
# steps, so it usually has the widest range in a cook (a 50-degree setpoint
# bump beats a probe's noise), which made it the driver by default. LTTB run
# on a flat-almost-everywhere driver degenerates into picking close to the
# first index of every roughly-equal-sized bucket (ties go to whichever
# point is seen first, and a flat/step series has no real triangle-area
# signal away from its one step) -- i.e. close to a rigid grid that knows
# nothing about a smooth probe trace's own peaks and valleys, including any
# narrow spikes riding on it. A grid needs a MUCH bigger budget than an
# LTTB run adaptively targeting the probe's own shape to keep narrow spikes
# within tolerance, and `select_indices`'s budget doubling gives up (falling
# back to returning every point) well before a driver-based grid gets there.
#
# The fix: run LTTB on every series independently at each budget and take
# the union of kept indices, so every series' shape earns its own point
# placement.
# ---------------------------------------------------------------------------


def _probe_with_spikes_and_a_setpoint_step(n=25000, seed=3):
    """A smooth-ish, noisy probe trace with a handful of brief, narrow
    flare-ups (the kind of short excursion the downsampler exists to keep),
    alongside a PSP-style setpoint series that steps once, mid-cook, from
    225 to 275. The step's range (50) is wider than the probe's full range
    (~50 as well, but dominated by tiny noise plus a few brief spikes), so
    the OLD code elects PSP -- a near-flat step function -- as the driver.
    """
    rng = random.Random(seed)
    times = [float(i) for i in range(n)]
    probe = [205.0 + 3.0 * math.sin(2 * math.pi * i / 900.0) + rng.uniform(-0.4, 0.4) for i in range(n)]
    # Five brief, tall flare-ups scattered through the cook -- narrow enough
    # that a rigid grid can straddle one entirely and miss its peak.
    spike_specs = [(3000, 5, 25.0), (7500, 5, -22.0), (12000, 5, 28.0), (16500, 5, -20.0), (21000, 5, 24.0)]
    for center, half_width, height in spike_specs:
        for i in range(center - half_width, center + half_width):
            phase = (i - (center - half_width)) / (2 * half_width)
            probe[i] += height * math.sin(math.pi * phase)

    step_idx = n // 2
    psp = [225.0] * step_idx + [275.0] * (n - step_idx)
    return probe, psp, times, step_idx


def test_union_across_series_replaces_the_degenerate_single_driver_selection():
    """The reported regression: with PSP (range 50) beating the probe (range
    ~50 too, but nearly all of that from 5 narrow spikes) as the OLD driver,
    `select_indices` doubles its budget through 1000/2000/4000/8000/16000 --
    all short of what a PSP-driven grid needs to pin down every spike -- and
    then gives up, returning all 25000 of 25000 points: no downsampling at
    all, on a payload that is supposed to shrink for a Raspberry Pi Zero
    front end.

    Run directly on the probe ALONE, LTTB converges within tolerance at a
    budget of 16000 (the next power-of-two the search tries after the 12000
    where direct convergence actually starts) -- so the fix, unioning LTTB
    across both series instead of gridding off PSP, should land in that same
    neighbourhood rather than at n.

    Bound asserted: len(idx) <= 16000, i.e. at least a 36% reduction from
    25000. That is not a tight/brittle number -- it is the same budget
    doubling step the OLD code already tries and fails at; if the union
    approach converges by the time direct LTTB-on-probe does, it must clear
    this bound. (Measured in practice: ~12,900 -- roughly half.)
    """
    probe, psp, times, _step_idx = _probe_with_spikes_and_a_setpoint_step()

    idx = select_indices([probe, psp], times, tolerance=2.0, min_points=10000)

    assert len(idx) <= 16000, f"expected a meaningful downsample, got {len(idx)} of {len(times)}"
    assert max_interpolation_error(probe, idx) <= 2.0
    assert max_interpolation_error(psp, idx) <= 2.0


def test_step_edge_survives_the_union_so_the_chart_draws_a_step_not_a_ramp():
    """Even though the probe now contributes its own point placement, the
    setpoint series must still keep the exact sample at its step -- losing
    it would make the chart interpolate a multi-thousand-sample ramp between
    225 and 275 that never happened."""
    n = 15000
    times = [float(i) for i in range(n)]
    probe = [200.0 + 5.0 * math.sin(2 * math.pi * i / 500.0) for i in range(n)]
    step_idx = 8000
    psp = [225.0] * step_idx + [275.0] * (n - step_idx)

    idx = select_indices([probe, psp], times, tolerance=2.0, min_points=10000)

    # The step happens BETWEEN sample step_idx-1 (still 225) and step_idx
    # (first 275 sample) -- either one being kept pins the jump to a single
    # sample-width, not a smeared ramp.
    assert step_idx in idx or (step_idx - 1) in idx
    assert max_interpolation_error(psp, idx) == 0.0


def test_max_points_caps_the_union_across_series():
    """A union of k independent LTTB runs can hold up to k times a single
    run's budget -- `max_points` must still be a hard ceiling on what comes
    back, not just on what a single driver's run would have produced."""
    n = 20000
    times = [float(i) for i in range(n)]
    # Three series, each oscillating on its own unrelated period, so their
    # LTTB picks barely overlap -- close to the worst case for union size.
    s1 = [200.0 + 10.0 * math.sin(2 * math.pi * i / 37.0) for i in range(n)]
    s2 = [180.0 + 10.0 * math.cos(2 * math.pi * i / 53.0) for i in range(n)]
    s3 = [220.0 + 10.0 * math.sin(2 * math.pi * i / 71.0 + 1.3) for i in range(n)]

    idx = select_indices([s1, s2, s3], times, tolerance=0.001, min_points=10000, max_points=3000)

    assert len(idx) <= 3000


# ---------------------------------------------------------------------------
# Fix pass 3: short-circuit the tolerance check.
#
# `select_indices` calls a per-series tolerance check once per series per
# budget-doubling iteration, purely to decide "did this budget clear the
# bar, yes or no". `max_interpolation_error` answers a stronger question
# (the WORST error) by scanning every dropped sample even after the first
# violation has already settled the yes/no question -- and most budgets
# tried during the doubling search fail, so this is where a synchronous,
# single-core, JIT-less request handler (a Raspberry Pi Zero, serving this
# per chart request) spends most of its time. `exceeds_tolerance` walks the
# same segments but returns the instant it finds a violation.
#
# This is a pure speed change: the SELECTED INDICES for any given input must
# be identical before and after. The two tests below prove it.
# ---------------------------------------------------------------------------


def test_exceeds_tolerance_agrees_with_max_interpolation_error():
    """`exceeds_tolerance` short-circuits instead of scanning for the worst
    error, but the two must always agree on the boolean question
    `select_indices` actually asks: did this budget clear the tolerance bar
    or not."""

    def agrees(values, indices, tolerance):
        assert exceeds_tolerance(values, indices, tolerance) == (max_interpolation_error(values, indices) > tolerance)

    # flat series -- zero error everywhere, comfortably within tolerance
    flat = [225.0] * 50
    agrees(flat, list(range(0, 50, 5)), 1.0)

    # one spike sitting entirely between two kept samples -- the single
    # violation dominates and must be caught on the first bad segment
    spiky = [225.0] * 50
    spiky[25] = 400.0
    agrees(spiky, [0, 20, 30, 49], 2.0)

    # noisy series, exercised at both a loose (should NOT exceed) and a
    # tight (should exceed) tolerance against the same selection
    rng = random.Random(11)
    noisy = [200.0 + rng.uniform(-5.0, 5.0) for _ in range(200)]
    kept = sorted({0, 199, *rng.sample(range(1, 199), 30)})
    agrees(noisy, kept, 3.0)
    agrees(noisy, kept, 0.2)

    # exactly-at-tolerance boundary: worst error == tolerance exactly, which
    # must NOT count as exceeding (both use strict ">")
    boundary_values = [0.0, 2.0, 0.0]
    assert max_interpolation_error(boundary_values, [0, 2]) == 2.0
    assert exceeds_tolerance(boundary_values, [0, 2], 2.0) is False
    agrees(boundary_values, [0, 2], 2.0)

    # degenerate: fewer than 2 indices
    agrees(flat, [], 1.0)
    agrees(flat, [7], 1.0)

    # degenerate: n of 0 and 1
    agrees([], [], 1.0)
    agrees([9.0], [0], 1.0)


def test_short_circuit_optimisation_does_not_change_selection_for_the_probe_psp_fixture():
    """Equivalence pin: `select_indices`'s per-budget check switched from
    `max_interpolation_error(...) > tolerance` (scans every dropped sample
    even once the answer is decided) to the short-circuiting
    `exceeds_tolerance(...)` (stops at the first violation). That must not
    change WHICH indices get selected for a given input -- it is purely a
    speed change.

    This fingerprint was captured by running this exact fixture/call through
    the PRE-change `select_indices` (the version that called only
    `max_interpolation_error`, before `exceeds_tolerance` existed) and
    hashing the returned list -- embedding all 12934 indices literally would
    dwarf the rest of this file, and a sha256 over the exact ordered list is
    just as sensitive to a single differing index as a literal list
    equality would be.
    """
    probe, psp, times, _step_idx = _probe_with_spikes_and_a_setpoint_step()
    idx = select_indices([probe, psp], times, tolerance=2.0, min_points=10000)

    assert len(idx) == 12934
    assert idx[:10] == [0, 1, 3, 4, 6, 7, 10, 13, 15, 16]
    assert idx[-10:] == [24985, 24986, 24987, 24989, 24991, 24992, 24994, 24995, 24997, 24999]
    assert (
        hashlib.sha256(json.dumps(idx).encode()).hexdigest()
        == "800ff42468bc661f78565bc66ba1c793172ebda9364f72aed009b89b26308b55"
    )


def test_short_circuit_optimisation_does_not_change_selection_for_the_dip_fixture():
    """Same equivalence pin as above, captured the same way, on a different
    fixture shape (one series, one narrow event) over the same 10000-sample
    gate -- so the pin isn't tied to a single input's code path through the
    union/tolerance logic."""
    values, times = _cook_with_dip(n=30000)
    idx = select_indices([values], times, tolerance=2.0, min_points=10000)

    assert len(idx) == 2000
    assert idx[:10] == [0, 1, 16, 31, 46, 61, 76, 91, 106, 121]
    assert idx[-10:] == [29863, 29878, 29893, 29908, 29923, 29938, 29953, 29968, 29983, 29999]
    assert (
        hashlib.sha256(json.dumps(idx).encode()).hexdigest()
        == "fd56e78cca22efa65cc2c0cbe6ee3b49c71d98c22713321e4c90b33e1aea461a"
    )


# ---------------------------------------------------------------------------
# Fix pass 4: `None` readings, and measuring error in TIME space.
#
# Two defects found by the final merge-readiness review, both of which only
# bite ABOVE the `min_points` gate (below it `select_indices` returns
# `list(range(n))` without ever touching a value or a timestamp, which is why
# every test above missed them):
#
# 1. A probe that is unplugged / open-circuit / reads badly stores Python
#    `None` -- `probes/base.py:251-253` returns `(None, 0)` and the Kalman
#    stage at `:374` deliberately passes it through, so it round-trips via
#    JSON `null` into `history["P"][label]`. The OLD every-Nth decimation only
#    INDEXED (`range(start, stop, step)`), so nulls flowed through untouched
#    and were emitted as `{"x": ts, "y": null}` -- a gap on both charts. This
#    module does value ARITHMETIC (bucket averages, triangle areas,
#    interpolation error) and raised `TypeError` on the first `None`, i.e. a
#    500 on both `GET /api/history/chart` and the legacy `POST
#    /history/refresh`. A dropped sample must be skipped, never coerced to 0
#    -- a fabricated 0 F reading drawn as if real is exactly what commit
#    1d8a67a9 removed from the empty-history branch.
#
# 2. The tolerance checkers interpolated in INDEX space (`(i - i0) / (i1 -
#    i0)`) while both charts draw straight lines in TIME space. History is
#    genuinely not evenly spaced: `write_history` only fires inside a work
#    cycle every ~3 s, nothing is written in STOP, and `read_history` selects
#    by ROW COUNT not wall clock -- so Monitor -> Stop -> idle -> Monitor
#    leaves an arbitrary time gap inside a retained window. The gate then
#    reports an error against a curve nobody draws.
# ---------------------------------------------------------------------------


def _drawn_curve_error(values, indices, times):
    """Independent oracle: the worst distance between the polyline the chart
    actually DRAWS (straight lines in TIME space between kept samples) and
    the true samples.

    Deliberately not `max_interpolation_error` -- it is the thing under test,
    so measuring it with itself would prove nothing.
    """
    worst = 0.0
    for k in range(len(indices) - 1):
        i0, i1 = indices[k], indices[k + 1]
        y0, y1 = values[i0], values[i1]
        if y0 is None or y1 is None:
            continue
        x0, x1 = times[i0], times[i1]
        for i in range(i0 + 1, i1):
            if values[i] is None:
                continue
            approx = y0 if x1 == x0 else y0 + (y1 - y0) * ((times[i] - x0) / (x1 - x0))
            worst = max(worst, abs(approx - values[i]))
    return worst


def _gapped_ramp(n=12000, gap_after=6006, gap_ms=7200000, cadence_ms=3000):
    """12000 samples at the real 3 s write cadence with one 2-hour gap, and a
    ramp 70 -> 270 F straddling the gap (linear in INDEX across indices
    6000..6012, so index-space interpolation sees a perfectly even climb
    while the drawn line jumps almost vertically at the gap)."""
    times = []
    t = 1700000000000.0
    for i in range(n):
        times.append(t)
        t += cadence_ms + (gap_ms if i == gap_after else 0)
    values = []
    for i in range(n):
        if i < 6000:
            values.append(70.0)
        elif i <= 6012:
            values.append(70.0 + 200.0 * (i - 6000) / 12.0)
        else:
            values.append(270.0)
    return values, times


def test_select_indices_survives_none_readings_above_the_gate():
    """CRITICAL regression: one unplugged probe sample anywhere in a series
    above the 10000-sample gate crashed the whole reduce path with
    `TypeError: unsupported operand type(s) for +: 'float' and 'NoneType'`
    (the bucket average in `lttb_indices`). At the ~3 s write cadence 10000
    samples is ~8.3 hours -- an ordinary brisket -- so this is not exotic.

    Covers both shapes the review asked for: an ISOLATED `None` (a single
    bad read) and a `None` RUN long enough to span an entire LTTB bucket at
    every budget the doubling search tries (bucket size at budget 1000 over
    n=12000 is ~12 samples; a 2000-sample run swallows ~166 of them), so the
    "whole bucket is null" fallback is genuinely exercised and not just the
    partial-bucket path.
    """
    n = 12000
    times = [1700000000000.0 + i * 3000.0 for i in range(n)]
    values = [200.0 + 20.0 * math.sin(2 * math.pi * i / 3000.0) for i in range(n)]
    values[5000] = None  # isolated bad read
    for i in range(8000, 10000):  # probe unplugged for ~100 minutes
        values[i] = None

    idx = select_indices([values], times, tolerance=2.0, min_points=10000)

    assert idx == sorted(set(idx))
    assert idx[0] == 0 and idx[-1] == n - 1
    assert len(idx) < n, "a smooth trace with gaps should still downsample, not fall through to every point"
    # The nulls are carried, not repaired: nothing invented a reading.
    assert any(values[i] is None for i in idx)
    # And the fidelity guarantee still holds over the samples that DO exist.
    assert _drawn_curve_error(values, idx, times) <= 2.0


def test_null_tolerant_helpers_never_fabricate_a_reading():
    """The three exported helpers must each survive `None` on their own --
    they are independently exported and `select_indices` is not the only way
    to reach them. A `None` sample has no reading, therefore no error to
    measure: it must be skipped, not read as 0 (which would report a
    200-degree error against a 200 F hold and drag a budget search out to
    full fidelity for no reason)."""
    values = [200.0] * 50
    values[10] = None
    values[25] = None
    times = [float(i) for i in range(50)]

    assert lttb_indices(values, times, 10)  # does not raise
    # Flat 200 F with two dropped reads: the drawn line is exactly the truth
    # everywhere a truth exists, so the error is 0 -- NOT 200 (0-coercion).
    assert max_interpolation_error(values, [0, 49], times=times) == 0.0
    assert exceeds_tolerance(values, [0, 49], 2.0, times=times) is False

    # A kept endpoint that is itself `None` has no drawn segment at all
    # (both charts render null as a gap), so that segment is unmeasurable
    # rather than infinitely bad.
    endpoint_null = [200.0, 201.0, None, 202.0, 203.0]
    endpoint_times = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert max_interpolation_error(endpoint_null, [0, 2, 4], times=endpoint_times) == 0.0
    assert exceeds_tolerance(endpoint_null, [0, 2, 4], 2.0, times=endpoint_times) is False


def test_tolerance_is_measured_against_the_curve_the_chart_actually_draws():
    """IMPORTANT regression: the 2 F guarantee was checked in INDEX space
    while uPlot (`HistoryChart.tsx`) and Chart.js (`history.js`) both draw
    straight lines in TIME space. On a window holding a real time gap --
    Monitor -> Stop -> idle -> Monitor, with the pre-gap rows still retained
    because `read_history` selects by row count -- the two diverge wildly.

    On this fixture the shipped code reported an index-space error of 0.00
    against a tolerance of 2.0 while the line the user actually sees was
    18.17 F away from the truth: between two kept indices straddling the gap,
    every pre-gap sample sits at ~0% of the TIME span and every post-gap one
    at ~100%, so the drawn segment jumps almost vertically at the gap while
    index-space math believes it ramps evenly across all 12 samples.
    """
    values, times = _gapped_ramp()

    idx = select_indices([values], times, tolerance=2.0, min_points=10000)

    assert _drawn_curve_error(values, idx, times) <= 2.0


def test_max_interpolation_error_reports_the_drawn_error_not_the_index_error():
    """The exported degrees-valued contract must agree with the chart too --
    `exceeds_tolerance` short-circuiting off a corrected metric while
    `max_interpolation_error` still reported the index-space one would leave
    the two disagreeing, breaking the invariant their docstrings assert."""
    values, times = _gapped_ramp()
    # The two kept samples that straddle the gap, plus the ramp's own ends.
    indices = [0, 6000, 6012, len(values) - 1]

    drawn = _drawn_curve_error(values, indices, times)
    assert drawn > 10.0, "fixture must actually exhibit the divergence"

    assert max_interpolation_error(values, indices, times=times) == drawn
    assert exceeds_tolerance(values, indices, 2.0, times=times) is True
    # ... and it still agrees with the boolean checker on the same inputs.
    assert exceeds_tolerance(values, indices, drawn, times=times) is False


def test_evenly_spaced_data_selects_the_same_indices_whatever_the_cadence():
    """Guard on the time-space fix: on evenly-spaced samples the `dt` cancels
    algebraically -- `((i - i0)*dt) / ((i1 - i0)*dt) == (i - i0) / (i1 - i0)`
    -- so a corrected checker must return byte-identical selections to the
    index-space one for every well-behaved window. Asserted here by holding
    the shape fixed and varying only the epoch origin and the cadence: 3 s
    real-cadence milliseconds and bare 0,1,2,... must select the same points,
    and both must match the hash this fixture was already pinned to before
    the fix.
    """
    values, unit_times = _cook_with_dip(n=30000)
    baseline = select_indices([values], unit_times, tolerance=2.0, min_points=10000)

    for origin, cadence in ((1700000000000.0, 3000.0), (0.0, 0.5), (-5000.0, 1.0)):
        shifted = [origin + i * cadence for i in range(len(values))]
        assert select_indices([values], shifted, tolerance=2.0, min_points=10000) == baseline

    # The pre-fix fingerprint for this fixture, unchanged.
    assert (
        hashlib.sha256(json.dumps(baseline).encode()).hexdigest()
        == "fd56e78cca22efa65cc2c0cbe6ee3b49c71d98c22713321e4c90b33e1aea461a"
    )
