import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import type { HistoryChartData } from "../../helpers/history/historyApi";

// Counts chart INSTANCES (mounts), not renders -- see the stub below.
let chartInstances = 0;

const fetchHistoryChartMock = rs.fn();
rs.mock("../../helpers/history/historyApi", () => ({
  fetchHistoryChart: (...args: unknown[]) => fetchHistoryChartMock(...args),
}));

// jsdom has no canvas, so the real uPlot chart is stubbed. The stub records
// (a) the props it was handed, so the ms -> s conversion is observable at the
// page seam, and (b) a per-INSTANCE id, so a test can tell a remount (which
// resets uPlot's scales) from a mere re-render.
rs.mock("./HistoryChart", () => ({
  HistoryChart: ({ times, series }: { times: number[]; series: { label: string }[] }) => {
    const instance = useRef<number | null>(null);
    if (instance.current === null) {
      chartInstances += 1;
      instance.current = chartInstances;
    }
    return (
      <div
        data-testid="chart"
        data-instance={String(instance.current)}
        data-times={times.join(",")}
        data-labels={series.map((s) => s.label).join(",")}
      />
    );
  },
}));

const { HistoryPage } = await import("./HistoryPage");

const PAYLOAD: HistoryChartData = {
  time_labels: [1784942370612, 1784942373612],
  chart_data: [
    {
      label: "Grill",
      borderColor: "rgb(0, 64, 255, 1)",
      hidden: false,
      data: [
        { x: 1784942370612, y: 220 },
        { x: 1784942373612, y: 225 },
      ],
    },
    {
      label: "Probe 1",
      borderColor: "#888",
      hidden: true,
      data: [
        { x: 1784942370612, y: 90 },
        { x: 1784942373612, y: 95 },
      ],
    },
  ],
  probe_mapper: { probes: { grill1: 0, probe1: 1 }, targets: {}, primarysp: {} },
  graph_labels: { probes: { grill1: "Grill", probe1: "Probe 1" }, targets: {}, primarysp: {} },
  annotations: {},
  minutes: 60,
};

// An empty history store: no timestamps, and every dataset's points list is
// empty. The page must say so rather than mounting a chart over nothing.
const EMPTY_PAYLOAD: HistoryChartData = {
  ...PAYLOAD,
  time_labels: [],
  chart_data: [{ label: "Grill", borderColor: "rgb(0, 64, 255, 1)", hidden: false, data: [] }],
};

function chartEl(): HTMLElement {
  return screen.getByTestId("chart");
}

async function renderLoaded() {
  const view = render(<HistoryPage />);
  await waitFor(() => expect(chartEl()).toBeInTheDocument());
  return view;
}

beforeEach(() => {
  chartInstances = 0;
  fetchHistoryChartMock.mockReset();
  fetchHistoryChartMock.mockResolvedValue(PAYLOAD);
});

afterEach(cleanup);

