"""Fidelity-driven downsampling for the history chart.

The chart used to keep every Nth sample, which silently erases short events
(a lid-open dip, an overshoot spike) whenever the step exceeded the event's
width. This selects points by Largest-Triangle-Three-Buckets instead, and
grows the budget until the drawn curve is within a stated tolerance of the
true curve -- a shape target rather than a point count.
"""


def lttb_indices(values, times, budget):
    """Largest-Triangle-Three-Buckets (Steinarsson, 2013).

    Returns the INDICES to keep, so several series can share one x-axis.
    Picks, per bucket, the sample forming the largest triangle with the
    previously-kept point and the next bucket's average -- a good proxy for
    "the point that most defines the curve's shape", which is why peaks and
    valleys survive where every-Nth loses them.

    A budget of 2 or fewer collapses to just the two endpoints -- LTTB can't
    draw less than a straight line, and returning every point for a small
    budget would defeat the point of asking for one. `n` of 0 or 1 has no
    "two endpoints" to speak of, so it returns whatever indices exist (`[]`
    or `[0]`).
    """
    n = len(values)
    if n <= 1:
        return list(range(n))
    if budget <= 2:
        return [0, n - 1]
    if budget >= n:
        return list(range(n))

    kept = [0]  # always keep the first point
    bucket_size = (n - 2) / (budget - 2)
    a = 0  # previously kept index

    for i in range(budget - 2):
        # Average of the NEXT bucket = the triangle's third vertex.
        next_start = int((i + 1) * bucket_size) + 1
        next_end = min(int((i + 2) * bucket_size) + 1, n)
        count = max(1, next_end - next_start)
        avg_x = sum(times[next_start:next_end]) / count if next_end > next_start else times[-1]
        avg_y = sum(values[next_start:next_end]) / count if next_end > next_start else values[-1]

        range_start = int(i * bucket_size) + 1
        range_end = min(int((i + 1) * bucket_size) + 1, n)
        best, best_area = range_start, -1.0
        for j in range(range_start, range_end):
            area = abs((times[a] - avg_x) * (values[j] - values[a]) - (times[a] - times[j]) * (avg_y - values[a]))
            if area > best_area:
                best_area, best = area, j
        kept.append(best)
        a = best

    kept.append(n - 1)  # always keep the last point
    return kept


def max_interpolation_error(values, indices):
    """Worst absolute gap between the drawn curve and the true one.

    The chart draws straight lines between kept samples, so the error at a
    dropped sample is the distance from it to that line. This is the number
    the tolerance is expressed in (degrees, same units as the probe).
    """
    if len(indices) < 2:
        return float("inf")
    worst = 0.0
    for k in range(len(indices) - 1):
        i0, i1 = indices[k], indices[k + 1]
        y0, y1 = values[i0], values[i1]
        span = i1 - i0
        if span <= 1:
            continue
        for i in range(i0 + 1, i1):
            approx = y0 + (y1 - y0) * ((i - i0) / span)
            err = abs(approx - values[i])
            if err > worst:
                worst = err
    return worst


def exceeds_tolerance(values, indices, tolerance):
    """Same segment walk as `max_interpolation_error`, but answers only the
    yes/no question `select_indices` actually needs -- does ANY dropped
    sample's error exceed `tolerance` -- and stops at the first violation
    instead of scanning the rest of the series to find the worst one.

    `select_indices` runs this once per series per budget-doubling
    iteration, purely to compare against `tolerance`; on a budget that's
    going to fail, the first bad segment already settles the question, and
    most iterations in the doubling search DO fail. Scanning to the end
    anyway is where a synchronous, single-core, JIT-less request handler
    (a Raspberry Pi Zero) spends most of its time on this path.

    `max_interpolation_error` stays untouched and separately exported -- it
    expresses the tolerance contract in degrees, which several tests assert
    directly, whereas this only ever answers "worse than tolerance or not".
    The two must always agree: `exceeds_tolerance(v, i, t) ==
    (max_interpolation_error(v, i) > t)`.
    """
    if len(indices) < 2:
        return True  # mirrors max_interpolation_error's float("inf") here: inf > any t
    for k in range(len(indices) - 1):
        i0, i1 = indices[k], indices[k + 1]
        y0, y1 = values[i0], values[i1]
        span = i1 - i0
        if span <= 1:
            continue
        for i in range(i0 + 1, i1):
            approx = y0 + (y1 - y0) * ((i - i0) / span)
            if abs(approx - values[i]) > tolerance:
                return True
    return False


