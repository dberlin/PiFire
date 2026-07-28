import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fetchMetrics, metricsExportUrl } from "./metricsApi";

const fetchMock = rs.fn();
rs.stubGlobal("fetch", fetchMock);

function envelope(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body };
}

//  Copied from a REAL /api/metrics response, not composed from the type and
//  not from common/defaults.py's metrics_items -- that list declares
//  starttime_c and timeinmode as ints, and process_metrics replaces both with
//  strings before the record leaves the server.
//
//  "1 m 30 s" for a 90-second span: the minute branch is `if seconds > 60`, so
//  a span of exactly 60 000 ms would report "60 s" instead.
const RECORD = {
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
  smart_start_profile: 0,
  startup_temp: 0,
  p_mode: 0,
  auger_cycle_time: 0,
  pellet_level_start: 0,
  pellet_level_end: 0,
  pellet_brand_type: "Lumber Jack Hickory",
};

const PAYLOAD = { metrics: [RECORD], units: "F", augerrate: 0.3 };

beforeEach(() => {
  fetchMock.mockReset();
});

describe("fetchMetrics", () => {
  it("unwraps the envelope", async () => {
    fetchMock.mockResolvedValue(envelope(200, { result: "OK", message: null, data: PAYLOAD }));
    const result = await fetchMetrics("");
    expect(result.ok).toBe(true);
    expect(result.data?.units).toBe("F");
    expect(result.data?.metrics[0].mode).toBe("Smoke");
  });

  it("reads the endpoint relative to the base url", async () => {
    fetchMock.mockResolvedValue(envelope(200, { result: "OK", message: null, data: PAYLOAD }));
    await fetchMetrics("http://grill.local");
    expect(fetchMock.mock.calls[0][0]).toBe("http://grill.local/api/metrics");
    //  A bare GET: no second argument, so no method, headers or body.
    expect(fetchMock.mock.calls[0][1]).toBeUndefined();
  });

  it("reports a server error instead of throwing", async () => {
    fetchMock.mockResolvedValue(envelope(500, { result: "Error", message: "boom", data: null }));
    const result = await fetchMetrics("");
    expect(result.ok).toBe(false);
    expect(result.status).toBe(500);
    expect(result.message).toBe("boom");
  });

  it("survives a body that is not JSON", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    });
    const result = await fetchMetrics("");
    expect(result.ok).toBe(false);
    expect(result.message).toBe("HTTP 502");
  });

  it("reports a dropped connection as status 0", async () => {
    fetchMock.mockRejectedValue(new Error("Failed to fetch"));
    const result = await fetchMetrics("");
    expect(result).toEqual({ ok: false, status: 0, message: "Failed to fetch", data: null });
  });

  it("refuses an OK status carrying an Error envelope", async () => {
    //  common/app.py's api_response puts the verdict in the BODY, so a 200 is
    //  not on its own a success. Pinned because the whole client branches on
    //  `ok` and a 200/Error would otherwise render as data.
    fetchMock.mockResolvedValue(envelope(200, { result: "Error", message: "nope", data: null }));
    expect((await fetchMetrics("")).ok).toBe(false);
  });
});

describe("metricsExportUrl", () => {
  it("points at the export endpoint", () => {
    expect(metricsExportUrl("")).toBe("/api/metrics/export");
    expect(metricsExportUrl("http://grill.local")).toBe("http://grill.local/api/metrics/export");
  });
});
