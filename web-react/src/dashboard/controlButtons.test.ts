import { describe, it, expect } from "vitest";
import { buttonsForMode } from "./controlButtons";
import { FIXTURE_DASH } from "../fixture";
import type { DashData } from "../types";

const at = (mode: string, over: Partial<DashData> = {}): DashData => ({ ...FIXTURE_DASH, currentMode: mode, ...over });
const labels = (d: DashData) => buttonsForMode(d).map((b) => b.label);

describe("buttonsForMode", () => {
  it("stopped → Startup / Prime / Monitor", () => {
    expect(labels(at("Stop"))).toEqual(["Startup", "Prime", "Monitor"]);
    expect(labels(at(""))).toEqual(["Startup", "Prime", "Monitor"]);
  });
  it("monitor → Startup / Stop", () => {
    expect(labels(at("Monitor"))).toEqual(["Startup", "Stop"]);
  });
  it("cooking → Smoke / Hold / Smoke+ / Shutdown / Stop", () => {
    expect(labels(at("Hold"))).toEqual(["Smoke", "Hold", "Smoke+", "Shutdown", "Stop"]);
  });
  it("Hold button opens the setpoint modal", () => {
    const hold = buttonsForMode(at("Smoke")).find((b) => b.label === "Hold")!;
    expect(hold.action.type).toBe("setpoint");
  });
  it("Stop and Shutdown are confirm actions; Smoke is a direct command", () => {
    const cooking = buttonsForMode(at("Hold"));
    expect(cooking.find((b) => b.label === "Stop")!.action.type).toBe("confirm");
    expect(cooking.find((b) => b.label === "Shutdown")!.action.type).toBe("confirm");
    expect(cooking.find((b) => b.label === "Smoke")!.action.type).toBe("command");
  });
  it("Smoke+ label reflects current state", () => {
    expect(labels(at("Hold", { smokePlus: false }))).toContain("Smoke+");
    const on = buttonsForMode(at("Hold", { smokePlus: true })).find((b) => b.label === "Smoke+")!;
    expect(on.variant).toBe("accent");
  });
});
