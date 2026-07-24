import { describe, expect, it, test } from "@rstest/core";
import {
  displayConfigFor,
  initialWorking,
  selectModule,
  setDepValue,
  setDisplayConfig,
} from "./wizardState";
import type { WizardState } from "./wizardTypes";

const base: WizardState = {
  modules_metadata: { grillplatform: {}, display: {}, distance: {}, probes: {} },
  selections: { grillplatform: "pcb_4.x.x", display: "ili9341b", distance: "none", probes: null },
  settings_dep_values: { grillplatform: {}, display: {}, distance: {} } as never,
  display_config: { ili9341b: { rotation: 90 } },
  probe_map: { probe_devices: [], probe_info: [] },
  probe_profiles: [],
  probes_units: "F",
  board_probe_maps: {},
  control_mode: "Stop",
  first_time_setup: true,
  has_draft: false,
};

function baseState(): WizardState {
  return {
    modules_metadata: { grillplatform: {}, probes: {}, display: {}, distance: {} },
    selections: { grillplatform: null, probes: null, display: null, distance: null },
    settings_dep_values: { grillplatform: {}, probes: {}, display: {}, distance: {} },
    display_config: {},
    probe_map: {
      probe_devices: [
        { device: "D1", module: "m", module_filename: "m", ports: ["ADC0"], config: {} },
      ],
      probe_info: [],
    },
    probe_profiles: [{ A: 1, B: 2, C: 3, id: "p1", name: "P1" }],
    probes_units: "C",
    board_probe_maps: {},
    control_mode: "Stop",
    first_time_setup: false,
    has_draft: false,
  };
}

describe("wizardState", () => {
  test("initialWorking copies selections/deps/display_config", () => {
    const w = initialWorking(base);
    expect(w.selections.display).toBe("ili9341b");
    expect(w.selections.probes).toBeNull();
    expect(w.display_config.ili9341b.rotation).toBe(90);
  });

  test("initialWorking produces deep copies (mutating result does not affect source)", () => {
    const w = initialWorking(base);
    w.display_config.ili9341b.rotation = 999;
    expect(base.display_config.ili9341b.rotation).toBe(90);
  });

  test("displayConfigFor returns {} for a never-configured module (KeyError fix)", () => {
    const w = initialWorking(base);
    expect(displayConfigFor(w, "ili9488b")).toEqual({});
    expect(displayConfigFor(w, "ili9341b")).toEqual({ rotation: 90 });
  });

  test("selectModule is immutable and updates the section", () => {
    const w = initialWorking(base);
    const w2 = selectModule(w, "display", "st7789");
    expect(w2.selections.display).toBe("st7789");
    expect(w.selections.display).toBe("ili9341b"); // original untouched
  });

  test("selectModule can set a section to a real module name from null", () => {
    const w = initialWorking(base);
    const w2 = selectModule(w, "probes", "default");
    expect(w2.selections.probes).toBe("default");
    expect(w.selections.probes).toBeNull(); // original untouched
  });

  test("setDepValue is immutable and writes a nested key under the section", () => {
    const w = initialWorking(base);
    const w2 = setDepValue(w, "grillplatform", "i2c_bus", "1");
    expect(w2.settings_dep_values.grillplatform.i2c_bus).toBe("1");
    expect(w.settings_dep_values.grillplatform.i2c_bus).toBeUndefined();
  });

  test("setDepValue accepts a null value", () => {
    const w = initialWorking(base);
    const w2 = setDepValue(w, "display", "usb_device", null);
    expect(w2.settings_dep_values.display.usb_device).toBeNull();
  });

  test("setDisplayConfig writes nested option value immutably", () => {
    const w = initialWorking(base);
    const w2 = setDisplayConfig(w, "ili9341b", "rotation", 180);
    expect(w2.display_config.ili9341b.rotation).toBe(180);
    expect(w.display_config.ili9341b.rotation).toBe(90);
  });

  test("setDisplayConfig on a never-configured module creates a fresh entry (no KeyError)", () => {
    const w = initialWorking(base);
    const w2 = setDisplayConfig(w, "ili9488b", "brightness", 50);
    expect(w2.display_config.ili9488b).toEqual({ brightness: 50 });
    expect(w.display_config.ili9488b).toBeUndefined();
  });

  it("initialWorking deep-clones probe_map and copies units", () => {
    const s = baseState();
    const w = initialWorking(s);
    expect(w.probe_map.probe_devices[0].device).toBe("D1");
    expect(w.probes_units).toBe("C");
    w.probe_map.probe_devices[0].device = "MUT";
    expect(s.probe_map.probe_devices[0].device).toBe("D1"); // no aliasing
  });
});
