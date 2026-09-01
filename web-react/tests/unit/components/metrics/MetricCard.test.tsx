import type { MetricRecord } from "@pifire/core/contracts/content";
import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MetricCard } from "../../../../src/components/metrics/MetricCard";

const RECORD: MetricRecord = {
  id: "m1",
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

describe("MetricCard", () => {
  it("names the mode in its heading", () => {
    render(<MetricCard record={RECORD} units="F" />);
    expect(screen.getByRole("heading", { name: "Smoke Mode" })).toBeInTheDocument();
  });

  it("labels the table for assistive technology", () => {
    render(<MetricCard record={RECORD} units="F" />);
    expect(screen.getByRole("table", { name: "Smoke Mode metrics" })).toBeInTheDocument();
  });

  it("renders the three Flask columns", () => {
    render(<MetricCard record={RECORD} units="F" />);
    expect(screen.getByRole("columnheader", { name: "Metric" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Value" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Converted" })).toBeInTheDocument();
  });

  it("renders every row the mode calls for", () => {
    render(<MetricCard record={RECORD} units="F" />);
    expect(screen.getByRole("rowheader", { name: "Estimated Pellet Usage" })).toBeInTheDocument();
    expect(screen.getByText("0.07 pounds (1.06 ounces)")).toBeInTheDocument();
  });

  it("carries the mode accent as a class", () => {
    const { container } = render(<MetricCard record={{ ...RECORD, mode: "Stop" }} units="F" />);
    expect(container.querySelector(".pf-metrics-card")).toHaveClass("stop");
  });

  it("keeps the raw record collapsed until asked", async () => {
    //  Asserted on the `open` ATTRIBUTE, not on visibility. jsdom has no UA
    //  stylesheet rule hiding a closed <details>' children, so
    //  `.not.toBeVisible()` would pass on a card that renders the JSON
    //  expanded -- it would be measuring nothing.
    const { container } = render(<MetricCard record={RECORD} units="F" />);
    const details = container.querySelector("details");
    expect(details).not.toHaveAttribute("open");

    await userEvent.click(screen.getByText("Raw Data"));
    expect(details).toHaveAttribute("open");
  });

  it("shows every field in the raw record, including the ones no table row names", () => {
    render(<MetricCard record={RECORD} units="F" />);
    //  fanontime, pellet_level_start/_end and pellet_brand_type are collected
    //  by control.py and named by no macro in _macro_metrics.html. The
    //  disclosure is where they are reachable, exactly as in Flask.
    const raw = screen.getByTestId("metric-raw-m1").textContent ?? "";
    expect(raw).toContain('"fanontime": 60');
    expect(raw).toContain('"pellet_level_end": 85');
  });
});
