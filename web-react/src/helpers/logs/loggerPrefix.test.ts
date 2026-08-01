import { describe, expect, it } from "@rstest/core";
import { stripLoggerPrefix } from "./loggerPrefix";

describe("stripLoggerPrefix", () => {
  it("replaces the logger prefix with just the clock", () => {
    expect(stripLoggerPrefix("2026-08-01 12:00:03 +0000 | INFO | Resolved 12 packages\n")).toBe(
      "12:00:03  Resolved 12 packages\n",
    );
  });

  it("handles every level, not only INFO", () => {
    expect(stripLoggerPrefix("2026-08-01 12:00:03 +0000 | ERROR | could not build wheel")).toBe(
      "12:00:03  could not build wheel",
    );
  });

  it("formats each line independently", () => {
    const text = "2026-08-01 12:00:03 +0000 | INFO | one\n2026-08-01 12:00:04 +0000 | INFO | two\n";
    expect(stripLoggerPrefix(text)).toBe("12:00:03  one\n12:00:04  two\n");
  });

  it("passes through a line the logger did not write", () => {
    // Continuation lines of a traceback, or a command printing its own format.
    // Dropping them would lose exactly the detail worth opening the panel for.
    const text =
      "2026-08-01 12:00:03 +0000 | INFO | Traceback (most recent call last):\n  File x, line 1\n";
    expect(stripLoggerPrefix(text)).toBe(
      "12:00:03  Traceback (most recent call last):\n  File x, line 1\n",
    );
  });

  it("leaves a bare pipe in the message alone", () => {
    // Only the prefix is matched, so shell output that contains pipes survives.
    expect(stripLoggerPrefix("2026-08-01 12:00:03 +0000 | INFO | sh -c 'a | b'")).toBe(
      "12:00:03  sh -c 'a | b'",
    );
  });

  it("returns empty text unchanged", () => {
    expect(stripLoggerPrefix("")).toBe("");
  });
});
