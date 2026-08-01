import { describe, expect, it } from "@rstest/core";
import { cookElapsed, fmtElapsed } from "../../../../src/helpers/dashboard/cookTime";

describe("cookElapsed", () => {
  it("is null when no cook has started", () => {
    // Flask's inactive branch keys off `!= 0` (dash_default.js:400,410);
    // controller.py:405,428 zeroes the timestamp when a cook ends.
    expect(cookElapsed(0, 1700000000)).toBeNull();
  });

  it("is the difference in whole seconds", () => {
    expect(cookElapsed(1700000000, 1700003600)).toBe(3600);
    expect(cookElapsed(1700000000, 1700000007)).toBe(7);
  });

  it("truncates a fractional server timestamp rather than carrying it", () => {
    // startup_timestamp is a float time.time() server-side
    // (controller/runtime/modes/startup.py:120); socket_io.py:234 math.truncs
    // it onto the wire, but a caller reading control directly would not.
    expect(cookElapsed(1700000000.9, 1700000010)).toBe(10);
  });

  it("clamps a negative elapsed to zero", () => {
    // startup_timestamp is the PI's clock; nowSeconds is the BROWSER's. A
    // browser running behind the Pi otherwise reports a negative cook.
    expect(cookElapsed(1700000100, 1700000000)).toBe(0);
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
