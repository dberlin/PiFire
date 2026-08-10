import { describe, expect, it, test } from "@rstest/core";
import type { ProbeMap, WizardState } from "../../../../src/helpers/contracts/wizard.gen";
import {
  displayConfigFor,
  EMPTY_PROBE_MAP,
  initialWorking,
  replaceProbeMap,
  reseedProbeMapForBoard,
  selectModule,
  setDepValue,
  setDisplayConfig,
  setSectionDepValues,
} from "../../../../src/helpers/wizard/wizardState";

const base: WizardState = {
  modules_metadata: { grillplatform: {}, display: {}, distance: {}, probes: {} },
  selections: { grillplatform: "pcb_4.x.x", display: "ili9341b", distance: "none", probes: null },
  settings_dep_values: { grillplatform: {}, display: {}, distance: {} },
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

describe("wizardState grillplatform helpers", () => {
  const boardA: ProbeMap = {
    probe_devices: [
      { device: "DA", module: "ads1115", module_filename: "ads1115", ports: ["ADC0"], config: {} },
    ],
    probe_info: [],
  };
  const boardB: ProbeMap = {
    probe_devices: [
      { device: "DB", module: "ads1115", module_filename: "ads1115", ports: ["ADC1"], config: {} },
    ],
    probe_info: [],
  };

  test("setSectionDepValues replaces a section's dep map immutably", () => {
    const w = initialWorking(base);
    const w2 = setSectionDepValues(w, "grillplatform", {
      output_auger: "14",
      system_type: "raspberry_pi_all",
    });
    expect(w2.settings_dep_values.grillplatform).toEqual({
      output_auger: "14",
      system_type: "raspberry_pi_all",
    });
    expect(w.settings_dep_values.grillplatform).toEqual({}); // original untouched
  });

  test("replaceProbeMap swaps the probe_map immutably", () => {
    const w = initialWorking(base);
    const w2 = replaceProbeMap(w, boardB);
    expect(w2.probe_map).toEqual(boardB);
    expect(w.probe_map).toEqual({ probe_devices: [], probe_info: [] });
  });

  test("reseedProbeMapForBoard reseeds when fresh + current equals previous board default", () => {
    // user hasn't edited: current == prev board default -> adopt new board
    const result = reseedProbeMapForBoard(boardA, boardA, boardB, true);
    expect(result).toEqual(boardB);
    expect(result).not.toBe(boardB); // deep-cloned, not aliased
  });

  test("reseedProbeMapForBoard treats same-content maps with different key order as equal", () => {
    // Same content as boardA, keys written in a different order. deepEqual must be
    // structural -- a JSON.stringify comparison would see these as different and
    // wrongly SKIP the reseed (preserving `reordered` instead of adopting boardB).
    const reordered: ProbeMap = {
      probe_info: [],
      probe_devices: [
        {
          config: {},
          ports: ["ADC0"],
          module_filename: "ads1115",
          module: "ads1115",
          device: "DA",
        },
      ],
    };
    const result = reseedProbeMapForBoard(reordered, boardA, boardB, true);
    expect(result).toEqual(boardB);
  });

  test("reseedProbeMapForBoard preserves current when the user has edited it", () => {
    const edited: ProbeMap = {
      probe_devices: [
        { device: "EDITED", module: "m", module_filename: "m", ports: [], config: {} },
      ],
      probe_info: [],
    };
    const result = reseedProbeMapForBoard(edited, boardA, boardB, true);
    expect(result).toBe(edited); // unchanged
  });

  test("reseedProbeMapForBoard is a no-op on an existing install", () => {
    const result = reseedProbeMapForBoard(boardA, boardA, boardB, false);
    expect(result).toBe(boardA);
  });

  test("reseedProbeMapForBoard reseeds to EMPTY when the new board has no entry (unedited)", () => {
    // caller passes EMPTY_PROBE_MAP for a board with no manifest entry
    const result = reseedProbeMapForBoard(boardA, boardA, EMPTY_PROBE_MAP, true);
    expect(result).toEqual({ probe_devices: [], probe_info: [] });
    expect(result).not.toBe(EMPTY_PROBE_MAP); // deep-cloned, not aliased to the shared singleton
  });

  test("reseedProbeMapForBoard treats an initial EMPTY previous board correctly", () => {
    // prev selection was null -> prevBoardMap is EMPTY; current also EMPTY -> reseed
    const result = reseedProbeMapForBoard(EMPTY_PROBE_MAP, EMPTY_PROBE_MAP, boardB, true);
    expect(result).toEqual(boardB);
  });
});
