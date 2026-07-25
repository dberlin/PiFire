import { describe, expect, it } from "@rstest/core";
import { hasDcFan } from "./platform";
import type { Settings } from "./settingsApi";

describe("hasDcFan", () => {
  it("is true when platform.dc_fan is true", () => {
    expect(hasDcFan({ platform: { dc_fan: true } } as Settings)).toBe(true);
  });

  it("is false when platform.dc_fan is false", () => {
    expect(hasDcFan({ platform: { dc_fan: false } } as Settings)).toBe(false);
  });

  // The Setup Wizard DERIVES dc_fan for x86_numato / ft232h_relay
  // (PlatformTab.tsx's own note), so an absent field means "not a DC-fan
  // build" — it must read as AC, never as "unknown, so show the controls".
  it("is false when dc_fan is absent", () => {
    expect(hasDcFan({ platform: {} } as Settings)).toBe(false);
  });

  it("is false when platform itself is absent", () => {
    expect(hasDcFan({} as Settings)).toBe(false);
  });
});
