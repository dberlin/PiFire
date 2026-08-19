import type { HistoryChartData, HistoryDataset } from "../contracts/content.gen";

/**
 * The API speaks epoch MILLISECONDS (`common/datastore_accessors.py` writes
 * `int(time.time() * 1000)`); the history chart -- uPlot on web, an SVG
 * reimplementation on mobile -- speaks epoch SECONDS (web's tooltip does
 * `new Date(x * 1000)`). Feeding milliseconds straight through renders
 * timestamps ~50,000 years out, on a chart that otherwise looks perfectly
 * fine, so the conversion lives here and is pinned by a test.
 */
export const MS_PER_SECOND = 1000;

/** Fallback stroke for a dataset with no `borderColor`. On web this is the ONE
 * place a palette value is duplicated outside theme.css: uPlot strokes a
 * canvas, and canvas strokes cannot read CSS custom properties. It must stay
 * equal to `--text-dim` / `Theme.dim`, and web-react's src/themeTokens.test.ts
 * fails if it does not. */
const FALLBACK_COLOR = "#8a7f70";

/** One series' worth of shaped chart data: a label, a stroke colour, and
 * values aligned 1:1 with `ChartInput.times`. Platform-agnostic -- neither
 * uPlot nor react-native-svg types leak in here. */
export interface ChartSeries {
  label: string;
  color: string;
  values: (number | null)[];
}

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
export function hasPlottableHistory(
  data: Pick<HistoryChartData, "time_labels" | "chart_data">,
): boolean {
  return data.time_labels.length > 0 && data.chart_data.some((dataset) => dataset.data.length > 0);
}

/**
 * A dataset's points are appended in lockstep with `time_labels`, so index
 * `i` of `data` corresponds to `time_labels[i]`. A probe that is configured
 * but absent from the history rows never gets points appended at all, though,
 * leaving a SHORTER `data` than `time_labels` -- and every chart series must
 * be exactly as long as the x array, so the tail is padded with nulls
 * (rendered as a gap) rather than truncated.
 */
function valuesFor(dataset: HistoryDataset, length: number): (number | null)[] {
  const values: (number | null)[] = [];
  for (let i = 0; i < length; i += 1) {
    values.push(dataset.data[i]?.y ?? null);
  }
  return values;
}

/**
 * Reshapes a `/api/history/chart` payload into a chart's props.
 *
 * Datasets flagged `hidden` are dropped: the flag mirrors
 * `not probe_config[probe]["enabled"]`, i.e. a probe the user switched off in
 * Settings, and the chart has no per-series visibility toggle to defer the
 * decision to.
 */
export function toChartInput(
  data: Pick<HistoryChartData, "time_labels" | "chart_data">,
): ChartInput {
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
