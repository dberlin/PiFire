import type {
  ControlPatchRequest,
  ControlPatchResponse,
  NotifyUpdate,
} from "../contracts/control.gen";

// Per-probe notification state lives in control["notify_data"] -- runtime
// CONTROL state, not settings. Written with a SINGLE POST /api/control
// carrying only the `notify_updates` key.
//
// Why not the /api/set/notify/{label}/{field}/{value} grammar that already
// exists (common/api_commands.py:501-570)? It is one round trip per FIELD, and
// an edit the user makes as one gesture would land as four -- leaving a window
// in which a target is set but its action is not.
//
// Why not post the whole `notify_data` array, which /api/control also accepts?
// Because that form is applied as a replace: an entry it omits is read as a
// deletion rather than as silence, so a client that built it from a read the
// write queue is invisible to CLOBBERS anything another writer changed in the
// same control cycle -- most visibly, a timer armed from the shell while this
// modal was open. A whole array posted from a queue-blind read cannot say
// WHICH fields it meant to change, so nothing at the drain can tell an
// intentional deletion from an omission. `notify_updates` addresses one entry
// at a time (label + type + the fields being changed), which is exactly what
// the drain needs to compose two writers -- see the notify.set op in
// common/control_delta.py.
//
// Verified live (Stop mode, 2026-07-25): a posted edit becomes visible on the
// next read after ~110 ms -- it is queued, not immediate.

/** POST a minimal control patch. Keep the patch to the keys you actually own:
 *  the server queues it as a MERGE of the whole posted object, so any extra key
 *  is a value patched back over whatever the control loop set meanwhile. */
export async function postControl(baseUrl: string, patch: ControlPatchRequest): Promise<void> {
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
  const body: ControlPatchResponse = await res.json();
  if (body.result !== "success") throw new Error(body.message ?? "control write rejected");
}

export function postNotifyUpdates(baseUrl: string, updates: NotifyUpdate[]): Promise<void> {
  return postControl(baseUrl, { notify_updates: updates });
}
