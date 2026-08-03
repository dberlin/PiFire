import { describe, expect, it } from "@rstest/core";
import { setPath } from "../../../../src/helpers/settings/delta";

describe("setPath typing", () => {
  it("accepts a real path with a correctly typed value", () => {
    const out = setPath({}, "startup.duration", 240);
    expect(out).toEqual({ startup: { duration: 240 } });
  });

  it("accepts a deep path", () => {
    const out = setPath({}, "startup.smartstart.exit_temp", 120);
    expect(out).toEqual({ startup: { smartstart: { exit_temp: 120 } } });
  });

  it("rejects a misspelled path", () => {
    // @ts-expect-error -- exit_tmep is not a field of startup.smartstart
    const out = setPath({}, "startup.smartstart.exit_tmep", 120);
    expect(out).toBeTruthy();
  });

  it("rejects a value of the wrong type for a real path", () => {
    // @ts-expect-error -- startup.duration is a number
    const out = setPath({}, "startup.duration", true);
    expect(out).toBeTruthy();
  });
});
