import { describe, expect, it } from "@rstest/core";

import { errorFor, unmatchedErrors } from "../../../../src/helpers/settings/fieldErrors";

const ERRORS = [
  { path: "startup.duration", message: "Input should be a valid integer" },
  { path: "pwm.frequency", message: "Input should be greater than 0" },
];

describe("errorFor", () => {
  it("finds the message for a path", () => {
    expect(errorFor(ERRORS, "startup.duration")).toBe("Input should be a valid integer");
  });

  it("returns null for a path with no error", () => {
    expect(errorFor(ERRORS, "startup.pwm_duty_cycle")).toBeNull();
  });

  it("returns null when there are no errors at all", () => {
    expect(errorFor([], "startup.duration")).toBeNull();
  });
});

describe("unmatchedErrors", () => {
  it("returns the errors no widget on this tab claims", () => {
    // A cross-section rule can reject a path the current tab does not render.
    // Dropping it silently would leave a failed save with nothing on screen.
    expect(unmatchedErrors(ERRORS, ["startup.duration"])).toEqual([
      { path: "pwm.frequency", message: "Input should be greater than 0" },
    ]);
  });

  it("returns nothing when every error is claimed", () => {
    expect(unmatchedErrors(ERRORS, ["startup.duration", "pwm.frequency"])).toEqual([]);
  });

  it("returns everything when the tab claims nothing", () => {
    expect(unmatchedErrors(ERRORS, [])).toEqual(ERRORS);
  });
});
