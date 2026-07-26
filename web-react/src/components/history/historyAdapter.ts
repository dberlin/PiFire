import type { HistoryChartData, HistoryDataset } from "../../helpers/history/historyApi";
import type { ChartSeries } from "./HistoryChart";

/**
 * The API speaks epoch MILLISECONDS (`common/datastore_accessors.py` writes
 * `int(time.time() * 1000)`); HistoryChart -- and uPlot underneath it --
 * speaks epoch SECONDS, and its tooltip does `new Date(x * 1000)`. Feeding
 * milliseconds straight through renders timestamps ~50,000 years out, on a
 * chart that otherwise looks perfectly fine, so the conversion lives here and
 * is pinned by a test.
 */
export const MS_PER_SECOND = 1000;

/** Fallback stroke for a dataset with no `borderColor`. This is the ONE place a
 * palette value is duplicated outside theme.css: uPlot strokes a canvas, and
 * canvas strokes cannot read CSS custom properties. It must stay equal to
 * `--text-dim` / `Theme.dim`, and src/themeTokens.test.ts fails if it does not. */
const FALLBACK_COLOR = "#8a7f70";

export interface ChartInput {
  /** Epoch SECONDS. */
  times: number[];
  series: ChartSeries[];
}

/**
 * An empty history yields `time_labels: []` (and `data: []` on every
 * dataset). Callers render an empty state for that instead of mounting a
 * chart over nothing.
 */
export function hasPlottableHistory(data: HistoryChartData): boolean {
  return data.time_labels.length > 0 && data.chart_data.some((ds) => ds.data.length > 0);
}

/**
 * A dataset's points are appended in lockstep with `time_labels`, so index
 * `i` of `data` corresponds to `time_labels[i]`. A probe that is configured
 * but absent from the history rows never gets points appended at all, though,
 * leaving a SHORTER `data` than `time_labels` -- and uPlot requires every
 * series to be exactly as long as the x array, so the tail is padded with
 * nulls (which uPlot renders as a gap) rather than truncated.
 */
function valuesFor(dataset: HistoryDataset, length: number): (number | null)[] {
  const values: (number | null)[] = [];
  for (let i = 0; i < length; i += 1) {
    values.push(dataset.data[i]?.y ?? null);
  }
  return values;
}

/**
 * Reshapes a `/api/history/chart` payload into HistoryChart's props.
 *
 * Datasets flagged `hidden` are dropped: the flag mirrors
 * `not probe_config[probe]["enabled"]`, i.e. a probe the user switched off in
 * Settings, and HistoryChart has no per-series visibility toggle to defer the
 * decision to.
 */
export function toChartInput(data: HistoryChartData): ChartInput {
  const times = data.time_labels.map((ms) => ms / MS_PER_SECOND);
  const series = data.chart_data
    .filter((ds) => !ds.hidden)
    .map((ds) => ({
      label: ds.label,
      color: ds.borderColor || FALLBACK_COLOR,
      values: valuesFor(ds, times.length),
    }));
  return { times, series };
}
