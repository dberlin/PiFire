"""Fidelity-driven downsampling for the history chart.

The chart used to keep every Nth sample, which silently erases short events
(a lid-open dip, an overshoot spike) whenever the step exceeded the event's
width. This selects points by Largest-Triangle-Three-Buckets instead, and
grows the budget until the drawn curve is within a stated tolerance of the
true curve -- a shape target rather than a point count.

Two properties of real history data every function here has to respect:

`None` is a legitimate reading. A probe that is unplugged, open-circuit or
reads badly stores Python `None` (`probes/base.py:251-253` returns
`(None, 0)`; the Kalman stage at `:374` deliberately passes it through), which
round-trips as JSON `null` all the way into `history["P"][label]`. Both
consumers render it as a GAP -- Chart.js via `spanGaps: False`, uPlot
natively -- so a dropped sample has no drawn position and therefore no error
to measure. Every value-touching site below skips it. It is never coerced to
0: a temperature nobody took, drawn as if real, is a worse bug than the crash.

Samples are not evenly spaced in time. `write_history` only fires inside a
work cycle, every ~3 s, and nothing at all is written in STOP; meanwhile
`read_history(num_items)` selects by ROW COUNT, not wall clock. So a
Monitor -> Stop -> idle -> Monitor sequence leaves an arbitrary time gap in
the middle of a window whose pre-gap rows are still retained. Both charts draw
straight lines in TIME space, so the tolerance -- the thing that enforces the
user's "within N degrees of the truth" -- must be measured there too. Measured
in index space it once reported 0.00 degrees on a window whose drawn line was
18 degrees off.
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

    Dropped readings (`None`) are excluded from the bucket average and from
    the triangle-area search: they have no y position, so they can neither
    contribute to nor win the "which sample most defines the shape" contest.
    When a whole bucket is `None` (a probe unplugged for a stretch) there is
    no triangle to compute for it at all -- it falls back to the bucket's
    first index, which is exactly what the area search already returns when
    no candidate beats the initial `best_area` of -1.0, and which keeps the
    `None` itself so the chart still shows the dropout.

    The triangle is anchored on the last kept point that HAS a reading, not
    simply on the last kept point. Those differ for exactly one bucket either
    side of a dropout, and the difference mattered: anchoring on a `None`
    left no triangle to compute, so the bucket straddling the end of a
    dropout fell back to its first index -- itself usually another `None` --
    and the first real samples after the probe came back were silently
    dropped. The last real point is also the vertex the CHART draws its next
    line from (a `None` has no drawn position), so it is the honest anchor.
    Same reason for the third vertex: when the whole next bucket is `None`
    its average is unusable, and the triangle degenerates to the anchor's own
    height, which ranks candidates by how far they deviate from it -- the
    right choice when what follows is a gap.
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
    anchor = 0 if values[0] is not None else None  # last kept index with a reading

    for i in range(budget - 2):
        # Average of the NEXT bucket = the triangle's third vertex.
        next_start = int((i + 1) * bucket_size) + 1
        next_end = min(int((i + 2) * bucket_size) + 1, n)
        count = max(1, next_end - next_start)
        if next_end > next_start:
            avg_x = sum(times[next_start:next_end]) / count
            present = [v for v in values[next_start:next_end] if v is not None]
            avg_y = sum(present) / len(present) if present else None
        else:
            avg_x = times[-1]
            avg_y = values[-1]

        range_start = int(i * bucket_size) + 1
        range_end = min(int((i + 1) * bucket_size) + 1, n)
        best, best_area = range_start, -1.0
        if anchor is not None:
            x_a, y_a = times[anchor], values[anchor]
            third_y = y_a if avg_y is None else avg_y
            for j in range(range_start, range_end):
                y_j = values[j]
                if y_j is None:
                    continue
                area = abs((x_a - avg_x) * (y_j - y_a) - (x_a - times[j]) * (third_y - y_a))
                if area > best_area:
                    best_area, best = area, j
        else:
            # Nothing has been kept with a reading yet (the window opens on a
            # dropout), so there is no anchor to form a triangle with: keep
            # the bucket's first actual reading, so the trace starts where
            # the data does rather than one bucket late.
            for j in range(range_start, range_end):
                if values[j] is not None:
                    best = j
                    break
        kept.append(best)
        if values[best] is not None:
            anchor = best

    kept.append(n - 1)  # always keep the last point
    return kept


def _run_boundary_indices(values):
    """First and last index of every maximal run of consecutive readings.

    These are the samples where a series starts and stops existing. Keeping
    them is what makes the tolerance checkers' "a segment with a `None` at
    either end is skipped whole" rule lossless instead of a blind spot: if
    both boundaries of every run are kept, then no reading can lie strictly
    between two consecutive kept points that have a `None` endpoint (a
    boundary would have to sit there, and it is itself kept), so every
    reading is either sent or lies on a drawn line whose error IS measured.

    Costs at most two indices per dropout. A probe unplugged once adds two
    points to a thousand; a probe flapping every other sample degenerates
    towards full fidelity, which is the honest answer for data that is half
    dropouts.
    """
    boundaries = set()
    previous = None
    for i, value in enumerate(values):
        if value is None:
            if previous is not None:
                boundaries.add(i - 1)
        elif previous is None:
            boundaries.add(i)
        previous = value
    if previous is not None:
        boundaries.add(len(values) - 1)
    return boundaries


def max_interpolation_error(values, indices, times=None):
    """Worst absolute gap between the drawn curve and the true one.

    The chart draws straight lines between kept samples, so the error at a
    dropped sample is the distance from it to that line. This is the number
    the tolerance is expressed in (degrees, same units as the probe).

    `times` is the x-axis the line is drawn against. Pass it: both consumers
    plot against wall clock, and history is not evenly spaced (see the module
    docstring), so index-space distance is the wrong ruler on any window
    holding a gap. It defaults to `None` -- meaning "assume unit spacing" --
    only because this function is separately exported and asserted directly
    by tests that build their own uniform x-axis; on evenly spaced samples
    the two agree exactly (the `dt` cancels: `((i-i0)*dt)/((i1-i0)*dt)`), so
    the default is a true statement about a uniform series rather than a
    silent fallback that means something different. The production caller
    (`select_indices`) always passes real timestamps.

    Samples whose value is `None` are skipped, and a segment with a `None` at
    either end is skipped whole: nothing is drawn there, so there is no error.

    That skip is a true statement about what the chart draws, but on its own
    it is not a fidelity guarantee: a REAL sample sitting inside such a
    segment is neither drawn nor measured, which is how a 60-degree excursion
    right after a dropout once passed the gate at 0.00 degrees. Closing that
    is the SELECTION's job, not this function's -- there is no honest number
    of degrees for a curve the chart never draws, and calling it infinite
    would only push every window holding a dropout to full fidelity.
    `_union_indices` therefore always keeps every run's boundary samples,
    which makes the skip provably lossless: no reading can lie inside a
    skipped segment. Callers passing their own `indices` (this is separately
    exported) get the honest-about-drawing reading, not that guarantee.
    """
    if len(indices) < 2:
        return float("inf")
    worst = 0.0
    for k in range(len(indices) - 1):
        i0, i1 = indices[k], indices[k + 1]
        if i1 - i0 <= 1:
            continue
        y0, y1 = values[i0], values[i1]
        if y0 is None or y1 is None:
            continue
        x0 = i0 if times is None else times[i0]
        span = (i1 - i0) if times is None else (times[i1] - x0)
        for i in range(i0 + 1, i1):
            value = values[i]
            if value is None:
                continue
            # A zero-width span means coincident timestamps: the drawn
            # segment is vertical, so every sample on it sits at y0.
            approx = y0 if span == 0 else y0 + (y1 - y0) * (((i if times is None else times[i]) - x0) / span)
            err = abs(approx - value)
            worst = max(worst, err)
    return worst


def exceeds_tolerance(values, indices, tolerance, times=None):
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

    `max_interpolation_error` stays separately exported -- it expresses the
    tolerance contract in degrees, which several tests assert directly,
    whereas this only ever answers "worse than tolerance or not". The two
    must always agree, for the same `times`: `exceeds_tolerance(v, i, t, x)
    == (max_interpolation_error(v, i, x) > t)`. `times` and the `None`
    handling -- including why skipping `None`-ended segments is lossless for
    the indices `select_indices` builds -- carry the same meaning here as
    there.
    """
    if len(indices) < 2:
        return True  # mirrors max_interpolation_error's float("inf") here: inf > any t
    for k in range(len(indices) - 1):
        i0, i1 = indices[k], indices[k + 1]
        if i1 - i0 <= 1:
            continue
        y0, y1 = values[i0], values[i1]
        if y0 is None or y1 is None:
            continue
        x0 = i0 if times is None else times[i0]
        span = (i1 - i0) if times is None else (times[i1] - x0)
        for i in range(i0 + 1, i1):
            value = values[i]
            if value is None:
                continue
            approx = y0 if span == 0 else y0 + (y1 - y0) * (((i if times is None else times[i]) - x0) / span)
            if abs(approx - value) > tolerance:
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

    Every series' run boundaries (`_run_boundary_indices`) go in on top of
    its LTTB picks, at every budget, so the tolerance gate `select_indices`
    runs against this union can never be silently exempted from a stretch of
    real readings. Without them the gate's segment skipping and LTTB's
    handling of dropouts compounded: real data next to a dropout was dropped
    from the payload AND excluded from the check, so a 60-degree excursion
    18 seconds after a probe was plugged back in was neither sent nor drawn
    while the gate reported "within tolerance".
    """
    kept = set()
    for s in series:
        kept.update(lttb_indices(s, times, budget))
        kept.update(_run_boundary_indices(s))
    return sorted(kept)


def select_indices(series, times, *, tolerance=2.0, min_points=10000, max_points=None):
    """Choose which samples to send for a set of co-timed series.

    Returns every index when there are `min_points` or fewer samples -- below
    that the payload is small enough that downsampling only loses information.
    Above it, grow the LTTB budget until the UNION of every series' own LTTB
    selection is within `tolerance` for all of them, optionally capped at
    `max_points`. `times` is forwarded to the tolerance check, not just used
    for LTTB's triangle areas: the guarantee is about the line the chart
    DRAWS, which is drawn against wall clock.

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

    One consequence of that trimming: `_union_indices` guarantees every
    series' run boundaries are kept -- which is what stops a dropout from
    exempting the readings beside it from the tolerance check -- and
    `_trim_to_cap` can drop some of them again. That is the same accepted
    degradation as the tolerance itself, and only reachable via `max_points`,
    which no production caller sets today.
    """
    n = len(times)
    if n <= min_points or not series:
        return list(range(n))

    ceiling = min(max_points, n) if max_points else n

    budget = 1000
    while budget < ceiling:
        union = _union_indices(series, times, budget)
        if not any(exceeds_tolerance(s, union, tolerance, times) for s in series):
            return _trim_to_cap(union, max_points) if max_points else union
        budget *= 2

    if max_points:
        union = _union_indices(series, times, ceiling)
        return _trim_to_cap(union, max_points)
    return list(range(n))
