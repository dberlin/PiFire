/**
 * Decides whether a uPlot data update (`setData`) should reset the x-scale
 * to autoscale over the new data, or preserve the plot's current x-scale.
 *
 * uPlot's own typings say `setData` "sets the chart data & redraws. (default
 * resetScales = true)" -- so calling it with no second argument autoscales x
 * back to the full data range on *every* tick, cancelling any zoom the user
 * just dragged. But unconditionally passing `false` breaks the other real
 * case: when the caller changes the requested time window (e.g. 60 -> 240
 * minutes), the incoming data covers a different x-range and the view MUST
 * rescale, or the new data renders off-screen.
 *
 * The rule implemented here: preserve the scale only when the user has
 * actually zoomed in. That's detected by comparing the plot's CURRENT
 * x-scale against the x-range of the data the plot CURRENTLY holds (i.e.
 * `u.data[0]`, read *before* the new data is applied):
 *
 *  - if the scale covers essentially the whole current range, the user
 *    hasn't zoomed -> reset. This also covers the time-window-change case:
 *    an untouched scale still equals the OLD data's full range (the user
 *    never zoomed), so it's indistinguishable from "not zoomed" -- which is
 *    exactly the outcome that's needed, since the new data needs a reset to
 *    become visible.
 *  - if the scale is a strict subset of the current range, the user has
 *    dragged a zoom -> preserve it.
 *
 * Exported as a pure function (rather than inlined in HistoryChart.tsx) so
 * it's directly unit-testable without driving a real uPlot instance -- the
 * same reason tooltipFormat.ts/tooltipRow.ts are split out, and to avoid
 * tripping react-refresh/only-export-components by co-exporting a
 * non-component from the component file.
 */
export function shouldResetScales(
  scaleMin: number | null | undefined,
  scaleMax: number | null | undefined,
  dataMin: number | null | undefined,
  dataMax: number | null | undefined,
): boolean {
  if (
    scaleMin == null ||
    scaleMax == null ||
    dataMin == null ||
    dataMax == null ||
    !Number.isFinite(scaleMin) ||
    !Number.isFinite(scaleMax) ||
    !Number.isFinite(dataMin) ||
    !Number.isFinite(dataMax)
  ) {
    // Degenerate input -- e.g. no scale yet (before the first render), or no
    // data. Nothing meaningful to preserve, so reset is the safe default.
    return true;
  }

  const range = dataMax - dataMin;
  if (range <= 0) {
    // No data, or a single point: there's no meaningful "zoomed in" state.
    return true;
  }

  // A small tolerance for float drift (from uPlot's own autoscale padding,
  // or repeated setData round-trips), relative to the data range -- epoch
  // seconds ranges are huge, so a fixed absolute epsilon would be
  // meaningless at one end of the scale and too loose at the other.
  const eps = range * 1e-6;
  const coversWholeRange = scaleMin <= dataMin + eps && scaleMax >= dataMax - eps;
  return coversWholeRange;
}
