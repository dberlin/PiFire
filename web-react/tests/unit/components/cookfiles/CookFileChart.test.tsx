import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import * as actualCookfileApi from "../../../../src/helpers/files/cookfileApi" with {
  rstest: "importActual",
};
import type { CookFileChartData } from "../../../../src/helpers/files/cookfileApi";
import { queryKeys } from "../../../../src/helpers/query/keys";
import { flushObservers, testQueryClient } from "../../test-utils";

const fetchCookFileChartMock = rs.fn();
rs.mock("../../../../src/helpers/files/cookfileApi", () => ({
  ...actualCookfileApi,
  fetchCookFileChart: (...args: unknown[]) => fetchCookFileChartMock(...args),
}));

// jsdom has no canvas, so the real uPlot chart is stubbed. The stub records the
// props it was handed -- so the ms -> s conversion and the annotation toggle
// are observable at the seam -- plus a per-INSTANCE id, so a test can tell a
// remount (which is how Reset zoom works) from a re-render.
let chartInstances = 0;
rs.mock("../../../../src/components/history/HistoryChart", () => ({
  HistoryChart: ({ times, annotations }: { times: number[]; annotations?: { xMin: number }[] }) => {
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
        data-annotations={
          annotations === undefined ? "none" : annotations.map((a) => a.xMin).join(",")
        }
      />
    );
  },
}));

const { CookFileChart } = await import("../../../../src/components/cookfiles/CookFileChart");

const PAYLOAD: CookFileChartData = {
  time_labels: [1784942370612, 1784942373612],
  chart_data: [
    {
      label: "Grill",
      borderColor: "#f00",
      hidden: false,
      data: [
        { x: 1784942370612, y: 220 },
        { x: 1784942373612, y: 225 },
      ],
    },
  ],
  probe_mapper: { probes: { grill1: 0 }, targets: {}, primarysp: {} },
  annotations: {
    event_0: {
      type: "line",
      xMin: 1784942370612,
      xMax: 1784942370612,
      borderColor: "#abc",
      label: { content: "Smoke" },
    },
  },
};

function renderChart(filename: string, queryClient: QueryClient = testQueryClient()) {
  return render(
    <QueryClientProvider client={queryClient}>
      <CookFileChart filename={filename} />
    </QueryClientProvider>,
  );
}

describe("CookFileChart", () => {
  beforeEach(() => {
    fetchCookFileChartMock.mockReset();
    fetchCookFileChartMock.mockResolvedValue(PAYLOAD);
  });

  afterEach(cleanup);

  it("fetches the chart separately from the detail payload", async () => {
    renderChart("Sunday.pifire");
    await screen.findByTestId("chart");
    expect(fetchCookFileChartMock).toHaveBeenCalledWith("Sunday.pifire");
  });

  it("shows a loading hint until the payload lands", () => {
    fetchCookFileChartMock.mockReturnValue(new Promise(() => {}));
    renderChart("Sunday.pifire");
    expect(screen.getByText(/Loading graph/)).toBeInTheDocument();
  });

  it("hands the chart epoch SECONDS, not the payload's milliseconds", async () => {
    renderChart("Sunday.pifire");
    expect(await screen.findByTestId("chart")).toHaveAttribute(
      "data-times",
      "1784942370.612,1784942373.612",
    );
  });

  it("passes annotations by default and withholds them when toggled off", async () => {
    const user = userEvent.setup();
    renderChart("Sunday.pifire");
    expect(await screen.findByTestId("chart")).toHaveAttribute(
      "data-annotations",
      "1784942370.612",
    );

    await user.click(screen.getByRole("checkbox", { name: /Show mode changes/ }));
    await waitFor(() =>
      expect(screen.getByTestId("chart")).toHaveAttribute("data-annotations", "none"),
    );
  });

  it("Reset zoom remounts the chart", async () => {
    const user = userEvent.setup();
    renderChart("Sunday.pifire");
    const before = (await screen.findByTestId("chart")).getAttribute("data-instance");

    await user.click(screen.getByRole("button", { name: "Reset zoom" }));

    await waitFor(() =>
      expect(screen.getByTestId("chart").getAttribute("data-instance")).not.toBe(before),
    );
  });

  it("offers the raw-data CSV beside the chart, as the Flask card does", async () => {
    renderChart("Sunday Brisket.pifire");
    await screen.findByTestId("chart");
    expect(screen.getByRole("link", { name: "Download CSV file" })).toHaveAttribute(
      "href",
      "/api/files/cookfiles/export?file=Sunday%20Brisket.pifire&kind=data",
    );
  });

  it("says the file has no chart data when the payload is empty", async () => {
    fetchCookFileChartMock.mockResolvedValue({ ...PAYLOAD, time_labels: [], chart_data: [] });
    renderChart("Sunday.pifire");
    expect(await screen.findByText(/no chart data/)).toBeInTheDocument();
    expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
  });

  it("distinguishes an old-format file from an empty one and points at repair", async () => {
    fetchCookFileChartMock.mockResolvedValue({
      ...PAYLOAD,
      time_labels: ["12:00:00", "12:05:00"],
    });
    renderChart("Sunday.pifire");
    expect(await screen.findByText(/older format/)).toBeInTheDocument();
    expect(screen.getByText(/Attempt Repair/)).toBeInTheDocument();
  });

  it("reports a failed chart fetch without hiding the rest of the page", async () => {
    fetchCookFileChartMock.mockRejectedValue(new Error("HTTP 500"));
    renderChart("Sunday.pifire");
    expect(await screen.findByText(/Couldn't load this cook's chart data/)).toBeInTheDocument();
  });

  it("drops the old chart once a refetch fails, rather than leaving it behind the error banner", async () => {
    // react-query does not clear `data` when a refetch errors -- the last
    // good payload stays cached under the same key, only `error`/`isError`
    // change. Rendering straight off `data` (as CookFileChart used to) would
    // therefore keep the PREVIOUS window's chart on screen right behind the
    // "couldn't load" banner, which reads as "here is the current chart, also
    // something went wrong" instead of "this chart is not trustworthy".
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: Number.POSITIVE_INFINITY } },
    });
    fetchCookFileChartMock.mockResolvedValueOnce(PAYLOAD);
    renderChart("Sunday.pifire", queryClient);
    await screen.findByTestId("chart");

    fetchCookFileChartMock.mockRejectedValueOnce(new Error("HTTP 500"));
    await act(() =>
      queryClient.invalidateQueries({ queryKey: queryKeys.cookfileChart("Sunday.pifire") }),
    );
    await flushObservers();

    expect(await screen.findByText(/Couldn't load this cook's chart data/)).toBeInTheDocument();
    expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
  });
});
