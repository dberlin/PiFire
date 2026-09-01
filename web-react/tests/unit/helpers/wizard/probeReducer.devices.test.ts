import type { ProbeMap, ProbeModuleData } from "@pifire/core/contracts/wizard";
import { expect, it } from "@rstest/core";

import {
  addDevice,
  alnum,
  availableProbes,
  deleteDevice,
  devicePortOptions,
  editDevice,
  isVirtualDevice,
} from "../../../../src/helpers/wizard/probeReducer";

const ADS_MODULE: ProbeModuleData = {
  friendly_name: "ADS1115 Adafruit",
  filename: "ads1115_adafruit",
  device_specific: { ports: ["ADC0", "ADC1"], type: "adc", config: [] },
};
const empty = (): ProbeMap => ({ probe_devices: [], probe_info: [] });

it("alnum strips punctuation and spaces", () => {
  expect(alnum("ADS 1115!")).toBe("ADS1115");
  expect(alnum("---")).toBe("");
});

it("isVirtualDevice matches on module-key substring", () => {
  expect(
    isVirtualDevice({
      device: "V",
      module: "virtual_average",
      module_filename: "",
      ports: [],
      config: {},
    }),
  ).toBe(true);
  expect(
    isVirtualDevice({
      device: "A",
      module: "ads1115_adafruit",
      module_filename: "",
      ports: [],
      config: {},
    }),
  ).toBe(false);
});

it("addDevice copies ports from the manifest and stores sanitized name", () => {
  const r = addDevice(empty(), {
    name: "ADS 1115",
    module: "ads1115_adafruit",
    moduleData: ADS_MODULE,
    config: { i2c_bus_addr: "0x48" },
  });
  expect(r.ok).toBe(true);
  if (r.ok) {
    expect(r.probeMap.probe_devices[0].device).toBe("ADS1115");
    expect(r.probeMap.probe_devices[0].ports).toEqual(["ADC0", "ADC1"]);
    expect(r.probeMap.probe_devices[0].config.i2c_bus_addr).toBe("0x48");
  }
});

it("addDevice rejects a duplicate sanitized name", () => {
  const pm = (
    addDevice(empty(), {
      name: "ADS1115",
      module: "ads1115_adafruit",
      moduleData: ADS_MODULE,
      config: {},
    }) as {
      probeMap: ProbeMap;
    }
  ).probeMap;
  const r = addDevice(pm, {
    name: "ADS-1115",
    module: "ads1115_adafruit",
    moduleData: ADS_MODULE,
    config: {},
  });
  expect(r.ok).toBe(false);
});

it("addDevice rejects an all-punctuation name (empty after sanitize) [FIX 3]", () => {
  const r = addDevice(empty(), {
    name: "---",
    module: "ads1115_adafruit",
    moduleData: ADS_MODULE,
    config: {},
  });
  expect(r.ok).toBe(false);
});

it("editDevice keeps module/ports immutable and cascades the rename to probes [FIX 1]", () => {
  let pm = (
    addDevice(empty(), {
      name: "ADS1115",
      module: "ads1115_adafruit",
      moduleData: ADS_MODULE,
      config: {},
    }) as {
      probeMap: ProbeMap;
    }
  ).probeMap;
  pm = {
    ...pm,
    probe_info: [
      {
        name: "Grill",
        label: "Grill",
        type: "Primary",
        enabled: true,
        device: "ADS1115",
        port: "ADC0",
        profile: {},
      },
    ],
  };
  const r = editDevice(pm, { originalName: "ADS1115", newName: "Main ADC", config: {} });
  expect(r.ok).toBe(true);
  if (r.ok) {
    expect(r.probeMap.probe_devices[0].device).toBe("MainADC");
    expect(r.probeMap.probe_devices[0].ports).toEqual(["ADC0", "ADC1"]); // immutable
    expect(r.probeMap.probe_info[0].device).toBe("MainADC"); // cascaded
  }
});

