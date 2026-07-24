import type {
  Probe,
  ProbeDevice,
  ProbeMap,
  ProbeModuleData,
  ProbeProfile,
  ProbeType,
} from "./probeTypes";

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

export interface ProbeInput {
  name: string;
  devicePort: string;
  type: ProbeType;
  profileId: string;
  enabled: boolean;
}

function buildProbe(profiles: ProbeProfile[], input: ProbeInput): Probe {
  const [device, port] = input.devicePort.split(":");
  const matched = profiles.find((p) => p.id === input.profileId);
  return {
    name: input.name,
    label: alnum(input.name),
    type: input.type,
    enabled: input.enabled,
    device,
    port,
    // Value copy, not a FK -- probes carry a profile snapshot (§5).
    profile: matched ? { ...matched } : {},
  };
}

function primaryCount(list: Probe[]): number {
  return list.filter((p) => p.type === "Primary").length;
}

// FIX 2: zero primaries are valid ONLY when there are zero probes. Any result
// with >=1 probe and 0 primaries is rejected on a delete/type-change path.
function violatesPrimaryRule(list: Probe[]): boolean {
  return list.length > 0 && primaryCount(list) === 0;
}

// A delete/type-change breaks the invariant only when it takes a map that
// HAS a Primary into a >0-probes / 0-Primary state (delta-based, human-approved
// 2026-07-24). Do NOT replace call sites with a raw post-state check.
function breaksPrimaryInvariant(before: Probe[], after: Probe[]): boolean {
  return !violatesPrimaryRule(before) && violatesPrimaryRule(after);
}

export function addProbe(pm: ProbeMap, profiles: ProbeProfile[], input: ProbeInput): ReducerResult {
  if (input.name === "")
    return { ok: false, error: "Probe name is empty. Please enter a probe name." };
  if (!input.devicePort?.includes(":")) {
    return { ok: false, error: "Please select a device and port for this probe." };
  }
  const probe = buildProbe(profiles, input);
  // exactly-one-Primary: a NEW Primary conflicts with any existing Primary.
  if (probe.type === "Primary" && primaryCount(pm.probe_info) > 0) {
    return {
      ok: false,
      error: "There must be only one Primary probe. Change the existing Primary first.",
    };
  }
  if (pm.probe_info.some((p) => p.label === probe.label)) {
    return {
      ok: false,
      error: "Probe name is already used or too similar to another. Choose a different name.",
    };
  }
  // New adds append at the end (legacy branch, routes.py:360-363).
  return { ok: true, probeMap: { ...pm, probe_info: [...pm.probe_info, probe] } };
}

