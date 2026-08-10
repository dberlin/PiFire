import { describe, expect, it } from "@rstest/core";
import { readSelected } from "../../../../src/helpers/settings/controllerSelection";
import type { ControllerCatalog } from "../../../../src/helpers/settings/controllerTypes.gen";
import type { SettingsSchema } from "../../../../src/helpers/settings/settingsTypes.gen";
const definitionFields = {
  attributions: [],
  author: "Test",
  contributors: [],
  image: "test.png",
  link: "",
  module_name: "test",
};

// `pid` is declared FIRST throughout, so a test asserting the fallback and a
// test asserting the saved selection can never accidentally agree.
const meta: ControllerCatalog = {
  metadata: {
    pid: { ...definitionFields, friendly_name: "PID Standard", description: "", config: [] },
    mpc: { ...definitionFields, friendly_name: "MPC", description: "", config: [] },
  },
};

describe("readSelected", () => {
  it("returns the saved selection when the catalog declares it", () => {
    expect(readSelected({ controller: { selected: "mpc" } } as SettingsSchema, meta)).toBe("mpc");
  });

  // A settings blob outlives the build that wrote it: an install can be
  // downgraded, or a third-party controller removed, leaving `selected` naming
  // something this build no longer ships. Falling back to the catalog's first
  // entry is what keeps the Controller tab rendering a real controller's
  // options instead of an empty form.
  it("falls back to the first controller when the saved selection is not in the catalog", () => {
    expect(readSelected({ controller: { selected: "nonesuch" } } as SettingsSchema, meta)).toBe(
      "pid",
    );
  });

  it("falls back to the first controller when nothing is saved", () => {
    expect(readSelected({ controller: {} } as SettingsSchema, meta)).toBe("pid");
    expect(readSelected({} as SettingsSchema, meta)).toBe("pid");
  });

  // Settings come from SQLite, not from this type system, so `selected` can be
  // any JSON value; anything that is not a string names no controller.
  it("falls back to the first controller when the saved selection is not a string", () => {
    expect(readSelected({ controller: { selected: 3 } } as unknown as SettingsSchema, meta)).toBe(
      "pid",
    );
  });

  // getControllerMetadata fails OPEN with null, and an empty catalog has no
  // first entry either -- with no catalog there is no controller to name.
  it("names no controller when there is no catalog", () => {
    expect(readSelected({ controller: { selected: "mpc" } } as SettingsSchema, null)).toBe("");
    expect(
      readSelected({ controller: { selected: "mpc" } } as SettingsSchema, { metadata: {} }),
    ).toBe("");
  });
});
