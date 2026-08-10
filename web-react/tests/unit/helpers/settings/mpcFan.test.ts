import { describe, expect, it } from "@rstest/core";
import { mpcFanConflict, mpcFanPending } from "../../../../src/helpers/settings/mpcFan";
import type { SettingsSchema } from "../../../../src/helpers/settings/settingsTypes.gen";

const dcFan = (pwmControl: boolean, fanInput: boolean): SettingsSchema =>
  ({
    platform: { dc_fan: true },
    pwm: { pwm_control: pwmControl },
    controller: { selected: "mpc", config: { mpc: { enable_fan_input: fanInput } } },
  }) as unknown as SettingsSchema;

describe("mpcFanConflict", () => {
  it("fires when MPC owns the fan but PWM control is off", () => {
    expect(
      mpcFanConflict({ selected: "mpc", enableFanInput: true, settings: dcFan(false, true) }),
    ).toBe(true);
  });

  it("does not fire when PWM control is on", () => {
    expect(
      mpcFanConflict({ selected: "mpc", enableFanInput: true, settings: dcFan(true, true) }),
    ).toBe(false);
  });

  it("does not fire when MPC is not commanding the fan", () => {
    expect(
      mpcFanConflict({ selected: "mpc", enableFanInput: false, settings: dcFan(false, false) }),
    ).toBe(false);
  });

  it("does not fire for another controller", () => {
    expect(
      mpcFanConflict({ selected: "pid", enableFanInput: true, settings: dcFan(false, true) }),
    ).toBe(false);
  });

  it("does not fire on an AC-fan build", () => {
    const ac = { ...dcFan(false, true), platform: { dc_fan: false } } as unknown as SettingsSchema;
    expect(mpcFanConflict({ selected: "mpc", enableFanInput: true, settings: ac })).toBe(false);
  });
});

describe("mpcFanPending", () => {
  it("reads saved settings when there is no draft", () => {
    expect(mpcFanPending(dcFan(true, true), {})).toBe(true);
    expect(mpcFanPending(dcFan(true, false), {})).toBe(false);
  });

  it("prefers an unsaved controller draft over saved settings", () => {
    const drafts = {
      controller: {
        value: { selected: "mpc", values: { enable_fan_input: true } },
        saved: false,
      },
    };
    expect(mpcFanPending(dcFan(true, false), drafts)).toBe(true);
  });

  it("honours a draft that turns fan control off", () => {
    const drafts = {
      controller: {
        value: { selected: "mpc", values: { enable_fan_input: false } },
        saved: false,
      },
    };
    expect(mpcFanPending(dcFan(true, true), drafts)).toBe(false);
  });

  it("is false when the draft selects another controller", () => {
    const drafts = {
      controller: { value: { selected: "pid", values: {} }, saved: false },
    };
    expect(mpcFanPending(dcFan(true, true), drafts)).toBe(false);
  });
});
