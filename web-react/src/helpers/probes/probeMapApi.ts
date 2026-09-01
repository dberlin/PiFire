import type {
  Probe,
  ProbeDevice,
  ProbeMap,
  ProbeMapErrorResponse,
  ProbeMapRequest,
  ProbeMapResponse,
  ProbeModuleCatalog,
  ProbeProfile,
} from "@pifire/core/contracts/wizard";
import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";

import type { ApplyProbeMapResult } from "./probeMapTypes";

export async function getProbeModules(baseUrl: string): Promise<ProbeModuleCatalog> {
  const res = await fetch(`${baseUrl}/api/probe_modules`);
  if (!res.ok) throw new Error(`GET /api/probe_modules failed: HTTP ${res.status}`);
  const body = await res.json();
  return body.data ?? { modules: {}, requires_install: {} };
}

// The route's four rejection codes, turned into sentences here rather than in
// the component: the codes are a backend contract and belong beside the client
// that speaks it. `bus_conflict` carries its own already-readable detail.
function explain(status: number, body: ProbeMapErrorResponse): string {
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
      body: JSON.stringify({ probe_map: probeMap } satisfies ProbeMapRequest),
    });
    const body: ProbeMapResponse | null = await res.json().catch(() => null);
    if (body?.result === "error") return { ok: false, message: explain(res.status, body) };
    if (!res.ok || body === null) {
      return { ok: false, message: `Probe configuration was not saved (HTTP ${res.status}).` };
    }
    return { ok: true };
  } catch {
    return { ok: false, message: "Could not reach PiFire. The probe configuration was not saved." };
  }
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isProbeProfile(value: unknown): value is ProbeProfile {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    "A" in value &&
    typeof value.A === "number" &&
    Number.isFinite(value.A) &&
    "B" in value &&
    typeof value.B === "number" &&
    Number.isFinite(value.B) &&
    "C" in value &&
    typeof value.C === "number" &&
    Number.isFinite(value.C) &&
    "id" in value &&
    typeof value.id === "string" &&
    "name" in value &&
    typeof value.name === "string"
  );
}

function isProbeDevice(value: unknown): value is ProbeDevice {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    "device" in value &&
    typeof value.device === "string" &&
    "module" in value &&
    typeof value.module === "string" &&
    "module_filename" in value &&
    typeof value.module_filename === "string" &&
    "ports" in value &&
    isStringArray(value.ports) &&
    "config" in value &&
    typeof value.config === "object" &&
    value.config !== null &&
    !Array.isArray(value.config)
  );
}

function isProbe(value: unknown): value is Probe {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    "name" in value &&
    typeof value.name === "string" &&
    "label" in value &&
    typeof value.label === "string" &&
    "type" in value &&
    (value.type === "Primary" || value.type === "Food" || value.type === "Aux") &&
    "enabled" in value &&
    typeof value.enabled === "boolean" &&
    "device" in value &&
    typeof value.device === "string" &&
    "port" in value &&
    typeof value.port === "string" &&
    "profile" in value &&
    typeof value.profile === "object" &&
    value.profile !== null &&
    !Array.isArray(value.profile) &&
    (Object.keys(value.profile).length === 0 || isProbeProfile(value.profile))
  );
}

/** Narrow the settings schema's intentionally loose plugin dictionaries once,
 * at the boundary into the strict generated wizard contract. */
export function readLiveProbeMap(settings: SettingsSchema): ProbeMap {
  const raw = settings?.probe_settings?.probe_map;
  const rawDevices = raw?.probe_devices ?? [];
  const rawProbes = raw?.probe_info ?? [];
  const probeDevices: ProbeDevice[] = [];
  const probeInfo: Probe[] = [];
  for (const value of rawDevices) {
    if (isProbeDevice(value)) probeDevices.push(value);
  }
  for (const value of rawProbes) {
    if (isProbe(value)) probeInfo.push(value);
  }
  return {
    probe_devices: probeDevices.length === rawDevices.length ? probeDevices : [],
    probe_info: probeInfo.length === rawProbes.length ? probeInfo : [],
  };
}

/** Live settings store probe profiles keyed by id; PortForm takes a list. */
export function readLiveProfiles(settings: SettingsSchema): ProbeProfile[] {
  const profiles: ProbeProfile[] = [];
  for (const value of Object.values(settings?.probe_settings?.probe_profiles ?? {})) {
    if (isProbeProfile(value)) profiles.push(value);
  }
  return profiles;
}
