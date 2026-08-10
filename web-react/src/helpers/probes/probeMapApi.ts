import type { SettingsSchema } from "../settings/settingsTypes.gen";
import type { ProbeMap, ProbeProfile } from "../wizard/probeTypes";
import type { ApplyProbeMapResult, ProbeModuleCatalog } from "./probeMapTypes";

const EMPTY_MAP: ProbeMap = { probe_devices: [], probe_info: [] };

export async function getProbeModules(baseUrl: string): Promise<ProbeModuleCatalog> {
  const res = await fetch(`${baseUrl}/api/probe_modules`);
  if (!res.ok) throw new Error(`GET /api/probe_modules failed: HTTP ${res.status}`);
  const body = (await res.json()) as { data?: ProbeModuleCatalog };
  return body.data ?? { modules: {}, requires_install: {} };
}

// The route's four rejection codes, turned into sentences here rather than in
// the component: the codes are a backend contract and belong beside the client
// that speaks it. `bus_conflict` carries its own already-readable detail
// (common/i2c_bus.py raises full sentences), so it is passed through.
function explain(
  status: number,
  body: { message?: string; detail?: string; modules?: string[] },
): string {
  switch (body.message) {
    case "system_active":
      return "Stop the grill before changing probe configuration.";
    case "modules_require_install":
      return `These probe modules need the setup wizard to install their dependencies first: ${(
        body.modules ?? []
      ).join(", ")}.`;
    case "bus_conflict":
      return body.detail ?? "This probe configuration conflicts on the I2C bus.";
    case "bad_probe_map":
      return "The probe configuration is malformed and was not saved.";
    default:
      return `Probe configuration was not saved (HTTP ${status}).`;
  }
}

export async function applyProbeMap(
  baseUrl: string,
  probeMap: ProbeMap,
): Promise<ApplyProbeMapResult> {
  try {
    const res = await fetch(`${baseUrl}/api/probe_map`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ probe_map: probeMap }),
    });
    const body = (await res.json().catch(() => ({}))) as {
      message?: string;
      detail?: string;
      modules?: string[];
    };
    if (!res.ok) return { ok: false, message: explain(res.status, body) };
    return { ok: true };
  } catch {
    // The grill going unreachable mid-save must not throw past the tab.
    return { ok: false, message: "Could not reach PiFire. The probe configuration was not saved." };
  }
}

/** The generated Settings type models probe_map as all-optional
 *  (settingsTypes.gen.ts:510) because common/settings_schema.py:229-234 keeps
 *  the device/probe dicts loose. The reducer and both cards need the required
 *  shape from helpers/wizard/probeTypes. This is the ONE place that crossing
 *  happens, so no component ever has to hold both ProbeMap types at once.
 *
 *  The double cast is required, not lazy: the generated members are bare index
 *  signatures ({[k: string]: unknown}[]), which TS says do not "sufficiently
 *  overlap" the required shapes, so a single `as` is a compile error. Same
 *  crossing, same idiom as NotificationsTab.tsx:22. Narrowing it for real would
 *  mean validating every driver-specific device dict in the browser, which is
 *  exactly what common/settings_schema.py:229-234 deliberately declines to do
 *  on the server. */
export function readLiveProbeMap(settings: SettingsSchema): ProbeMap {
  const raw = settings?.probe_settings?.probe_map;
  if (!raw) return EMPTY_MAP;
  return {
    probe_devices: (raw.probe_devices ?? []) as unknown as ProbeMap["probe_devices"],
    probe_info: (raw.probe_info ?? []) as unknown as ProbeMap["probe_info"],
  };
}

/** Live settings store probe_profiles keyed by id; PortForm's picker takes a
 *  list. Same flattening /api/wizard/state does (api_wizard/routes.py:129-130). */
export function readLiveProfiles(settings: SettingsSchema): ProbeProfile[] {
  return Object.values(settings?.probe_settings?.probe_profiles ?? {}) as unknown as ProbeProfile[];
}
