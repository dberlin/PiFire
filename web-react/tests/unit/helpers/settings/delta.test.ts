import { describe, expect, it } from "@rstest/core";

import { setPath } from "../../../../src/helpers/settings/delta";

describe("setPath", () => {
  it("builds a nested delta from a dot path", () => {
    expect(setPath({}, "globals.grill_name", "Smokey")).toEqual({
      globals: { grill_name: "Smokey" },
    });
    expect(setPath({}, "pwm.update_time", 7)).toEqual({ pwm: { update_time: 7 } });
  });
  it("merges into an existing partial without mutating input", () => {
    const base = { pwm: { update_time: 7 } };
    const out = setPath(base, "pwm.frequency", 100);
    expect(out).toEqual({ pwm: { update_time: 7, frequency: 100 } });
    expect(base).toEqual({ pwm: { update_time: 7 } });
  });
});
