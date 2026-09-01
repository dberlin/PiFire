import type { ProbeMap, ProbeProfile } from "@pifire/core/contracts/wizard";
import { describe, expect, it } from "@rstest/core";

import { editProbe } from "../../../../src/helpers/wizard/probeReducer";

const P: ProbeProfile[] = [];
const aux = (label: string, device: string, port: string): ProbeMap["probe_info"][number] => ({
  name: label,
  label,
  type: "Aux",
  enabled: true,
  device,
  port,
  profile: {},
});
const adc = (device: string, ports: string[]): ProbeMap["probe_devices"][number] => ({
  device,
  module: "ads1115_adafruit",
  module_filename: "ads1115_adafruit",
  ports,
  config: {},
});
const virt = (device: string, inputs: string[]): ProbeMap["probe_devices"][number] => ({
  device,
  module: "virtual_average",
  module_filename: "virtual_average",
  ports: ["VIRT0"],
  config: { probes_list: inputs },
});

describe("editProbe virtual-port reposition", () => {
  // 3a: the virtual entry currently sorts BEFORE one of its inputs; editing it
  // relocates it to immediately after the last input found in the backward scan.
  it("editing a virtual (VIRT) probe relocates it after its input probes [3a]", () => {
    const pm: ProbeMap = {
      probe_devices: [adc("ADS", ["ADC0", "ADC1"]), virt("Avg", ["Grill", "Food"])],
      probe_info: [
        aux("Avg", "Avg", "VIRT0"), // index 0 -- BEFORE its inputs (needs fixing)
        aux("Grill", "ADS", "ADC0"), // index 1
        aux("Food", "ADS", "ADC1"), // index 2
      ],
    };
    const r = editProbe(pm, P, "Avg", {
      name: "Avg",
      devicePort: "Avg:VIRT0",
      type: "Aux",
      profileId: "",
      enabled: true,
    });
    expect(r.ok).toBe(true);
    if (r.ok) {
      const labels = r.probeMap.probe_info.map((p) => p.label);
      // Avg must now sort after Food (its last-scanned input).
      expect(labels.indexOf("Avg")).toBeGreaterThan(labels.indexOf("Food"));
      expect(r.probeMap.probe_info).toHaveLength(3);
    }
  });

  it("editing a virtual probe already correctly placed leaves order unchanged [3a in-place]", () => {
    const pm: ProbeMap = {
      probe_devices: [adc("ADS", ["ADC0"]), virt("Avg", ["Grill"])],
      probe_info: [aux("Grill", "ADS", "ADC0"), aux("Avg", "Avg", "VIRT0")],
    };
    const r = editProbe(pm, P, "Avg", {
      name: "Avg",
      devicePort: "Avg:VIRT0",
      type: "Aux",
      profileId: "",
      enabled: true,
    });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.probeMap.probe_info.map((p) => p.label)).toEqual(["Grill", "Avg"]);
  });

  // 3b: an input probe currently sorts AFTER the virtual device that consumes it;
  // editing the input moves it to immediately before the virtual entry.
  it("editing an input probe moves it before the virtual entry that consumes it [3b]", () => {
    const pm: ProbeMap = {
      probe_devices: [adc("ADS", ["ADC0"]), virt("Avg", ["Grill"])],
      probe_info: [
        aux("Avg", "Avg", "VIRT0"), // index 0 -- virtual entry
        aux("Grill", "ADS", "ADC0"), // index 1 -- its input, AFTER it (needs fixing)
      ],
    };
    const r = editProbe(pm, P, "Grill", {
      name: "Grill",
      devicePort: "ADS:ADC0",
      type: "Aux",
      profileId: "",
      enabled: true,
    });
    expect(r.ok).toBe(true);
    if (r.ok) {
      const labels = r.probeMap.probe_info.map((p) => p.label);
      expect(labels.indexOf("Grill")).toBeLessThan(labels.indexOf("Avg"));
      expect(r.probeMap.probe_info).toHaveLength(2);
    }
  });
});
