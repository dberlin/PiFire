import type { SettingsSchema } from "./settingsTypes.gen";

export type Settings = SettingsSchema;
export type SettingsFlag =
  | "settings_update"
  | "controller_update"
  | "distance_update"
  | "probe_profile_update";

export function buildSettingsUrl(baseUrl: string, path: string): string {
  return `${baseUrl}/api/${path}`;
}

export async function getSettings(baseUrl: string): Promise<Settings> {
  const res = await fetch(buildSettingsUrl(baseUrl, "settings"));
  if (!res.ok) throw new Error(`GET /api/settings failed: HTTP ${res.status}`);
  const body = (await res.json()) as { settings?: Settings };
  return body.settings ?? (body as Settings); // GET /api/settings returns { settings: {...} }
}

export interface ControllerOption {
  option_name: string;
  option_friendly_name: string;
  option_description: string;
  option_type: "float" | "int" | "bool" | "list" | "string" | string;
  option_default: number | boolean | string | null;
  option_min: number | null;
  option_max: number | null;
  // _macro_settings.html:51 forwards this; controllers.json declares steps down
  // to 1e-10, so without it those spinners default to a step of 1.
  option_step?: number | null;
  list_values?: (string | number)[];
  list_labels?: string[];
}
export interface ControllerMetadata {
  metadata: Record<
    string,
    { friendly_name: string; description: string; config: ControllerOption[] }
  >;
}

export async function getControllerMetadata(baseUrl: string): Promise<ControllerMetadata | null> {
  try {
    const res = await fetch(buildSettingsUrl(baseUrl, "controller_metadata"));
    if (!res.ok) return null;
    return (await res.json()) as ControllerMetadata;
  } catch {
    return null; // fail-open: Controller tab renders an "unavailable" state
  }
}

export async function getMode(baseUrl: string): Promise<string> {
  try {
    const res = await fetch(buildSettingsUrl(baseUrl, "get/mode"));
    if (!res.ok) return "";
    const body = (await res.json()) as { data?: { mode?: string } };
    return body.data?.mode ?? "";
  } catch {
    // "" means UNKNOWN, and consumers gate on it -- i.e. this fails CLOSED,
    // not open. That is deliberate: the History tab's extended-data toggle is
    // a data-integrity guard (changing it mid-cook changes the history
    // schema), so when we cannot confirm the grill is stopped we must not
    // allow the change. Consumers should distinguish "" from a real mode when
    // explaining WHY a control is locked.
    return "";
  }
}

export async function applySettings(
  baseUrl: string,
  delta: object,
  flags: SettingsFlag[],
): Promise<{ ok: boolean; message: string; data?: Settings }> {
  try {
    const res = await fetch(buildSettingsUrl(baseUrl, "settings_update"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: delta, flags }),
    });
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` };
    const body = (await res.json()) as { result?: string; message?: string; data?: Settings };
    return { ok: body.result === "success", message: body.message ?? "", data: body.data };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "network error" };
  }
}
