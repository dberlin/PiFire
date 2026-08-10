import type {
  ControllerCatalog,
  ModeResponse,
  SaveFieldError,
  SettingsFlag,
  SettingsResponse,
  SettingsUpdateResponse,
} from "./controllerTypes.gen";
import type { SettingsSchema } from "./settingsTypes.gen";

export function buildSettingsUrl(baseUrl: string, path: string): string {
  return `${baseUrl}/api/${path}`;
}

export async function getSettings(baseUrl: string): Promise<SettingsSchema> {
  const res = await fetch(buildSettingsUrl(baseUrl, "settings"));
  if (!res.ok) throw new Error(`GET /api/settings failed: HTTP ${res.status}`);
  const body = (await res.json()) as SettingsResponse;
  return body.settings;
}

export async function getControllerMetadata(baseUrl: string): Promise<ControllerCatalog | null> {
  try {
    const res = await fetch(buildSettingsUrl(baseUrl, "controller_metadata"));
    if (!res.ok) return null;
    return (await res.json()) as ControllerCatalog;
  } catch {
    return null; // fail-open: Controller tab renders an "unavailable" state
  }
}

export async function getMode(baseUrl: string): Promise<string> {
  try {
    const res = await fetch(buildSettingsUrl(baseUrl, "get/mode"));
    if (!res.ok) return "";
    const body = (await res.json()) as ModeResponse;
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
): Promise<{ ok: boolean; message: string; errors: SaveFieldError[]; data?: SettingsSchema }> {
  try {
    const res = await fetch(buildSettingsUrl(baseUrl, "settings_update"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: delta, flags }),
    });
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, errors: [] };
    const body = (await res.json()) as SettingsUpdateResponse;
    return {
      ok: body.result === "success",
      message: body.message ?? "",
      errors: body.errors ?? [],
      data: body.result === "success" ? (body.data as SettingsSchema) : undefined,
    };
  } catch (e) {
    return {
      ok: false,
      message: e instanceof Error ? e.message : "network error",
      errors: [],
    };
  }
}
