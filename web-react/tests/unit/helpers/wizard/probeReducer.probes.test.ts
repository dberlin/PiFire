import { expect, it } from "@rstest/core";
import { addProbe, deleteProbe, editProbe } from "../../../../src/helpers/wizard/probeReducer";
import type { ProbeMap, ProbeProfile } from "../../../../src/helpers/contracts/wizard.gen";

const PROFILES: ProbeProfile[] = [{ A: 1, B: 2, C: 3, id: "PT-1000", name: "PT-1000" }];
const dev = (device: string, ports: string[]): ProbeMap["probe_devices"][number] => ({
  device,
  module: "ads1115_adafruit",
  module_filename: "ads1115_adafruit",
  ports,
  config: {},
});

it("addProbe builds label, splits device:port, value-copies the profile object", () => {
  const pm: ProbeMap = { probe_devices: [dev("ADS1115", ["ADC0"])], probe_info: [] };
  const r = addProbe(pm, PROFILES, {
    name: "Grill 1",
    devicePort: "ADS1115:ADC0",
    type: "Primary",
    profileId: "PT-1000",
    enabled: true,
  });
  expect(r.ok).toBe(true);
  if (r.ok) {
    const p = r.probeMap.probe_info[0];
    expect(p.label).toBe("Grill1");
    expect(p.device).toBe("ADS1115");
    expect(p.port).toBe("ADC0");
    expect(p.profile).toEqual({ A: 1, B: 2, C: 3, id: "PT-1000", name: "PT-1000" });
  }
});

it("addProbe rejects an empty name", () => {
  const pm: ProbeMap = { probe_devices: [dev("ADS1115", ["ADC0"])], probe_info: [] };
  expect(
    addProbe(pm, PROFILES, {
      name: "",
      devicePort: "ADS1115:ADC0",
      type: "Food",
      profileId: "PT-1000",
      enabled: true,
    }).ok,
  ).toBe(false);
});

it("addProbe rejects an empty devicePort", () => {
  const pm: ProbeMap = { probe_devices: [dev("ADS1115", ["ADC0"])], probe_info: [] };
  expect(
    addProbe(pm, PROFILES, {
      name: "Grill 1",
      devicePort: "",
      type: "Food",
      profileId: "PT-1000",
      enabled: true,
    }).ok,
  ).toBe(false);
});

it("addProbe blocks a second Primary", () => {
  let pm: ProbeMap = { probe_devices: [dev("ADS1115", ["ADC0", "ADC1"])], probe_info: [] };
  pm = (
    addProbe(pm, PROFILES, {
      name: "Grill",
      devicePort: "ADS1115:ADC0",
      type: "Primary",
      profileId: "PT-1000",
      enabled: true,
    }) as { probeMap: ProbeMap }
  ).probeMap;
  const r = addProbe(pm, PROFILES, {
    name: "Grill2",
    devicePort: "ADS1115:ADC1",
    type: "Primary",
    profileId: "PT-1000",
    enabled: true,
  });
  expect(r.ok).toBe(false);
});

it("addProbe permits zero primaries transiently (Food first) — add is not primary-guarded", () => {
  const pm: ProbeMap = { probe_devices: [dev("ADS1115", ["ADC0"])], probe_info: [] };
  expect(
    addProbe(pm, PROFILES, {
      name: "Food1",
      devicePort: "ADS1115:ADC0",
      type: "Food",
      profileId: "PT-1000",
      enabled: true,
    }).ok,
  ).toBe(true);
});

it("deleteProbe blocks removing the last Primary while other probes remain [FIX 2]", () => {
  const pm: ProbeMap = {
    probe_devices: [dev("ADS1115", ["ADC0", "ADC1"])],
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
      {
        name: "Food",
        label: "Food",
        type: "Food",
        enabled: true,
        device: "ADS1115",
        port: "ADC1",
        profile: {},
      },
    ],
  };
  expect(deleteProbe(pm, "Grill").ok).toBe(false); // would leave 1 probe, 0 primaries
  expect(deleteProbe(pm, "Food").ok).toBe(true); // still 1 primary left
});

it("deleteProbe allows removing the only probe even if Primary (zero probes → zero primaries OK) [FIX 2]", () => {
  const pm: ProbeMap = {
    probe_devices: [dev("ADS1115", ["ADC0"])],
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
  expect(deleteProbe(pm, "Grill").ok).toBe(true);
});

it("deleteProbe scrubs the label from virtual probes_list", () => {
  const pm: ProbeMap = {
    probe_devices: [
      dev("ADS1115", ["ADC0"]),
      {
        device: "Avg",
        module: "virtual_average",
        module_filename: "virtual_average",
        ports: ["VIRT0"],
        config: { probes_list: ["Grill", "Food"] },
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
        name: "Food",
        label: "Food",
        type: "Aux",
        enabled: true,
        device: "ADS1115",
        port: "ADC0",
        profile: {},
      },
    ],
  };
  const r = deleteProbe(pm, "Grill");
  expect(r.ok).toBe(true);
  if (r.ok) expect(r.probeMap.probe_devices[1].config.probes_list as string[]).toEqual(["Food"]);
});

it("editProbe type-change away from the only Primary is blocked while probes remain [FIX 2]", () => {
  const pm: ProbeMap = {
    probe_devices: [dev("ADS1115", ["ADC0", "ADC1"])],
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
      {
        name: "Food",
        label: "Food",
        type: "Food",
        enabled: true,
        device: "ADS1115",
        port: "ADC1",
        profile: {},
      },
    ],
  };
  const r = editProbe(pm, PROFILES, "Grill", {
    name: "Grill",
    devicePort: "ADS1115:ADC0",
    type: "Food",
    profileId: "PT-1000",
    enabled: true,
  });
  expect(r.ok).toBe(false);
});

it("editProbe rejects an empty devicePort", () => {
  const pm: ProbeMap = {
    probe_devices: [dev("ADS1115", ["ADC0"])],
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
  const r = editProbe(pm, PROFILES, "Grill", {
    name: "Grill",
    devicePort: "",
    type: "Primary",
    profileId: "PT-1000",
    enabled: true,
  });
  expect(r.ok).toBe(false);
});

it("editProbe in place replaces an ordinary (non-virtual) probe", () => {
  const pm: ProbeMap = {
    probe_devices: [dev("ADS1115", ["ADC0"])],
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
  const r = editProbe(pm, PROFILES, "Grill", {
    name: "Grill Renamed",
    devicePort: "ADS1115:ADC0",
    type: "Primary",
    profileId: "PT-1000",
    enabled: false,
  });
  expect(r.ok).toBe(true);
  if (r.ok) {
    expect(r.probeMap.probe_info).toHaveLength(1);
    expect(r.probeMap.probe_info[0].label).toBe("GrillRenamed");
    expect(r.probeMap.probe_info[0].enabled).toBe(false);
  }
});
