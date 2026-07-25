const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/**
 * One plotted sample. `x` is epoch MILLISECONDS (the JS convention the
 * history store already uses: `common/datastore_accessors.py` writes
 * timestamps as `int(time.time() * 1000)`). uPlot wants epoch SECONDS, so
 * anything feeding HistoryChart must divide by 1000 -- see
 * components/history/historyAdapter.ts.
 */
export interface HistoryPoint {
  x: number;
  y: number | null;
}

/** A Chart.js dataset. Only the keys this UI reads are typed; the payload
 * carries ~15 more Chart.js styling keys (pointRadius, borderDash, ...) that
 * uPlot has no use for. `hidden` mirrors `not probe_config[probe]["enabled"]`
 * -- i.e. a probe the user switched off in Settings.
 *
 * An empty history yields empty arrays (`time_labels: []` and `data: []` on
 * every dataset); the page renders an empty state for that rather than
 * plotting a fabricated point. */
export interface HistoryDataset {
  label: string;
  data: HistoryPoint[];
  borderColor?: string;
  hidden?: boolean;
}

/** Probe key -> index into `chart_data`, grouped by series role. */
export interface HistoryProbeMapper {
  probes: Record<string, number>;
  targets: Record<string, number>;
  primarysp: Record<string, number>;
}

/** Probe key -> display label, same grouping as `probe_mapper`. */
export interface HistoryGraphLabels {
  probes: Record<string, string>;
  targets: Record<string, string>;
  primarysp: Record<string, string>;
}

/** A Chart.js line annotation for a mode change (`prepare_annotations` in
 * common/app.py). Keyed `event_<n>` -- a DICT, not a list. HistoryChart has
 * no annotation support yet, so these are carried but not drawn. */
export interface HistoryAnnotation {
  type: string;
  xMin: number;
  xMax: number;
  borderColor: string;
  label?: { content?: string };
}

export interface HistoryChartData {
  /** Epoch MILLISECONDS -- see HistoryPoint. Empty when there is no history. */
  time_labels: number[];
  chart_data: HistoryDataset[];
  probe_mapper: HistoryProbeMapper;
  graph_labels: HistoryGraphLabels;
  annotations: Record<string, HistoryAnnotation>;
  minutes: number;
}

/**
 * GET /api/history/chart -- the read-only React endpoint (blueprints/
 * api_history/routes.py). Deliberately not the legacy POST /history/refresh,
 * which persists the requested window into settings as a side effect.
 *
 * `minutes` is clamped to >= 1 because the endpoint 400s on anything below
 * that, and the page's number input can transiently produce 0 while being
 * edited. Omitting it lets the server use the user's saved window.
 */
export async function fetchHistoryChart(
  baseUrl: string = BASE_URL,
  minutes?: number,
): Promise<HistoryChartData> {
  const qs = minutes === undefined ? "" : `?minutes=${Math.max(1, Math.round(minutes))}`;
  const res = await fetch(`${baseUrl}/api/history/chart${qs}`);
  if (!res.ok) throw new Error(`GET /api/history/chart failed: HTTP ${res.status}`);
  return (await res.json()) as HistoryChartData;
}
