import { describe, expect, it } from "vitest";
import { fmtDuration } from "./deriveView";

describe("fmtDuration", () => {
  it("formats zero as mm:ss", () => {
    expect(fmtDuration(0)).toBe("00:00");
  });
  it("formats sub-hour durations as mm:ss", () => {
    expect(fmtDuration(65)).toBe("01:05");
  });
  it("formats hour-plus durations as h:mm:ss", () => {
    expect(fmtDuration(3661)).toBe("1:01:01");
  });
  it("clamps negative durations to 00:00", () => {
    expect(fmtDuration(-5)).toBe("00:00");
  });
});
