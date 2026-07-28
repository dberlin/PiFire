import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { render, screen, waitFor } from "@testing-library/react";
import * as actualMetricsApi from "../../helpers/metrics/metricsApi" with {
  rstest: "importActual",
};
import type { MetricsPayload, MetricsResult } from "../../helpers/metrics/metricsTypes";

// The API module is mocked, not `fetch` -- the idiom AdminPage.test.tsx
// established. Stubbed through a lazy wrapper so the hoisted mock factory never
// captures an uninitialised binding, and `metricsExportUrl` stays REAL: the
// href it builds is one of the things under test here, and a stub would let a
// wrong one pass unnoticed.
const fetchMetricsMock = rs.fn();
rs.mock("../../helpers/metrics/metricsApi", () => ({
  ...actualMetricsApi,
  fetchMetrics: (...a: unknown[]) => fetchMetricsMock(...a),
}));

const { MetricsPage } = await import("./MetricsPage");

const RECORD = {
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

const ok = (data: MetricsPayload): MetricsResult => ({
  ok: true,
  status: 200,
  message: "",
  data,
});

const payload = (metrics: unknown[], augerrate = 0.3) =>
  ({ metrics, units: "F", augerrate }) as unknown as MetricsPayload;

beforeEach(() => {
  fetchMetricsMock.mockReset();
});

describe("MetricsPage", () => {
  it("renders a card per record once the read lands", async () => {
    fetchMetricsMock.mockResolvedValue(
      ok(payload([RECORD, { ...RECORD, id: "m2", mode: "Hold" }])),
    );
    render(<MetricsPage />);

    await waitFor(() => expect(screen.getByRole("heading", { name: "Smoke Mode" })).toBeVisible());
    expect(screen.getByRole("heading", { name: "Hold Mode" })).toBeVisible();
  });

  it("offers the CSV as a download link, not a button", async () => {
    fetchMetricsMock.mockResolvedValue(ok(payload([RECORD])));
    render(<MetricsPage />);

    const link = await screen.findByRole("link", { name: "Download CSV Data" });
    expect(link).toHaveAttribute("href", "/api/metrics/export");
  });

  it("states the auger rate the estimates assume", async () => {
    fetchMetricsMock.mockResolvedValue(ok(payload([RECORD], 0.45)));
    render(<MetricsPage />);
    expect(await screen.findByText("Auger rate: 0.45 g/s")).toBeVisible();
  });

  it("shows Flask's empty state and no CSV link when nothing is recorded", async () => {
    fetchMetricsMock.mockResolvedValue(ok(payload([])));
    render(<MetricsPage />);

    expect(await screen.findByRole("heading", { name: "No Data" })).toBeVisible();
    expect(screen.getByText("Start the grill to begin populating metrics.")).toBeVisible();
    //  Flask hides the export behind the same `{% if %}`: a CSV of nothing is
    //  a file that says "No Data".
    expect(screen.queryByRole("link", { name: "Download CSV Data" })).toBeNull();
  });

  it("reports a failed read in place", async () => {
    fetchMetricsMock.mockResolvedValue({ ok: false, status: 500, message: "boom", data: null });
    render(<MetricsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("shows a loading note before the first response", () => {
    //  A promise that never settles, so this asserts the loading branch rather
    //  than racing a resolved one to the first paint.
    fetchMetricsMock.mockReturnValue(new Promise(() => {}));
    render(<MetricsPage />);
    expect(screen.getByText("Loading metrics…")).toBeVisible();
  });

  it("reads once on mount and not on a timer", async () => {
    fetchMetricsMock.mockResolvedValue(ok(payload([RECORD])));
    render(<MetricsPage />);
    await screen.findByRole("heading", { name: "Smoke Mode" });
    expect(fetchMetricsMock).toHaveBeenCalledTimes(1);
  });
});
