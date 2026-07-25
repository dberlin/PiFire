// Per-probe notification state lives in control["notify_data"] -- runtime
// CONTROL state, not settings. Read with GET /api/get/notify, written with a
// SINGLE POST /api/control carrying only the notify_data key.
//
// Why not the /api/set/notify/{label}/{field}/{value} grammar that already
// exists (common/api_commands.py:420-476)? Because every web-process MERGE write
// queues the WHOLE control dict (common/datastore_accessors.py:75-77), the drain
// applies each queued dict with SQLite json_patch (:120-122) -- RFC 7396, which
// REPLACES arrays wholesale -- and read_control() never sees the pending queue
// (:55-61). notify_data is an array, so four sequential field writes inside one
// control cycle each queue a stale snapshot and the last one silently wins:
// three edits vanish. One POST carrying the whole array is atomic instead.
//
// Verified live (Stop mode, 2026-07-25): GET /api/get/notify returns exactly the
// array that /api/current exposes as notify_data (deep-equal), and a posted edit
// becomes visible on the next GET after ~110 ms -- it is queued, not immediate.

export interface NotifyEntry {
  label: string;
  type: string; // "probe" | "probe_limit_high" | "probe_limit_low" | "timer" | "hopper" | "test"
  req: boolean;
  shutdown: boolean;
  keep_warm?: boolean;
  reignite?: boolean;
  target?: number;
  eta?: number | null;
  condition?: string;
  triggered?: boolean;
  // Index signature: entries carry per-type extras (name, last_check, ...) that
  // MUST survive the round trip untouched -- we post the whole array back.
  [k: string]: unknown;
}

export async function getNotifyData(baseUrl: string): Promise<NotifyEntry[]> {
  const res = await fetch(`${baseUrl}/api/get/notify`);
  if (!res.ok) throw new Error(`GET /api/get/notify failed: HTTP ${res.status}`);
  const body = (await res.json()) as { result?: string; data?: unknown };
  if (body.result !== "OK" || !Array.isArray(body.data)) {
    // Deliberately throws rather than returning []: the caller posts this array
    // straight back, so an empty fallback would wipe every notification set.
    throw new Error("GET /api/get/notify returned no notify_data");
  }
  return body.data as NotifyEntry[];
}

/** POST a minimal control patch. Keep the patch to the keys you actually own:
 *  the server queues it as a MERGE of the whole posted object, so any extra key
 *  is a value patched back over whatever the control loop set meanwhile. */
export async function postControl(baseUrl: string, patch: Record<string, unknown>): Promise<void> {
  const res = await fetch(`${baseUrl}/api/control`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`POST /api/control failed: HTTP ${res.status}`);
  // This endpoint answers { result: "success" } with HTTP 201 -- lowercase, NOT
  // the "OK" that common/app.py's api_response envelope uses everywhere else
  // (blueprints/api/routes.py:211). Do NOT route this through command.ts's
  // post(); it tests result === "OK" and would report every save as a failure.
  const body = (await res.json()) as { result?: string; message?: string };
  if (body.result !== "success") throw new Error(body.message ?? "control write rejected");
}

export function postNotifyData(baseUrl: string, entries: NotifyEntry[]): Promise<void> {
  // eta is null on an idle probe entry (common/defaults.py:520) and is re-sent
  // as null here. That is safe: strip_null_members (common/common.py:179-207)
  // returns lists unchanged, so json_patch replaces the array verbatim and the
  // "stripped null member(s)" diagnostic does not fire. Confirmed against a live
  // control.log -- no new ERROR line after a round-tripped write.
  return postControl(baseUrl, { notify_data: entries });
}
