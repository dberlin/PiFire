import type {
  CookFileChartData,
  HistoryAnnotation,
  HistoryChartData,
} from "../../helpers/contracts/content.gen";
import { type ChartInput, hasPlottableHistory, toChartInput } from "../history/historyAdapter";

/**
 * Adapt a saved cook file's chart payload to the shipped uPlot chart.
 *
 * The shipped adapter is reused as-is: toChartInput reads ONLY `time_labels`
 * and `chart_data`, the exact two keys this payload carries. It is not
 * re-implemented here.
 *
 * The one thing that differs is the x axis. Live history always writes numeric
 * epoch milliseconds (common/datastore_accessors.py), so historyAdapter's
 * `ms / 1000` is safe there. A cook file's graph_data.json is whatever
 * prepare_chartdata wrote AT COOK TIME, and pre-v1.5 archives (and hand-built
 * files) can carry "12:00:00" strings. A string divided by 1000 is NaN, and
 * uPlot renders NaN as nothing at all -- a blank chart with no error anywhere.
 * So the guard lives here, and historyAdapter is not loosened to accommodate a
 * shape live history can never produce.
 *
 * Returns null when there is nothing plottable, so the caller can say WHY.
 */
export function toCookChartInput(data: CookFileChartData): ChartInput | null {
  const times = data.time_labels;
  if (times.length === 0) return null;
  if (!hasNumericTimes(times)) return null;

  const asHistory = {
    time_labels: times as number[],
    chart_data: data.chart_data,
  } as HistoryChartData;

  if (!hasPlottableHistory(asHistory)) return null;
  return toChartInput(asHistory);
}

/** Whether every x value is a finite number. False means a pre-v1.5 archive
 * carrying display strings, which the caller reports as "needs repair" rather
 * than as "no data". */
export function hasNumericTimes(times: unknown[]): boolean {
  return times.every((t) => typeof t === "number" && Number.isFinite(t));
}

/** Annotations arrive as a DICT keyed `event_<n>` (common/app.py
 * prepare_annotations), with `xMin`/`xMax` in epoch MILLISECONDS. HistoryChart's
 * x axis is epoch SECONDS -- the same conversion toChartInput does for the
 * data, applied to the markers so they land on the right samples. */
export function toChartAnnotations(
  annotations: Record<string, HistoryAnnotation>,
): HistoryAnnotation[] {
  return Object.values(annotations).map((a) => ({
    ...a,
    xMin: a.xMin / 1000,
    xMax: a.xMax / 1000,
  }));
}
