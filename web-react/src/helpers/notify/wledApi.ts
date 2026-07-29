// Typed client for the /api/wled_* action surface (discover / push / test).
//
// Unlike the file endpoints (helpers/files/apiEnvelope.ts) these return a bare
// {result: "success"|"error", message, ...} envelope -- result:"success" on a
// 200, and result:"error" either in a 200 body or alongside a 500 (see
// blueprints/api/routes.py:_api_get_wled_discover / _api_post_wled_push_profiles
// / _api_post_wled_test_profile). We therefore branch on the `result` field,
// not res.ok, and never throw on a result:"error": the card renders the message.
// A network/parse failure becomes a synthesized error result so the caller has
// one uniform shape to display.

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export interface WledDevice {
  ip: string;
  led_count: number;
  name: string;
}

export interface WledDiscoverResult {
  result: "success" | "error";
  message: string;
  devices: WledDevice[];
}

export interface WledActionResult {
  result: "success" | "error";
  message: string;
  profiles_pushed?: number;
}

export async function discoverWled(timeoutSec = 15): Promise<WledDiscoverResult> {
  try {
    const res = await fetch(`${BASE_URL}/api/wled_discover?timeout=${timeoutSec}`);
    const body = (await res.json().catch(() => ({}))) as Partial<WledDiscoverResult>;
    return {
      result: body.result === "success" ? "success" : "error",
      message: body.message ?? `HTTP ${res.status}`,
      devices: body.devices ?? [],
    };
  } catch {
    return {
      result: "error",
      message: "Could not reach PiFire to discover WLED devices.",
      devices: [],
    };
  }
}

export async function pushWledProfiles(
  deviceAddress: string,
  profileNumbers: Record<string, number>,
): Promise<WledActionResult> {
  try {
    const res = await fetch(`${BASE_URL}/api/wled_push_profiles`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_address: deviceAddress, profile_numbers: profileNumbers }),
    });
    const body = (await res.json().catch(() => ({}))) as Partial<WledActionResult>;
    return {
      result: body.result === "success" ? "success" : "error",
      message: body.message ?? `HTTP ${res.status}`,
      profiles_pushed: body.profiles_pushed,
    };
  } catch {
    return { result: "error", message: "Could not reach PiFire to push profiles." };
  }
}

export async function testWledProfile(
  deviceAddress: string,
  profileNumber: number,
): Promise<WledActionResult> {
  try {
    const res = await fetch(`${BASE_URL}/api/wled_test_profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_address: deviceAddress, profile_number: profileNumber }),
    });
    const body = (await res.json().catch(() => ({}))) as Partial<WledActionResult>;
    return {
      result: body.result === "success" ? "success" : "error",
      message: body.message ?? `HTTP ${res.status}`,
    };
  } catch {
    return { result: "error", message: "Could not reach PiFire to test the profile." };
  }
}
