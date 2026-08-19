import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { describe, expect, it } from "@rstest/core";
import { hasDcFan } from "../../../../src/helpers/settings/platform";

describe("hasDcFan", () => {
  it("is true when platform.dc_fan is true", () => {
    expect(hasDcFan({ platform: { dc_fan: true } } as SettingsSchema)).toBe(true);
  });

  it("is false when platform.dc_fan is false", () => {
    expect(hasDcFan({ platform: { dc_fan: false } } as SettingsSchema)).toBe(false);
  });

  // The Setup Wizard DERIVES dc_fan for x86_numato / ft232h_relay
  // (PlatformTab.tsx's own note), so an absent field means "not a DC-fan
  // build" — it must read as AC, never as "unknown, so show the controls".
  it("is false when dc_fan is absent", () => {
    expect(hasDcFan({ platform: {} } as SettingsSchema)).toBe(false);
  });

  it("is false when platform itself is absent", () => {
    expect(hasDcFan({} as SettingsSchema)).toBe(false);
  });
});
