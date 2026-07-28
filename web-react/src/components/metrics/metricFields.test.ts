import { describe, expect, it } from "@rstest/core";
import type { MetricRecord } from "../../helpers/metrics/metricsTypes";
import { metricRows, modeAccent } from "./metricFields";

//  A 90-second span, so timeinmode takes the minute form. Exactly 60 000 ms
//  would read "60 s" -- process_metrics' branch is `if seconds > 60`.
const BASE: MetricRecord = {
  id: "db1e2c1d-8aa5-11f1-b03c-844709826791",
  starttime: 1_700_000_000_000,
  starttime_c: "17:13:20",
  endtime: 1_700_000_090_000,
  endtime_c: "17:14:50",
  timeinmode: "1 m 30 s",
  mode: "Smoke",
  augerontime: 100,
  augerontime_c: "100 s",
  estusage_m: "30 grams",
  estusage_i: "0.07 pounds (1.06 ounces)",
  fanontime: 60,
  fanontime_c: "0",
  smokeplus: true,
  primary_setpoint: 225,
  smart_start_profile: 2,
  startup_temp: 160,
  p_mode: 2,
  auger_cycle_time: 20,
  pellet_level_start: 90,
  pellet_level_end: 85,
  pellet_brand_type: "Lumber Jack Hickory",
};

const labels = (record: MetricRecord, units = "F") =>
  metricRows(record, units).map((row) => row.label);

describe("metricRows", () => {
  it("gives Stop mode only its stop time", () => {
    expect(labels({ ...BASE, mode: "Stop" })).toEqual(["Stop Time"]);
  });

  it("treats Error like Stop, as the Jinja dispatch does", () => {
    expect(labels({ ...BASE, mode: "Error" })).toEqual(["Stop Time"]);
  });

  it("gives Shutdown, Monitor and Manual the three timing rows", () => {
    for (const mode of ["Shutdown", "Monitor", "Manual"]) {
      expect(labels({ ...BASE, mode })).toEqual(["Start Time", "End Time", "Time in Mode"]);
    }
  });

  it("gives Smoke the timing, usage, smoke-plus and startup rows", () => {
    expect(labels({ ...BASE, mode: "Smoke" })).toEqual([
      "Start Time",
      "End Time",
      "Time in Mode",
      "Auger On Time",
      "Estimated Pellet Usage",
      "Smoke Plus",
      "Smart Start Profile",
      "Smart Startup Temp",
      "P Mode",
      "Auger Cycle Time",
    ]);
  });

  it("gives Hold the setpoint instead of the startup rows", () => {
    expect(labels({ ...BASE, mode: "Hold" })).toEqual([
      "Start Time",
      "End Time",
      "Time in Mode",
      "Auger On Time",
      "Estimated Pellet Usage",
      "Smoke Plus",
      "Grill Set Temp",
    ]);
  });

  it("reads the Hold setpoint from primary_setpoint", () => {
    //  _macro_metrics.html reads metric['grill_settemp'], which is not a
    //  column in the metrics table -- Jinja renders the missing key as empty,
    //  so Flask's Hold card has always shown a blank setpoint.
    const rows = metricRows({ ...BASE, mode: "Hold", primary_setpoint: 225 }, "F");
    const row = rows.find((r) => r.label === "Grill Set Temp");
    expect(row).toEqual({ label: "Grill Set Temp", value: "225", converted: "225 F" });
  });

  it("falls back to the Startup row set for an unknown mode", () => {
    //  The Jinja dispatch's final `{% else %}` renders render_reignite, whose
    //  row set is identical to render_startup's.
    expect(labels({ ...BASE, mode: "Prime" })).toEqual(labels({ ...BASE, mode: "Startup" }));
  });

  it("shows an em dash for a mode that has not ended", () => {
    //  What the server really sends for a running mode: endtime 0, endtime_c
    //  the NUMBER 0, and timeinmode the string "Active".
    const running = { ...BASE, mode: "Smoke", endtime: 0, endtime_c: 0, timeinmode: "Active" };
    const rows = metricRows(running, "F");
    expect(rows.find((r) => r.label === "End Time")).toEqual({
      label: "End Time",
      value: "0",
      converted: "—",
    });
    expect(rows.find((r) => r.label === "Time in Mode")).toEqual({
      label: "Time in Mode",
      value: "—",
      converted: "Active",
    });
  });

  it("reports the elapsed milliseconds as the raw Time in Mode", () => {
    const rows = metricRows(BASE, "F");
    expect(rows.find((r) => r.label === "Time in Mode")?.value).toBe("90000");
  });

  it("renders Smoke Plus as Active or Disabled", () => {
    const on = metricRows({ ...BASE, mode: "Smoke", smokeplus: true }, "F");
    const off = metricRows({ ...BASE, mode: "Smoke", smokeplus: false }, "F");
    expect(on.find((r) => r.label === "Smoke Plus")?.converted).toBe("Active");
    expect(off.find((r) => r.label === "Smoke Plus")?.converted).toBe("Disabled");
  });

  it("suffixes the startup temperature with the grill's units", () => {
    const rows = metricRows({ ...BASE, mode: "Startup", startup_temp: 160 }, "C");
    expect(rows.find((r) => r.label === "Smart Startup Temp")?.converted).toBe("160 C");
  });
});

describe("modeAccent", () => {
  it("greens the modes that start a cook", () => {
    expect(modeAccent("Startup")).toBe("start");
  });

  it("reds the modes that end one", () => {
    expect(modeAccent("Stop")).toBe("stop");
    expect(modeAccent("Error")).toBe("stop");
  });

  it("warns on Reignite", () => {
    expect(modeAccent("Reignite")).toBe("warn");
  });

  it("leaves everything else neutral", () => {
    expect(modeAccent("Smoke")).toBe("neutral");
    expect(modeAccent("Prime")).toBe("neutral");
  });
});
