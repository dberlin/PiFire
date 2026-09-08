import { describe, expect, it } from "@rstest/core";
import { FIXTURE_DASH } from "@pifire/core/fixture";

import { cookElapsed, fmtElapsed } from "../../../../src/helpers/dashboard/cookTime";

describe("cookElapsed", () => {
  it("keeps unknown cook provenance unknown, even when mode elapsed is known", () => {
    expect(cookElapsed({ ...FIXTURE_DASH.durations, modeElapsedS: 100 })).toBeNull();
  });

  it("uses cook exposure rather than elapsed in the current mode", () => {
    expect(cookElapsed({
      ...FIXTURE_DASH.durations, modeElapsedS: 7, cookElapsedS: 3723.9,
    })).toBe(3723);
  });
});

describe("fmtElapsed", () => {
  it('renders the inactive state as Flask does, "--"', () => {
    expect(fmtElapsed(null)).toBe("--");
  });

  it("renders under a minute as NNs", () => {
    expect(fmtElapsed(0)).toBe("00s");
    expect(fmtElapsed(7)).toBe("07s");
    expect(fmtElapsed(59)).toBe("59s");
  });

  it("renders under an hour as MM:SS", () => {
    expect(fmtElapsed(60)).toBe("01:00");
    expect(fmtElapsed(754)).toBe("12:34");
    expect(fmtElapsed(3599)).toBe("59:59");
  });

  it("renders an hour and over as HH:MM:SS with a zero-padded hour", () => {
    // deriveView's fmtDuration does NOT pad the hour; this one matches Flask's
    // formatDuration (dash_default.js:599-611) exactly.
    expect(fmtElapsed(3600)).toBe("01:00:00");
    expect(fmtElapsed(3723)).toBe("01:02:03");
    expect(fmtElapsed(45296)).toBe("12:34:56");
  });
});
