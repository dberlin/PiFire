// Typed client for the /api/metrics surface.
//
// Two GETs and nothing else -- blueprints/api_metrics registers no POST, so
// there is no write here to get wrong.

import type { MetricsPayload } from "@pifire/core/contracts/content";
import type { ApiEnvelope } from "@pifire/core/contracts/core";

import type { MetricsResult } from "./metricsTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/** Every record, with process_metrics' derived columns already applied. */
export async function fetchMetrics(baseUrl = BASE_URL): Promise<MetricsResult> {
  try {
    const res = await fetch(`${baseUrl}/api/metrics`);
    //  A body that is not JSON (a proxy's HTML 502, a dropped connection
    //  mid-stream) must not mask the status the caller renders.
    const body = (await res.json().catch(() => ({}))) as Partial<ApiEnvelope>;
    return {
      //  The verdict is in the BODY, not the status: common/app.py's
      //  api_response envelope can carry "Error" under a 200.
      ok: res.ok && body.result === "OK",
      status: res.status,
      message: body.message ?? `HTTP ${res.status}`,
      data: (body.data as MetricsPayload | null | undefined) ?? null,
    };
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

/** Href for the CSV. Deliberately not fetched: letting the anchor carry it
 * keeps the file out of JS memory and gives the user the browser's own save
 * dialog -- the same reasoning as adminApi.ts's backupDownloadUrl. */
export const metricsExportUrl = (baseUrl = BASE_URL) => `${baseUrl}/api/metrics/export`;
