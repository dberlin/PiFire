import type { ControlPatchRequest, ControlPatchResponse } from "./contracts/control.gen";

/** POST a minimal control patch. Keep the patch to the keys you actually own:
 *  the server converts it to validated named delta operations, so unrelated
 *  control-loop updates remain untouched when the queue drains. */
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
  const body = (await res.json()) as ControlPatchResponse;
  if (body.result !== "success") throw new Error(body.message ?? "control write rejected");
}
