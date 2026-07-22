export type Settings = Record<string, any>;
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

export async function getMode(baseUrl: string): Promise<string> {
  try {
    const res = await fetch(buildSettingsUrl(baseUrl, "get/mode"));
    if (!res.ok) return "";
    const body = (await res.json()) as { data?: { mode?: string } };
    return body.data?.mode ?? "";
  } catch {
    return ""; // mode-gating fails open to "unknown"; History tab treats non-"Stop" as gated
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
