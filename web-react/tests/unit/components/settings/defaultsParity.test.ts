import { describe, expect, it } from "@rstest/core";
import { SETTINGS_DEFAULTS } from "../../../../src/helpers/settings/settingsDefaults.gen";

// A sample of the values the tabs fall back to when the store carries no key
// -- not the whole of SETTINGS_DEFAULTS, just the handful the shutdown/startup
// tabs read. Pinned here so a hand-typed literal cannot drift from the schema
// again: each expected value comes from the generated constant.
describe("tab fallbacks match the schema", () => {
  it("carries the durations the tabs need", () => {
    expect(SETTINGS_DEFAULTS.shutdown.shutdown_duration).toBe(240);
    expect(SETTINGS_DEFAULTS.startup.duration).toBe(240);
  });

  it("carries the startup values the tabs need", () => {
    expect(SETTINGS_DEFAULTS.startup.pwm_duty_cycle).toBe(100);
    expect(SETTINGS_DEFAULTS.startup.smartstart.exit_temp).toBe(120);
    expect(SETTINGS_DEFAULTS.startup.prime_on_startup).toBe(0);
  });
});