def _trim_to_cap(union_sorted, cap):
    """Deterministically shrink a sorted, deduped index list to at most `cap`
    entries, keeping it evenly spread (and the first/last endpoints) rather
    than lopping off one end.

    Picks `cap` evenly-spaced POSITIONS within `union_sorted` (not evenly
    spaced in raw index space -- the union is already shape-driven, so this
    preserves that spread instead of re-gridding it). Rounding can collide
    two positions on the same underlying index, so the result may come in
    under `cap`; it will never exceed it.
    """
    if len(union_sorted) <= cap:
        return union_sorted
    if cap <= 1:
        return union_sorted[:1]
    step = (len(union_sorted) - 1) / (cap - 1)
    picked = sorted({union_sorted[round(i * step)] for i in range(cap)})
    return picked


def _union_indices(series, times, budget):
    """Run LTTB on every series independently and merge the kept indices.

    Each series earns its own placement decisions -- a step function's edges
    survive without dictating where points land on a smooth trace sharing its
    x-axis, and vice versa. Sorted and deduped since series overlap heavily
    where their shapes agree (e.g. both flat).
    """
    kept = set()
    for s in series:
        kept.update(lttb_indices(s, times, budget))
    return sorted(kept)


def select_indices(series, times, *, tolerance=2.0, min_points=10000, max_points=None):
    """Choose which samples to send for a set of co-timed series.

    Returns every index when there are `min_points` or fewer samples -- below
    that the payload is small enough that downsampling only loses information.
    Above it, grow the LTTB budget until the UNION of every series' own LTTB
    selection is within `tolerance` for all of them, optionally capped at
    `max_points`.

    Each series is downsampled independently at the shared `budget`, then the
    kept indices are merged -- so `budget` is no longer the returned point
    count the way it is for `lttb_indices`. For k series, the union at budget
    B holds between B points (total agreement -- e.g. every series flat
    together) and k*B (no overlap at all); in practice probe traces and
    setpoint/target step functions overlap heavily on their flat stretches, so
    real unions land well below the k*B ceiling. This costs O(k*n) per budget
    doubling instead of O(n), but a single wide-range series (a step function)
    no longer has to independently satisfy every other series' shape through
    its own point placement, so convergence typically happens at a much
    smaller budget -- total work goes down, not up, in the case this fixes.

    If the budget search exhausts without any budget under `n` meeting
    `tolerance`: with no `max_points` cap, full fidelity (every index) is
    always available and is exactly what the tolerance contract promises, so
    that's what comes back -- never a still-failing downsample. With a
    `max_points` cap, the cap is an explicit instruction to accept
    degradation, so the union at the cap is returned instead -- trimmed down
    to the cap itself if the union overshoots it (`_trim_to_cap`: an evenly
    spaced subset of the union, endpoints kept, so `max_points` is always a
    hard ceiling on the returned count).
    """
    n = len(times)
    if n <= min_points or not series:
        return list(range(n))

    ceiling = min(max_points, n) if max_points else n

    budget = 1000
    while budget < ceiling:
        union = _union_indices(series, times, budget)
        if not any(exceeds_tolerance(s, union, tolerance) for s in series):
            return _trim_to_cap(union, max_points) if max_points else union
        budget *= 2

    if max_points:
        union = _union_indices(series, times, ceiling)
        return _trim_to_cap(union, max_points)
    return list(range(n))
