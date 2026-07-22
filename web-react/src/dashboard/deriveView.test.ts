import { describe, it, expect } from "vitest";
import { deriveView, fmtDuration } from "./deriveView";
import { FIXTURE_DASH } from "../fixture";
import type { DashData } from "../types";

const make = (over: Partial<DashData>): DashData => ({ ...FIXTURE_DASH, ...over });

describe("deriveView — mode / cooking", () => {
  it("treats Hold as cooking with an uppercased label", () => {
    const v = deriveView(make({ currentMode: "Hold" }));
    expect(v.cooking).toBe(true);
    expect(v.modeLabel).toBe("HOLD");
    expect(v.liveColor).toBe("#5ec96f");
  });

  it("treats Stop (and empty mode) as not cooking", () => {
    expect(deriveView(make({ currentMode: "Stop" })).cooking).toBe(false);
    const empty = deriveView(make({ currentMode: "" }));
    expect(empty.cooking).toBe(false);
    expect(empty.modeLabel).toBe("STOP");
  });

  it("treats Monitor and Shutdown as not cooking", () => {
    expect(deriveView(make({ currentMode: "Monitor" })).cooking).toBe(false);
    expect(deriveView(make({ currentMode: "Shutdown" })).cooking).toBe(false);
  });
});

describe("deriveView — gauge", () => {
  it("clamps the gauge fraction to [0,1] and reads the setpoint", () => {
    const v = deriveView(make({ primaryProbe: { ...FIXTURE_DASH.primaryProbe, temp: 300, setTemp: 225, maxTemp: 600 } }));
    expect(v.gaugeFrac).toBeCloseTo(0.5, 5);
    expect(v.hasSetpoint).toBe(true);
    expect(v.setpointInt).toBe(225);
  });

  it("marks no-setpoint when setTemp is 0 and never exceeds full", () => {
    const v = deriveView(make({ primaryProbe: { ...FIXTURE_DASH.primaryProbe, temp: 9999, setTemp: 0, maxTemp: 600 } }));
    expect(v.hasSetpoint).toBe(false);
    expect(v.gaugeFrac).toBe(1);
  });
});

describe("deriveView — probes", () => {
  it("formats a targeted probe with a bar and green when within 1° of target", () => {
    const v = deriveView(
      make({ foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], temp: 202.5, target: 203, targetReq: true }] }),
    );
    expect(v.hasProbes).toBe(true);
    const p = v.probes[0];
    expect(p.targetStr).toBe("→ 203°");
    expect(p.tgtColor).toBe("#5ec96f"); // done (>= target - 1)
    expect(p.barColor).toBe("#5ec96f");
    expect(p.barPct).toBeGreaterThan(90);
  });

  it("renders an untargeted probe as AMBIENT with an empty bar", () => {
    const v = deriveView(make({ foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], target: 0, targetReq: false }] }));
    const p = v.probes[0];
    expect(p.targetStr).toBe("AMBIENT");
    expect(p.barPct).toBe(0);
  });

  it("reports no probes when the list is empty", () => {
    expect(deriveView(make({ foodProbes: [] })).hasProbes).toBe(false);
  });
});

describe("deriveView — outputs", () => {
  it("maps fan/auger/igniter on-states to status + accent color", () => {
    const v = deriveView(make({ outputs: { fan: true, auger: true, igniter: true } }));
    expect(v.fan).toMatchObject({ on: true, status: "RUNNING", color: "var(--accent)" });
    expect(v.auger).toMatchObject({ on: true, status: "FEEDING" });
    expect(v.igniter).toMatchObject({ on: true, status: "HOT", dot: "#ff7a1a" });
  });

  it("maps off-states to IDLE/OFF with the idle color", () => {
    const v = deriveView(make({ outputs: { fan: false, auger: false, igniter: false } }));
    expect(v.fan.status).toBe("IDLE");
    expect(v.igniter.status).toBe("IDLE");
    expect(v.fan.color).toBe("#57514a");
  });
});

describe("deriveView — hopper thresholds", () => {
  it("is green/OK above 35%", () => {
    const v = deriveView(make({ hopperLevel: 68 }));
    expect(v.hopper.color).toBe("#5ec96f");
    expect(v.hopper.label).toBe("LEVEL OK");
  });
  it("is amber/RUNNING LOW below 35%", () => {
    const v = deriveView(make({ hopperLevel: 20 }));
    expect(v.hopper.color).toBe("#ffb020");
    expect(v.hopper.label).toBe("RUNNING LOW");
  });
  it("is red/REFILL below 15%", () => {
    const v = deriveView(make({ hopperLevel: 8 }));
    expect(v.hopper.color).toBe("#ff5a4d");
    expect(v.hopper.label).toBe("REFILL PELLETS");
    expect(v.hopper.pct).toBe(8);
  });
});

describe("deriveView — pills", () => {
  it("shows P-mode and an ON smoke+ pill when smokePlus is set", () => {
    const v = deriveView(make({ pMode: 2, smokePlus: true }));
    expect(v.pillL).toMatchObject({ label: "P-MODE", value: "P-2" });
    expect(v.pillR).toMatchObject({ label: "SMOKE+", value: "ON" });
  });
  it("shows an OFF smoke+ pill when smokePlus is unset", () => {
    expect(deriveView(make({ smokePlus: false })).pillR.value).toBe("OFF");
  });
});

describe("fmtDuration", () => {
  it("formats mm:ss under an hour", () => {
    expect(fmtDuration(0)).toBe("00:00");
    expect(fmtDuration(65)).toBe("01:05");
  });
  it("formats h:mm:ss at/over an hour and floors negatives to zero", () => {
    expect(fmtDuration(3661)).toBe("1:01:01");
    expect(fmtDuration(-5)).toBe("00:00");
  });
});
