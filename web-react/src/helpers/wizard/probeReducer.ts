import type { ProbeDevice, ProbeMap, ProbeModuleData } from "./probeTypes";

export type ReducerResult = { ok: true; probeMap: ProbeMap } | { ok: false; error: string };

// Python str.isalnum() analog over ASCII device/probe names (matches
// "".join(c for c in name if c.isalnum()), blueprints/probeconfig/routes.py:57).
export function alnum(s: string): string {
  return Array.from(s)
    .filter((c) => /[0-9A-Za-z]/.test(c))
    .join("");
}

// "virtual" in device["module"] -- a substring match on the module KEY (§3),
// NOT the manifest device_specific.type field.
export function isVirtualDevice(d: ProbeDevice): boolean {
  return d.module.includes("virtual");
}

export function availableProbes(pm: ProbeMap): string[] {
  return pm.probe_info.map((p) => p.label);
}

export function devicePortOptions(pm: ProbeMap): { value: string; label: string }[] {
  const opts: { value: string; label: string }[] = [];
  for (const d of pm.probe_devices) {
    for (const port of d.ports) {
      opts.push({ value: `${d.device}:${port}`, label: `${d.device} -> ${port}` });
    }
  }
  return opts;
}

export function addDevice(
  pm: ProbeMap,
  input: {
    name: string;
    module: string;
    moduleData: ProbeModuleData;
    config: Record<string, unknown>;
  },
): ReducerResult {
  const deviceName = alnum(input.name);
  if (input.name === "")
    return { ok: false, error: "Device name is blank. Please enter a device name." };
  // FIX 3: an all-punctuation name sanitizes to "" -- reject (legacy checked
  // only the raw name and let an empty sanitized key through, §9).
  if (deviceName === "")
    return {
      ok: false,
      error: "Device name has no letters or numbers. Please enter a valid name.",
    };
  if (pm.probe_devices.some((d) => d.device === deviceName)) {
    return { ok: false, error: "Device name already exists. Please choose a unique name." };
  }
  const device: ProbeDevice = {
    device: deviceName,
    module: input.module,
    module_filename: input.moduleData.filename ?? input.module,
    ports: [...input.moduleData.device_specific.ports],
    config: { ...input.config },
  };
  return { ok: true, probeMap: { ...pm, probe_devices: [...pm.probe_devices, device] } };
}

export function editDevice(
  pm: ProbeMap,
  input: { originalName: string; newName: string; config: Record<string, unknown> },
): ReducerResult {
  const newName = alnum(input.newName);
  if (input.newName === "")
    return { ok: false, error: "Device name is blank. Please enter a device name." };
  if (newName === "")
    return {
      ok: false,
      error: "Device name has no letters or numbers. Please enter a valid name.",
    };
  const idx = pm.probe_devices.findIndex((d) => d.device === input.originalName);
  if (idx === -1) return { ok: false, error: "Device not found." };
  if (newName !== input.originalName && pm.probe_devices.some((d) => d.device === newName)) {
    return { ok: false, error: "Device name already exists. Please choose a unique name." };
  }
  const original = pm.probe_devices[idx];
  // module/module_filename/ports are immutable on edit (§2 edit_device).
  const updated: ProbeDevice = {
    device: newName,
    module: original.module,
    module_filename: original.module_filename,
    ports: [...original.ports],
    config: { ...input.config },
  };
  const probe_devices = pm.probe_devices.map((d, i) => {
    if (i === idx) return updated;
    // FIX 1 (virtual own device key): a virtual device may reference this
    // device -- but device references live only in probe_info and probes_list,
    // not on another device's own key, so nothing to rewrite on siblings here.
    return d;
  });
  // FIX 1: cascade the rename to every probe pointing at the old device name.
  const probe_info =
    newName === input.originalName
      ? pm.probe_info
      : pm.probe_info.map((p) => (p.device === input.originalName ? { ...p, device: newName } : p));
  return { ok: true, probeMap: { probe_devices, probe_info } };
}

export function deleteDevice(pm: ProbeMap, name: string): ProbeMap {
  const doomed = new Set(pm.probe_info.filter((p) => p.device === name).map((p) => p.label));
  const probe_devices = pm.probe_devices
    .filter((d) => d.device !== name)
    .map((d) => {
      // FIX 4: scrub the cascade-deleted probe labels out of any virtual
      // device's probes_list (legacy leaves them dangling, §2 delete_device).
      if (!isVirtualDevice(d)) return d;
      const list = (d.config.probes_list as string[] | undefined) ?? [];
      const scrubbed = list.filter((label) => !doomed.has(label));
      return scrubbed.length === list.length
        ? d
        : { ...d, config: { ...d.config, probes_list: scrubbed } };
    });
  const probe_info = pm.probe_info.filter((p) => p.device !== name);
  return { probe_devices, probe_info };
}