describe("HistoryPage", () => {
  it("loads and renders the chart", async () => {
    await renderLoaded();

    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);
    expect(chartEl().getAttribute("data-labels")).toBe("Grill");
  });

  it("asks for the user's saved window on first load by sending no minutes", async () => {
    await renderLoaded();

    expect(fetchHistoryChartMock.mock.calls[0][1]).toBeUndefined();
  });

  it("shows the window the server reports once it has loaded", async () => {
    fetchHistoryChartMock.mockResolvedValue({ ...PAYLOAD, minutes: 240 });
    await renderLoaded();

    expect(screen.getByLabelText(/minutes/i)).toHaveValue(240);
  });

  it("hands the chart epoch SECONDS, not the API's milliseconds", async () => {
    // Pins the /1000 conversion end to end: HistoryChart's tooltip does
    // `new Date(x * 1000)`, so leaking milliseconds through here dates every
    // sample ~50,000 years into the future.
    await renderLoaded();

    expect(chartEl().getAttribute("data-times")).toBe("1784942370.612,1784942373.612");
  });

  it("shows a loading state before the first response", () => {
    render(<HistoryPage />);

    expect(screen.getByText(/loading history/i)).toBeInTheDocument();
    expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
  });

  it("refetches when the window changes", async () => {
    await renderLoaded();
    fetchHistoryChartMock.mockClear();

    fireEvent.change(screen.getByLabelText(/minutes/i), { target: { value: "120" } });

    await waitFor(() => expect(fetchHistoryChartMock).toHaveBeenCalledWith(expect.anything(), 120));
  });

  it("remounts the chart on a window change so the x-scale is reset", async () => {
    // shouldResetScales compares the current scale against the data the plot
    // already holds, so it cannot distinguish a data tick from a window
    // change: a zoom dragged before the window changed would otherwise
    // survive and leave the new window's data off-screen. The page forces the
    // reset by giving the chart a key derived from the window.
    await renderLoaded();
    const before = chartEl().getAttribute("data-instance");

    fireEvent.change(screen.getByLabelText(/minutes/i), { target: { value: "120" } });
    await waitFor(() => expect(chartEl().getAttribute("data-instance")).not.toBe(before));
  });

  it("remounts the chart when Reset zoom is clicked", async () => {
    await renderLoaded();
    const before = chartEl().getAttribute("data-instance");

    fireEvent.click(screen.getByRole("button", { name: /reset zoom/i }));

    expect(chartEl().getAttribute("data-instance")).not.toBe(before);
  });

  it("keeps the previous chart on screen while the next window loads", async () => {
    // Stale-while-revalidating: the last good payload survives the refetch, so
    // changing the window doesn't blank the page. It also means the remount
    // above is caused by the window key alone -- not by the chart being
    // unmounted while a request is in flight.
    await renderLoaded();
    fetchHistoryChartMock.mockReturnValue(new Promise(() => {}));

    fireEvent.change(screen.getByLabelText(/minutes/i), { target: { value: "120" } });

    await waitFor(() => expect(screen.getByText(/loading history/i)).toBeInTheDocument());
    expect(chartEl()).toBeInTheDocument();
  });

  it("renders an empty state, and no chart, when there is no history", async () => {
    fetchHistoryChartMock.mockResolvedValue(EMPTY_PAYLOAD);

    render(<HistoryPage />);

    await waitFor(() => expect(screen.getByText(/no history yet/i)).toBeInTheDocument());
    expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
  });

  it("shows an error banner when the fetch fails", async () => {
    fetchHistoryChartMock.mockRejectedValue(new Error("boom"));

    render(<HistoryPage />);

    await waitFor(() => expect(screen.getByText(/couldn't load/i)).toBeInTheDocument());
    expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
  });

  it("recovers from an error when a new window loads", async () => {
    fetchHistoryChartMock.mockRejectedValueOnce(new Error("boom"));
    render(<HistoryPage />);
    await waitFor(() => expect(screen.getByText(/couldn't load/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/minutes/i), { target: { value: "15" } });

    await waitFor(() => expect(chartEl()).toBeInTheDocument());
    expect(screen.queryByText(/couldn't load/i)).not.toBeInTheDocument();
  });

  // Deferred requests, so the test controls exactly when each settles instead
  // of relying on mock resolution order.
  type Deferred = { promise: Promise<HistoryChartData>; resolve: (v: HistoryChartData) => void };
  function deferred(): Deferred {
    let resolve!: (v: HistoryChartData) => void;
    const promise = new Promise<HistoryChartData>((res) => {
      resolve = res;
    });
    return { promise, resolve };
  }

  it("does not read a stale same-window outcome as settled after 120 -> 60 -> 120", async () => {
    // Pins the exact collision the forMinutes-only tag used to miss: request
    // A (minutes=120) settles and is shown. The window flips to 60 (request
    // B, never resolved here) and back to 120 (request C) *before* B settles.
    // C is a brand new request -- distinct from A -- but it asks for the same
    // window A already answered. A window-value-only tag can't tell "the
    // outcome on file is for this window and is current" from "the outcome on
    // file happens to carry this window's number, but a newer request for it
    // is still in flight": both compare equal. The page must show loading
    // (not A's older snapshot) until C -- not A -- resolves, and then render
    // C's payload.
    const reqs: { minutes: number | undefined; d: Deferred }[] = [];
    fetchHistoryChartMock.mockReset();
    fetchHistoryChartMock.mockImplementation((_base: string, minutes: number | undefined) => {
      const d = deferred();
      reqs.push({ minutes, d });
      return d.promise;
    });

    render(<HistoryPage />);
    await waitFor(() => expect(reqs.length).toBe(1));
    reqs[0].d.resolve({ ...PAYLOAD, minutes: 60 }); // initial load, unrelated window
    await waitFor(() => expect(screen.getByTestId("chart")).toBeInTheDocument());

    const input = screen.getByLabelText(/minutes/i);

    fireEvent.change(input, { target: { value: "120" } }); // request A
    await waitFor(() => expect(reqs.length).toBe(2));
    reqs[1].d.resolve({ ...PAYLOAD, minutes: 120, time_labels: [111] }); // A settles: an older 120 snapshot
    await waitFor(() => expect(screen.queryByText(/loading history/i)).not.toBeInTheDocument());

    fireEvent.change(input, { target: { value: "60" } }); // request B
    await waitFor(() => expect(reqs.length).toBe(3));
    // B is left pending -- flip straight back to 120 before it ever settles.
    fireEvent.change(input, { target: { value: "120" } }); // request C
    await waitFor(() => expect(reqs.length).toBe(4));

    // C is still in flight. The only Outcome on file is A's (also tagged
    // 120), which a window-value-only comparison would read as "settled".
    expect(screen.queryByText(/loading history/i)).toBeInTheDocument();
    expect(screen.getByTestId("chart").getAttribute("data-times")).toBe("0.111"); // still A's payload

    reqs[3].d.resolve({ ...PAYLOAD, minutes: 120, time_labels: [222] }); // C settles: the current snapshot
    await waitFor(() => expect(screen.queryByText(/loading history/i)).not.toBeInTheDocument());
    expect(screen.getByTestId("chart").getAttribute("data-times")).toBe("0.222");
  });

  it("links Export CSV at the legacy CSV route", async () => {
    await renderLoaded();

    const link = screen.getByRole("link", { name: /export csv/i });
    expect(link.getAttribute("href")).toContain("/history/export");
  });
});
