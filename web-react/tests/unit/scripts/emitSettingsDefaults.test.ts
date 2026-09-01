import { describe, expect, it } from "@rstest/core";

import { emitSettingsDefaults } from "../../../scripts/emitSettingsDefaults";

const SCHEMA = {
  properties: {
    shutdown: { default: { auto_power_off: false, shutdown_duration: 240 } },
    startup: {
      default: { duration: 240, smartstart: { enabled: false, exit_temp: 120 } },
    },
    versions: { $ref: "#/$defs/Versions" },
    server_info: { $ref: "#/$defs/ServerInfo" },
  },
};

describe("emitSettingsDefaults", () => {
  it("emits each section's resolved default, nested", () => {
    const out = emitSettingsDefaults(SCHEMA);
    expect(out).toContain('"shutdown_duration": 240');
    expect(out).toContain('"exit_temp": 120');
  });

  it("skips sections the schema gives no default", () => {
    // versions/server_info/lastupdated are generated per install -- emitting a
    // default for them would invent a value the backend never produces.
    const out = emitSettingsDefaults(SCHEMA);
    expect(out).not.toContain("versions");
    expect(out).not.toContain("server_info");
  });

  it("marks the exported constant `as const`, a compile-time immutability hint", () => {
    const out = emitSettingsDefaults(SCHEMA);
    expect(out).toContain("export const SETTINGS_DEFAULTS =");
    expect(out).toContain("as const");
  });

  it("carries the do-not-edit banner", () => {
    expect(emitSettingsDefaults(SCHEMA)).toContain("do not edit");
  });
});
