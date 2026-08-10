import type { DismissWarningsRequest, DismissWarningsResponse } from "../contracts/core.gen";

// Client for the warnings dismiss endpoint.
//
// Modeled on helpers/update/updateApi.ts: a refusal resolves to false rather
// than throwing, because the caller keeps the banner up and lets the user retry
// instead of catching an escape.

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/** Clear the warnings up to and including `throughId` -- the high-water mark
 *  that arrived with the banner being dismissed. Resolves true when the server
 *  confirms; false on any refusal or transport failure. */
export async function dismissWarnings(throughId: number): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/api/dismiss_warnings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ through_id: throughId } satisfies DismissWarningsRequest),
    });
    const body = (await res.json().catch(() => ({}))) as Partial<DismissWarningsResponse>;
    return res.ok && body.result === "OK";
  } catch {
    return false;
  }
}
