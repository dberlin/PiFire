import type { HistoryChartData } from "../contracts/content.gen";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

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
