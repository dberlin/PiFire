import type {
  AddPelletProfileData,
  EditPelletBrandsRequest,
  EditPelletProfileData,
  EditPelletWoodsRequest,
  PelletActionRequest,
  PelletActionResponse,
} from "../contracts/control.gen";

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

async function post(baseUrl: string, request: PelletActionRequest): Promise<PelletActionResult> {
  try {
    const res = await fetch(`${baseUrl}/api/pellets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` };
    const body: PelletActionResponse = await res.json();
    return body.result === "OK"
      ? { ok: true, message: "" }
      : { ok: false, message: body.message ?? "Request rejected." };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "network error" };
  }
}

export const loadProfile = (baseUrl: string, profile: string) =>
  post(baseUrl, { action: "load_profile", data: { profile } });
export const hopperCheck = (baseUrl: string) => post(baseUrl, { action: "hopper_check", data: {} });
export const editBrands = (baseUrl: string, data: EditPelletBrandsRequest["data"]) =>
  post(baseUrl, { action: "edit_brands", data });
export const editWoods = (baseUrl: string, data: EditPelletWoodsRequest["data"]) =>
  post(baseUrl, { action: "edit_woods", data });
export const addProfile = (baseUrl: string, data: AddPelletProfileData) =>
  post(baseUrl, { action: "add_profile", data });
export const editProfile = (baseUrl: string, data: EditPelletProfileData) =>
  post(baseUrl, { action: "edit_profile", data });
export const deleteProfile = (baseUrl: string, profile: string) =>
  post(baseUrl, { action: "delete_profile", data: { profile } });
export const deleteLog = (baseUrl: string, log_item: string) =>
  post(baseUrl, { action: "delete_log", data: { log_item } });