export function editProbe(
  pm: ProbeMap,
  profiles: ProbeProfile[],
  originalLabel: string,
  input: ProbeInput,
): ReducerResult {
  if (input.name === "")
    return { ok: false, error: "Probe name is empty. Please enter a probe name." };
  if (!input.devicePort?.includes(":")) {
    return { ok: false, error: "Please select a device and port for this probe." };
  }
  const found = pm.probe_info.findIndex((p) => p.label === originalLabel);
  if (found === -1) return { ok: false, error: "Error editing probe. Please try again." };
  const probe = buildProbe(profiles, input);
  // exactly-one-Primary, skipping the probe being edited.
  if (probe.type === "Primary") {
    const otherPrimary = pm.probe_info.some((p, i) => i !== found && p.type === "Primary");
    if (otherPrimary)
      return {
        ok: false,
        error: "There must be only one Primary probe. Change the existing Primary first.",
      };
  }
  // A rename must not collide with a DIFFERENT existing probe.
  if (
    probe.label !== originalLabel &&
    pm.probe_info.some((p, i) => i !== found && p.label === probe.label)
  ) {
    return {
      ok: false,
      error: "Probe name is already used or too similar to another. Choose a different name.",
    };
  }
  // Rename cascade into virtual probes_list (§3 pre-step): rewrite the old
  // label to the new one everywhere it is consumed.
  const probe_devices =
    probe.label === originalLabel
      ? pm.probe_devices
      : pm.probe_devices.map((d) => {
          if (!isVirtualDevice(d)) return d;
          const list = (d.config.probes_list as string[] | undefined) ?? [];
          if (!list.includes(originalLabel)) return d;
          return {
            ...d,
            config: {
              ...d.config,
              probes_list: list.map((l) => (l === originalLabel ? probe.label : l)),
            },
          };
        });
  // Virtual-port reposition (§3 branches 3a/3b/3c), reproducing
  // blueprints/probeconfig/routes.py:321-358 exactly. Build the working
  // probe_info as a mutable copy; the reposition branches splice it in
  // place, mirroring legacy list.insert/pop index arithmetic.
  const info = [...pm.probe_info];

  if (probe.port.includes("VIRT")) {
    // 3a: this probe IS a virtual device's output entry. Ensure its config
    // entry sorts after every one of that device's input probes.
    const owning = probe_devices.find((d) => isVirtualDevice(d) && d.device === probe.device);
    const inputProbes = (owning?.config.probes_list as string[] | undefined) ?? [];
    for (let i = info.length - 1; i >= 0; i--) {
      if (i === found) {
        // Own entry reached first (by index, not label) -- already correct.
        info[found] = probe;
        break;
      }
      if (inputProbes.includes(info[i].label)) {
        // Hit an input probe at a higher index -- relocate right after it.
        info.splice(i + 1, 0, probe);
        info.splice(found, 1);
        break;
      }
    }
  } else {
    // Does this probe feed any virtual device? (§3 in_virtual_device)
    const consuming = probe_devices
      .filter(
        (d) =>
          isVirtualDevice(d) &&
          ((d.config.probes_list as string[] | undefined) ?? []).includes(probe.label),
      )
      .map((d) => d.device);
    if (consuming.length > 0) {
      // 3b: ensure this input's entry sorts before the consuming virtual entry.
      for (let i = 0; i < info.length; i++) {
        if (info[i].label === originalLabel) {
          info[i] = probe; // own slot reached first -- already correct.
          break;
        }
        if (consuming.includes(info[i].device)) {
          info.splice(i, 0, probe); // insert before the virtual entry
          info.splice(found + 1, 1); // +1: the insert shifted the stale copy up
          break;
        }
      }
    } else {
      // 3c: ordinary probe -- in-place replace.
      info[found] = probe;
    }
  }

  const probe_info = info;
  // FIX 2 is delta-based: reject only when THIS edit is what breaks the
  // invariant (was compliant, would become non-compliant). A pre-existing
  // out-of-invariant state (e.g. a fixture with no Primary at all) is left
  // alone -- the guard exists to stop actions from causing the violation,
  // not to retroactively re-block every other field on an already-broken map.
  if (breaksPrimaryInvariant(pm.probe_info, probe_info)) {
    return { ok: false, error: "At least one probe must be Primary while probes are configured." };
  }
  return { ok: true, probeMap: { probe_devices, probe_info } };
}

export function deleteProbe(pm: ProbeMap, label: string): ReducerResult {
  const probe_info = pm.probe_info.filter((p) => p.label !== label);
  // FIX 2 (delta-based, see editProbe above): only reject when deleting is
  // what pushes a compliant map into zero-Primary-with-probes-remaining.
  if (breaksPrimaryInvariant(pm.probe_info, probe_info)) {
    return {
      ok: false,
      error:
        "At least one probe must be Primary while probes are configured. Reassign Primary before deleting.",
    };
  }
  const probe_devices = pm.probe_devices.map((d) => {
    if (!isVirtualDevice(d)) return d;
    const list = (d.config.probes_list as string[] | undefined) ?? [];
    if (!list.includes(label)) return d;
    return { ...d, config: { ...d.config, probes_list: list.filter((l) => l !== label) } };
  });
  return { ok: true, probeMap: { probe_devices, probe_info } };
}
