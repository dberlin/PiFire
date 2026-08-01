import { afterEach, describe, expect, rs, test } from "@rstest/core";

afterEach(() => {
  rs.resetAllMocks();
});

// Shaped like the real GET /api/history/chart response (verified against a
// running backend): `time_labels` are epoch MILLISECONDS, `annotations` is a
// DICT keyed `event_<n>`, and `graph_labels` is present alongside
// `probe_mapper`.
const PAYLOAD = {
  time_labels: [1784942370612, 1784942373612],
  chart_data: [
    {
      label: "Grill",
      borderColor: "rgb(0, 64, 255, 1)",
      hidden: false,
      data: [
        { x: 1784942370612, y: 100 },
        { x: 1784942373612, y: 110 },
      ],
    },
  ],
  probe_mapper: { probes: { grill1: 0 }, targets: {}, primarysp: {} },
  graph_labels: { probes: { grill1: "Grill" }, targets: {}, primarysp: {} },
  annotations: {},
  minutes: 30,
};

function mockFetch(response: unknown): void {
  globalThis.fetch = rs.fn().mockResolvedValue(response) as never;
}

function fetchCalls(): unknown[][] {
  return (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls;
}

describe("historyApi", () => {
  test("fetchHistoryChart requests the window and returns parsed JSON", async () => {
    mockFetch({ ok: true, json: async () => PAYLOAD });
    const { fetchHistoryChart } = await import("../../../../src/helpers/history/historyApi");

    const data = await fetchHistoryChart("", 30);

    expect(fetchCalls()[0][0]).toContain("/api/history/chart?minutes=30");
    expect(data.minutes).toBe(30);
    expect(data.time_labels).toEqual([1784942370612, 1784942373612]);
    expect(data.chart_data[0].data[0]).toEqual({ x: 1784942370612, y: 100 });
  });

  test("fetchHistoryChart omits the query when no window is given", async () => {
    mockFetch({ ok: true, json: async () => ({ ...PAYLOAD, minutes: 60 }) });
    const { fetchHistoryChart } = await import("../../../../src/helpers/history/historyApi");

    await fetchHistoryChart("");

    expect(fetchCalls()[0][0]).not.toContain("minutes=");
  });

  test("fetchHistoryChart prefixes the base URL", async () => {
    mockFetch({ ok: true, json: async () => PAYLOAD });
    const { fetchHistoryChart } = await import("../../../../src/helpers/history/historyApi");

    await fetchHistoryChart("http://pifire.local:5000", 5);

    expect(fetchCalls()[0][0]).toBe("http://pifire.local:5000/api/history/chart?minutes=5");
  });

  test("fetchHistoryChart clamps a sub-1 window the endpoint would reject", async () => {
    // The number input can transiently read 0 (or a fraction) while being
    // edited; /api/history/chart 400s on minutes < 1.
    mockFetch({ ok: true, json: async () => PAYLOAD });
    const { fetchHistoryChart } = await import("../../../../src/helpers/history/historyApi");

    await fetchHistoryChart("", 0);
    await fetchHistoryChart("", 0.4);

    expect(fetchCalls()[0][0]).toContain("minutes=1");
    expect(fetchCalls()[1][0]).toContain("minutes=1");
  });

  test("fetchHistoryChart throws on a non-ok response", async () => {
    mockFetch({ ok: false, status: 400 });
    const { fetchHistoryChart } = await import("../../../../src/helpers/history/historyApi");

    await expect(fetchHistoryChart("", 5)).rejects.toThrow("HTTP 400");
  });
});
