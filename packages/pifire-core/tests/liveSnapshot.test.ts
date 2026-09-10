import type { DashSocketPayload, ThermocoupleHealthView } from "../src/contracts/core.gen";
import { describe, expect, it } from "@rstest/core";
import { deriveView } from "../src/dashboard/deriveView";
import { demoDashAt } from "../src/demoData";
import { FIXTURE_DASH } from "../src/fixture";
import { projectLiveSnapshot } from "../src/liveConnection";

const health: ThermocoupleHealthView = {
  device: "mcp9601", port: "TC0", label: FIXTURE_DASH.primaryProbe.label,
  displayName: "Grill", role: "Primary", outcome: "notify_only",
  detector: { source: "software", policy: "observe" },
  report: { state: "confirmed", faults: ["malfunction"], evidence: [], temperatureValid: true, detail: {} },
  freshness: { current: true, lastReportedAgeS: 2, reason: "current" },
};
const payload: DashSocketPayload = {
  ...FIXTURE_DASH,
  primaryProbe: { ...FIXTURE_DASH.primaryProbe, temp: 226, status: { lastTemp: 225, lastReadingAge: 3 } },
  foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], temp: 160, status: { lastTemp: 159, lastReadingAge: 4 } }],
  thermocoupleHealth: [health],
};

describe("retained live snapshots", () => {
  it("ages retained readings and health without rewriting acquisition validity or inventing control errors", () => {
    const result = projectLiveSnapshot(payload, 100_000, 131_000, true);
    const view = deriveView(result.live);
    expect(result.controlAlive).toBe(false);
    expect(result.live.errors).toEqual([]);
    expect(result.live.primaryProbe.temp).toBe(226);
    expect(result.live.primaryProbe.status.lastTemp).toBe(225);
    expect(result.live.thermocoupleHealth?.[0]?.report).toEqual(health.report);
    expect(result.live.thermocoupleHealth?.[0]?.freshness).toEqual({
      current: false, lastReportedAgeS: 33, reason: "retained",
    });
    expect(view.tempInt).toBe(226);
    expect(view.stale).toBe("last data 34s ago");
    expect(view.probes[0].tempInt).toBe(160);
    expect(view.probes[0].stale).toBe("last data 35s ago");
    expect(payload.primaryProbe.status.lastReadingAge).toBe(3);
    expect(health.freshness.current).toBe(true);
  });

  it.each([null, 101_000, Number.NaN])("does not invent ages for an invalid receipt %s", (receipt) => {
    const result = projectLiveSnapshot(payload, receipt, 100_000, true);
    expect(result.controlAlive).toBe(false);
    expect(result.live.thermocoupleHealth?.[0]?.freshness).toEqual({
      current: false, lastReportedAgeS: null, reason: "unknown-clock",
    });
    expect(deriveView(result.live).stale).toBe("Last known");
    expect(result.live.primaryProbe.temp).toBe(226);
  });

  it("reanchors new packets while preserving a producer's stale health and unknown acquisition age", () => {
    const fresh = projectLiveSnapshot(payload, 140_000, 141_000, true);
    expect(fresh.controlAlive).toBe(true);
    expect(deriveView(fresh.live).stale).toBeNull();
    expect(fresh.live.thermocoupleHealth?.[0]?.freshness.current).toBe(true);
    const producerStale = projectLiveSnapshot({
      ...payload,
      primaryProbe: { ...payload.primaryProbe, status: { lastTemp: 226, lastReadingAge: null } },
      thermocoupleHealth: [{ ...health, freshness: { current: false, lastReportedAgeS: null, reason: "unknown-clock" } }],
    }, 140_000, 141_000, true);
    expect(producerStale.live.thermocoupleHealth?.[0]?.freshness.current).toBe(false);
    expect(deriveView(producerStale.live).tempInt).toBe(226);
    expect(deriveView(producerStale.live).stale).toBe("Last known");
  });

  it("retains numeric data without health or last-good metadata after transport loss", () => {
    const result = projectLiveSnapshot(FIXTURE_DASH, 100_000, 105_000, false);
    expect(result.controlAlive).toBe(false);
    expect(deriveView(result.live).tempInt).toBe(FIXTURE_DASH.primaryProbe.temp);
    expect(deriveView(result.live).stale).toBe("Last known");
  });

  it("keeps simulated readings fresh without requiring producer metadata", () => {
    const demo = demoDashAt(120);
    const view = deriveView(demo);
    expect(view.tempInt).toBe(demo.primaryProbe.temp);
    expect(view.stale).toBeNull();
    expect(view.probes[0].stale).toBeNull();
  });
});
