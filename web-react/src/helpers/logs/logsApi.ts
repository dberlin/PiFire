// Typed client for GET /api/admin/logs/view.
//
// Unlike the rest of /api/admin/*, this endpoint does NOT use the api_response
// envelope: it send_file()s the stitched bytes so the browser's Range machinery
// works on it, which is what makes tailing a delta rather than a re-download.
// So there is no unpack() here -- the status line and Content-Range ARE the
// protocol.

import type { ApiEnvelope } from "@pifire/core/contracts/core";
import type { LogFamily, LogsMetadata } from "@pifire/core/contracts/operations";

import type { LogDelta } from "./logTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export function logViewUrl(stem: string, baseUrl = BASE_URL): string {
  return `${baseUrl}/api/admin/logs/view?log=${encodeURIComponent(stem)}`;
}

export function logDownloadUrl(stem: string, baseUrl = BASE_URL): string {
  return `${logViewUrl(stem, baseUrl)}&download=1`;
}

/** Range offsets are BYTE offsets. Using String.length here would desync the
 * cursor permanently on the first non-ASCII log line. */
export function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** The `<total>` from either `bytes a-b/<total>` or `bytes * /<total>`. */
function totalFromContentRange(header: string | null): number | null {
  const match = /\/(\d+)\s*$/.exec(header ?? "");
  return match ? Number(match[1]) : null;
}

/** The families the server can serve, for the Log Files picker.
 *
 * This one call DOES use the api_response envelope -- it is the same
 * `GET /api/admin/logs` the admin page reads. A failure resolves to an empty
 * list rather than throwing: the picker is one half of the page, and the events
 * tab beside it does not depend on this listing at all. */
export async function fetchLogFamilies(baseUrl = BASE_URL): Promise<LogFamily[]> {
  const response = await fetch(`${baseUrl}/api/admin/logs`);
  const body = (await response.json().catch(() => ({}))) as Partial<ApiEnvelope>;
  if (!response.ok || body.result !== "OK") return [];
  return (body.data as LogsMetadata | undefined)?.families ?? [];
}

export async function fetchLogWhole(
  stem: string,
  baseUrl = BASE_URL,
): Promise<{ text: string; total: number }> {
  const response = await fetch(logViewUrl(stem, baseUrl));
  const text = await response.text();
  return { text, total: byteLength(text) };
}

/**
 * One tail poll.
 *
 * Rotation is the hazard this function exists to survive. When the family
 * rolls, the stitched stream shrinks and everything after the cursor is
 * different bytes. Two independent signals catch it, because one is not
 * enough: a 416 whose total is below the cursor, and a 206 whose total is
 * below the total we last saw. The second matters because a rotation can leave
 * the new total still above the cursor, in which case the server answers 206
 * quite happily with content from the wrong place.
 */
export async function fetchLogDelta(
  stem: string,
  offset: number,
  lastTotal: number,
  baseUrl = BASE_URL,
): Promise<LogDelta> {
  const response = await fetch(logViewUrl(stem, baseUrl), {
    headers: { Range: `bytes=${offset}-` },
  });
  const total = totalFromContentRange(response.headers.get("Content-Range"));

  if (response.status === 416) {
    if (total !== null && total < offset) {
      const whole = await fetchLogWhole(stem, baseUrl);
      return { kind: "rotated", text: whole.text, nextOffset: whole.total, total: whole.total };
    }
    return { kind: "unchanged", nextOffset: offset, total: total ?? lastTotal };
  }

  if (response.status === 206) {
    if (total !== null && total < lastTotal) {
      const whole = await fetchLogWhole(stem, baseUrl);
      return { kind: "rotated", text: whole.text, nextOffset: whole.total, total: whole.total };
    }
    const text = await response.text();
    return {
      kind: "appended",
      text,
      nextOffset: offset + byteLength(text),
      total: total ?? lastTotal,
    };
  }

  //  200 means the server ignored the Range entirely; treat it as a whole read
  //  rather than appending a duplicate of everything already displayed.
  const text = await response.text();
  return { kind: "rotated", text, nextOffset: byteLength(text), total: byteLength(text) };
}