it("editDevice rejects a rename to an existing different device's name", () => {
  let pm = empty();
  pm = (
    addDevice(pm, {
      name: "ADS1115",
      module: "ads1115_adafruit",
      moduleData: ADS_MODULE,
      config: {},
    }) as { probeMap: ProbeMap }
  ).probeMap;
  pm = (
    addDevice(pm, {
      name: "ADS2",
      module: "ads1115_adafruit",
      moduleData: ADS_MODULE,
      config: {},
    }) as { probeMap: ProbeMap }
  ).probeMap;
  const r = editDevice(pm, { originalName: "ADS1115", newName: "ADS2", config: {} });
  expect(r.ok).toBe(false);
});

it("editDevice returns ok:false when originalName matches no device", () => {
  const r = editDevice(empty(), { originalName: "NoSuchDevice", newName: "New", config: {} });
  expect(r.ok).toBe(false);
});

it("editDevice rename cascades to probe_info[].device even when a virtual device is present", () => {
  const pm: ProbeMap = {
    probe_devices: [
      {
        device: "ADS1115",
        module: "ads1115_adafruit",
        module_filename: "ads1115_adafruit",
        ports: ["ADC0"],
        config: {},
      },
      {
        device: "Avg",
        module: "virtual_average",
        module_filename: "virtual_average",
        ports: ["VIRT0"],
        config: { probes_list: ["Grill"] },
      },
    ],
    probe_info: [
      {
        name: "Grill",
        label: "Grill",
        type: "Aux",
        enabled: true,
        device: "ADS1115",
        port: "ADC0",
        profile: {},
      },
    ],
  };
  const r = editDevice(pm, { originalName: "ADS1115", newName: "ADC One", config: {} });
  expect(r.ok).toBe(true);
  if (r.ok) expect(r.probeMap.probe_info[0].device).toBe("ADCOne");
});

it("deleteDevice cascades probe deletion AND scrubs virtual probes_list [FIX 4]", () => {
  const pm: ProbeMap = {
    probe_devices: [
      {
        device: "ADS1115",
        module: "ads1115_adafruit",
        module_filename: "ads1115_adafruit",
        ports: ["ADC0"],
        config: {},
      },
      {
        device: "Avg",
        module: "virtual_average",
        module_filename: "virtual_average",
        ports: ["VIRT0"],
        config: { probes_list: ["Grill", "Probe2"] },
      },
    ],
    probe_info: [
      {
        name: "Grill",
        label: "Grill",
        type: "Aux",
        enabled: true,
        device: "ADS1115",
        port: "ADC0",
        profile: {},
      },
      {
        name: "Probe-2",
        label: "Probe2",
        type: "Aux",
        enabled: true,
        device: "Avg",
        port: "VIRT0",
        profile: {},
      },
    ],
  };
  const out = deleteDevice(pm, "ADS1115");
  expect(out.probe_devices.map((d) => d.device)).toEqual(["Avg"]);
  expect(out.probe_info.map((p) => p.label)).toEqual(["Probe2"]);
  expect(out.probe_devices[0].config.probes_list as string[]).toEqual(["Probe2"]); // "Grill" scrubbed
});

it("devicePortOptions builds device:port pairs", () => {
  const pm: ProbeMap = {
    probe_devices: [
      { device: "ADS1115", module: "m", module_filename: "m", ports: ["ADC0", "ADC1"], config: {} },
    ],
    probe_info: [],
  };
  expect(devicePortOptions(pm)).toEqual([
    { value: "ADS1115:ADC0", label: "ADS1115 -> ADC0" },
    { value: "ADS1115:ADC1", label: "ADS1115 -> ADC1" },
  ]);
});

it("availableProbes returns probe labels", () => {
  const pm: ProbeMap = {
    probe_devices: [],
    probe_info: [
      {
        name: "Grill",
        label: "Grill",
        type: "Primary",
        enabled: true,
        device: "D",
        port: "ADC0",
        profile: {},
      },
    ],
  };
  expect(availableProbes(pm)).toEqual(["Grill"]);
});
