// Write client for the pellet inventory manager.
//
// ONE endpoint, POST /api/pellets, carrying ONE INTENT per request:
// {"action": <name>, "data": {...}}. The server does its own
// read-modify-write of the pellet blob inside the handler
// (common/pellets_actions.py). This client must never post a pellet
// database: write_pellet_db() is a whole-blob overwrite with no merge
// (common/datastore_accessors.py) and the control process writes the same
// blob every 60s and at every mode end (controller/runtime/modes/base.py),
// so a database held across a round trip and posted back discards the
// controller's est_usage/hopper_level updates.
//
// Envelope is common/app.py api_response: {result: "OK"|"Error", message, data}
// -- the same "OK" contract command.ts uses, NOT the lowercase "success"
// that /api/control answers with.

export interface PelletActionResult {
  ok: boolean;
  message: string;
}

type Action =
  | "load_profile"
  | "hopper_check"
  | "edit_brands"
  | "edit_woods"
  | "add_profile"
  | "edit_profile"
  | "delete_profile"
  | "delete_log";

async function post(
  baseUrl: string,
  action: Action,
  data: Record<string, unknown>,
): Promise<PelletActionResult> {
  try {
    const res = await fetch(`${baseUrl}/api/pellets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, data }),
    });
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` };
    const body = (await res.json()) as { result?: string; message?: string };
    return body.result === "OK"
      ? { ok: true, message: "" }
      : { ok: false, message: body.message ?? "Request rejected." };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "network error" };
  }
}

// A `type` alias, not an `interface`, deliberately: only type aliases get an
// implicit index signature, so `ProfileFields & {...}` is assignable to
// post()'s `Record<string, unknown>` payload. As an interface this file does
// not typecheck.
export type ProfileFields = {
  brand_name: string;
  wood_type: string;
  rating: number;
  comments: string;
};

export const loadProfile = (b: string, profile: string) => post(b, "load_profile", { profile });
export const hopperCheck = (b: string) => post(b, "hopper_check", {});
export const editBrands = (b: string, d: { new_brand: string } | { delete_brand: string }) =>
  post(b, "edit_brands", d);
export const editWoods = (b: string, d: { new_wood: string } | { delete_wood: string }) =>
  post(b, "edit_woods", d);
export const addProfile = (b: string, d: ProfileFields & { add_and_load: boolean }) =>
  post(b, "add_profile", d);
export const editProfile = (b: string, d: ProfileFields & { profile: string }) =>
  post(b, "edit_profile", d);
export const deleteProfile = (b: string, profile: string) => post(b, "delete_profile", { profile });
export const deleteLog = (b: string, log_item: string) => post(b, "delete_log", { log_item });
