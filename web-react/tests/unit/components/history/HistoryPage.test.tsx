import type { HistoryChartData } from "@pifire/core/contracts/content";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { MemoryRouter } from "react-router";
import { testQueryClient } from "../../test-utils";

// Counts chart INSTANCES (mounts), not renders -- see the stub below.
let chartInstances = 0;

const fetchHistoryChartMock = rs.fn();
rs.mock("../../../../src/helpers/history/historyApi", () => ({
  fetchHistoryChart: (...args: unknown[]) => fetchHistoryChartMock(...args),
}));

// The page reads Settings > History > Auto Refresh once on mount. Defaulted to
// "off" so the tests that aren't about polling see no timers at all.
const getSettingsMock = rs.fn();
rs.mock("../../../../src/helpers/settings/settingsApi", () => ({
  getSettings: (...args: unknown[]) => getSettingsMock(...args),
}));

// jsdom has no canvas, so the real uPlot chart is stubbed. The stub records
// (a) the props it was handed, so the ms -> s conversion is observable from
// the page, and (b) a per-INSTANCE id, so a test can tell a remount (which
// resets uPlot's scales) from a mere re-render.
rs.mock("../../../../src/components/history/HistoryChart", () => ({
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

// The saved-cook list is its own component with its own tests and its own
// fetches; stubbed here so this module's assertions stay about the chart.
rs.mock("../../../../src/components/cookfiles/CookFileList", () => ({
  CookFileList: () => <div data-testid="cookfile-list" />,
}));

const { HistoryPage } = await import("../../../../src/components/history/HistoryPage");

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

// The page carries a <Link> to /metrics, which needs a router in context.
// MemoryRouter rather than the real one: these tests are about the chart,
// and none of them navigates.
function renderPage() {
  return render(
    <QueryClientProvider client={testQueryClient()}>
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function renderLoaded() {
  const view = renderPage();
  await waitFor(() => expect(chartEl()).toBeInTheDocument());
  return view;
}

beforeEach(() => {
  chartInstances = 0;
  fetchHistoryChartMock.mockReset();
  fetchHistoryChartMock.mockResolvedValue(PAYLOAD);
  getSettingsMock.mockReset();
  getSettingsMock.mockResolvedValue({ history_page: { autorefresh: "off" } });
});

afterEach(() => {
  cleanup();
  rs.useRealTimers();
});

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
    renderPage();

    expect(screen.getByText(/loading history/i)).toBeInTheDocument();
    expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
  });

  it("refetches when the window changes", async () => {
    await renderLoaded();
    fetchHistoryChartMock.mockClear();

    fireEvent.change(screen.getByLabelText(/minutes/i), { target: { value: "120" } });

    await waitFor(() => expect(fetchHistoryChartMock).toHaveBeenCalledWith(expect.anything(), 120));
  });

  it("refetches when the window changes, driven by the query key, not a hand-rolled counter", async () => {
    // Pins that `minutes` alone determines the request: typing a new window
    // (character by character, via userEvent) ends with exactly that window
    // as the last call, with no BASE_URL surprises from the new queryFn
    // wiring -- the exact regression a stray requestId-vs-queryKey mismatch
    // would produce.
    await renderLoaded();
    fetchHistoryChartMock.mockClear();

    await userEvent.clear(screen.getByLabelText(/minutes/i));
    await userEvent.type(screen.getByLabelText(/minutes/i), "120");

    await waitFor(() => expect(fetchHistoryChartMock).toHaveBeenLastCalledWith("", 120));
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
    //
    // placeholderData is what makes this true now: the outgoing request never
    // settles below, so the page never drops into the "Loading history…"
    // branch at all -- the previous window's chart stays up, unmounted and
    // remounted, as react-query's placeholder for the new key the whole time
    // its own fetch is outstanding. Synchronizing on "Loading history…" (as
    // this test did against the old requestId-driven effect, which showed
    // that text AND the stale chart at once) would hang forever now, so this
    // waits on the mock call instead.
    await renderLoaded();
    fetchHistoryChartMock.mockClear();
    fetchHistoryChartMock.mockReturnValue(new Promise(() => {}));

    fireEvent.change(screen.getByLabelText(/minutes/i), { target: { value: "120" } });

    await waitFor(() => expect(fetchHistoryChartMock).toHaveBeenCalledWith(expect.anything(), 120));
    expect(chartEl()).toBeInTheDocument();
    expect(screen.queryByText(/loading history/i)).not.toBeInTheDocument();
  });

  it("renders an empty state, and no chart, when there is no history", async () => {
    fetchHistoryChartMock.mockResolvedValue(EMPTY_PAYLOAD);

    renderPage();

    await waitFor(() => expect(screen.getByText(/no history yet/i)).toBeInTheDocument());
    expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
  });

  it("shows an error banner when the fetch fails", async () => {
    fetchHistoryChartMock.mockRejectedValue(new Error("boom"));

    renderPage();

    await waitFor(() => expect(screen.getByText(/couldn't load/i)).toBeInTheDocument());
    expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
  });

  it("recovers from an error when a new window loads", async () => {
    fetchHistoryChartMock.mockRejectedValueOnce(new Error("boom"));
    renderPage();
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
    // Pins the exact collision a window-value-only tag used to miss: request
    // A (minutes=120) settles and is shown. The window flips to 60 (request
    // B, never resolved here) and back to 120 (request C) *before* B settles.
    // C is a brand new request -- distinct from A -- but it asks for the same
    // window A already answered. A window-value-only tag can't tell "the
    // outcome on file is for this window and is current" from "the outcome on
    // file happens to carry this window's number, but a newer request for it
    // is still in flight": both compare equal.
    //
    // react-query sidesteps the whole class of bug by keying its cache on
    // `minutes` itself and tracking each key's own fetch generation, so C
    // resolving after A can never be mistaken for A resolving late. While C is
    // in flight the query key (120) already has cached data -- A's -- so the
    // page shows A's snapshot (not a loading state: this is exactly what
    // placeholderData/cached-data-while-refetching means) and MUST NOT swap
    // to any other payload until C -- not A -- actually resolves.
    const reqs: { minutes: number | undefined; d: Deferred }[] = [];
    fetchHistoryChartMock.mockReset();
    fetchHistoryChartMock.mockImplementation((_base: string, minutes: number | undefined) => {
      const d = deferred();
      reqs.push({ minutes, d });
      return d.promise;
    });

    renderPage();
    await waitFor(() => expect(reqs.length).toBe(1));
    reqs[0].d.resolve({ ...PAYLOAD, minutes: 60 }); // initial load, unrelated window
    await waitFor(() => expect(screen.getByTestId("chart")).toBeInTheDocument());

    const input = screen.getByLabelText(/minutes/i);

    fireEvent.change(input, { target: { value: "120" } }); // request A
    await waitFor(() => expect(reqs.length).toBe(2));
    reqs[1].d.resolve({ ...PAYLOAD, minutes: 120, time_labels: [111] }); // A settles: an older 120 snapshot
    // Wait on the payload itself, not on "no loading text": that text is
    // already absent the whole time thanks to placeholderData, so it would
    // never force this await to actually wait for A's resolution to land.
    await waitFor(() => expect(chartEl().getAttribute("data-times")).toBe("0.111"));

    fireEvent.change(input, { target: { value: "60" } }); // request B
    await waitFor(() => expect(reqs.length).toBe(3));
    // B is left pending -- flip straight back to 120 before it ever settles.
    fireEvent.change(input, { target: { value: "120" } }); // request C
    await waitFor(() => expect(reqs.length).toBe(4));

    // C is still in flight. The only cached data on file for key 120 is A's;
    // the page shows it (no loading text -- there IS current data for this
    // key) rather than either blanking or, worse, showing something neither A
    // nor C ever returned.
    expect(screen.queryByText(/loading history/i)).not.toBeInTheDocument();
    expect(screen.getByTestId("chart").getAttribute("data-times")).toBe("0.111"); // still A's payload

    reqs[3].d.resolve({ ...PAYLOAD, minutes: 120, time_labels: [222] }); // C settles: the current snapshot
    // Same reasoning as above: wait on C's payload actually landing, not on
    // an absence that was already true before C ever resolved.
    await waitFor(() => expect(chartEl().getAttribute("data-times")).toBe("0.222"));
    expect(screen.queryByText(/loading history/i)).not.toBeInTheDocument();
  });

  it("links Export CSV at the legacy CSV route", async () => {
    await renderLoaded();

    const link = screen.getByRole("link", { name: /export csv/i });
    expect(link.getAttribute("href")).toContain("/history/export");
  });
});

describe("HistoryPage — auto refresh", () => {
  // Mirrors REFRESH_MS in HistoryPage.tsx. Deliberately duplicated rather than
  // imported: exporting a non-component from a component file trips
  // react-refresh/only-export-components (the reason tooltipFormat.ts and
  // scaleReset.ts are separate modules), and one constant does not warrant a
  // module of its own. InstallProgress.test.tsx hardcodes its 250ms the same
  // way.
  const REFRESH_MS = 5000;

  // Settles the mount-time settings read and the first chart fetch. waitFor
  // can't be used here: it polls on a timer that fake timers freeze.
  async function settle() {
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
      await Promise.resolve();
    });
  }

  async function tick(ms: number) {
    await act(async () => {
      await rs.advanceTimersByTimeAsync(ms);
      await Promise.resolve();
    });
  }

  it("refetches on the interval while autorefresh is on", async () => {
    getSettingsMock.mockResolvedValue({ history_page: { autorefresh: "on" } });
    rs.useFakeTimers();

    renderPage();
    await settle();
    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);

    await tick(REFRESH_MS);
    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(2);

    // The timer is re-armed after each response settles, so polling keeps
    // going rather than firing exactly once.
    await tick(REFRESH_MS);
    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(3);
  });

  it("keeps asking for the same window a poll refreshes", async () => {
    getSettingsMock.mockResolvedValue({ history_page: { autorefresh: "on" } });
    rs.useFakeTimers();

    renderPage();
    await settle();
    fireEvent.change(screen.getByLabelText(/minutes/i), { target: { value: "120" } });
    await settle();
    fetchHistoryChartMock.mockClear();

    await tick(REFRESH_MS);

    expect(fetchHistoryChartMock).toHaveBeenCalledWith(expect.anything(), 120);
  });

  it("does not remount the chart on a refresh tick, so a drag-zoom survives", async () => {
    // A poll is NOT a window change: it must ride the ordinary data-update
    // path (same chartKey -> re-render, uPlot setData, shouldResetScales
    // preserving the zoom) rather than the remount a window change forces.
    //
    // Two ticks, not one: react-query's notifyManager flushes a query
    // observer's result through a real setTimeout(0), and under fake timers
    // a setTimeout(0) scheduled while advanceTimersByTimeAsync(REFRESH_MS)
    // is already processing its final due timer doesn't fire until the NEXT
    // call that advances the clock -- confirmed by instrumenting
    // notifyManager's scheduler directly. A single tick's assertion would
    // pass even against a broken chartKey (e.g. one that folds dataUpdatedAt
    // in) purely because the render that would expose the break hasn't
    // landed yet; a second tick forces it through.
    getSettingsMock.mockResolvedValue({ history_page: { autorefresh: "on" } });
    rs.useFakeTimers();

    renderPage();
    await settle();
    const before = chartEl().getAttribute("data-instance");

    await tick(REFRESH_MS);
    await tick(REFRESH_MS);

    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(3);
    expect(chartEl().getAttribute("data-instance")).toBe(before);
  });

  it("does not poll while autorefresh is off", async () => {
    getSettingsMock.mockResolvedValue({ history_page: { autorefresh: "off" } });
    rs.useFakeTimers();

    renderPage();
    await settle();
    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);

    await tick(REFRESH_MS * 4);

    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);
  });

  it("polls on the auto-refresh cadence when the setting is on (refetchInterval gated by the setting)", async () => {
    // Distinct from "refetches on the interval while autorefresh is on"
    // above: this one drives the tick with a bare `act(() =>
    // advanceTimersByTime(...))`, proving that gating refetchInterval on
    // `autoRefresh` (rather than, say, always polling) is what makes this
    // tick produce a second fetch.
    getSettingsMock.mockResolvedValue({ history_page: { autorefresh: "on" } });
    rs.useFakeTimers();

    renderPage();
    await settle();
    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);

    await act(() => rs.advanceTimersByTime(REFRESH_MS));
    await settle();
    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(2);
  });

  it("does not poll when the setting is off (refetchInterval must be false, not merely unset)", async () => {
    getSettingsMock.mockResolvedValue({ history_page: { autorefresh: "off" } });
    rs.useFakeTimers();

    renderPage();
    await settle();
    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);

    await act(() => rs.advanceTimersByTime(REFRESH_MS * 3));
    await settle();
    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);
  });

  it("does not poll when the settings read fails", async () => {
    getSettingsMock.mockRejectedValue(new Error("boom"));
    rs.useFakeTimers();

    renderPage();
    await settle();

    await tick(REFRESH_MS * 4);

    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);
  });

  it("clears the interval on unmount", async () => {
    getSettingsMock.mockResolvedValue({ history_page: { autorefresh: "on" } });
    rs.useFakeTimers();

    const view = renderPage();
    await settle();
    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);

    view.unmount();
    await tick(REFRESH_MS * 4);

    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);
  });

  it("does not stack a poll on top of a request that is still in flight", async () => {
    // The in-flight guard is react-query's own request dedup, not something
    // this page hand-rolls anymore (see HistoryPage.tsx's useQuery comment):
    // refetchInterval ticks while a fetch for that key is still outstanding
    // are absorbed rather than queued, so a response slower than the interval
    // cannot pile polls up.
    getSettingsMock.mockResolvedValue({ history_page: { autorefresh: "on" } });
    rs.useFakeTimers();
    fetchHistoryChartMock.mockReturnValue(new Promise(() => {})); // never settles

    renderPage();
    await settle();
    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);

    await tick(REFRESH_MS * 4);

    expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);
  });

  it("renders the saved-cook list below the chart, as the Flask page does", async () => {
    fetchHistoryChartMock.mockResolvedValue(PAYLOAD);
    renderPage();
    expect(await screen.findByTestId("cookfile-list")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Saved cooks" })).toBeInTheDocument();
  });

  it("links to the metrics page", async () => {
    //  Ported from blueprints/history/templates/history/index.html:47, the only
    //  link into /metrics anywhere in the Flask tree -- the navbar has never
    //  carried one. Dropping it in the first history port left the React
    //  /metrics unreachable by clicking.
    renderPage();
    const link = await screen.findByRole("link", { name: "Metrics" });
    expect(link).toHaveAttribute("href", "/metrics");
  });
});
