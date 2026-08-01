import { describe, expect, it } from "@rstest/core";
import { batteryBadge, connectionBadge } from "../../../../src/helpers/dashboard/probeStatus";
import type { ProbeStatus } from "../../../../src/helpers/types";

describe("connectionBadge", () => {
  it("is null when the probe has no `connected` key at all", () => {
    // socket_io.py:858-859 copies the key only if the driver set it, and only
    // the Bluetooth drivers do. A wired ADC probe must show no pill.
    expect(connectionBadge({})).toBeNull();
    expect(connectionBadge({ batteryPercentage: 50 })).toBeNull();
    expect(connectionBadge(undefined)).toBeNull();
  });

  it("distinguishes connected from disconnected", () => {
    expect(connectionBadge({ connected: true })).toEqual({ label: "Connected", tone: "ok" });
    expect(connectionBadge({ connected: false })).toEqual({ label: "Disconnected", tone: "off" });
  });
});

describe("batteryBadge", () => {
  const withPct = (batteryPercentage: number | null): ProbeStatus => ({ batteryPercentage });

  it("is null when the probe has no `batteryPercentage` key at all", () => {
    expect(batteryBadge({})).toBeNull();
    expect(batteryBadge({ connected: true })).toBeNull();
    expect(batteryBadge(undefined)).toBeNull();
  });

  it("reports Unknown when the key is present but null", () => {
    expect(batteryBadge(withPct(null))).toEqual({ text: "Unknown", tone: "unknown", level: 0 });
  });

  it("uses Flask's thresholds", () => {
    expect(batteryBadge(withPct(5))).toMatchObject({ tone: "danger", level: 0 });
    expect(batteryBadge(withPct(9))).toMatchObject({ tone: "danger", level: 0 });
    expect(batteryBadge(withPct(10))).toMatchObject({ tone: "warn", level: 1 });
    expect(batteryBadge(withPct(39))).toMatchObject({ tone: "warn", level: 1 });
    expect(batteryBadge(withPct(40))).toMatchObject({ tone: "ok", level: 2 });
    expect(batteryBadge(withPct(89))).toMatchObject({ tone: "ok", level: 2 });
    expect(batteryBadge(withPct(90))).toMatchObject({ tone: "ok", level: 3 });
    expect(batteryBadge(withPct(100))).toMatchObject({ tone: "ok", level: 3 });
  });

  it("rounds and clamps to 0-100", () => {
    expect(batteryBadge(withPct(64.6))?.text).toBe("65%");
    expect(batteryBadge(withPct(-20))?.text).toBe("0%");
    expect(batteryBadge(withPct(140))?.text).toBe("100%");
  });

  // Flask writes `battery_percentage || null` (dash_default.js:443,453), which
  // turns a genuine flat battery into "Unknown". That is a bug; we do not copy
  // it. This test is what stops a later parity pass reintroducing it.
  it("renders a real 0% as 0% in the danger tone, NOT as Unknown", () => {
    expect(batteryBadge(withPct(0))).toEqual({ text: "0%", tone: "danger", level: 0 });
  });
});
