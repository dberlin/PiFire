import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import type { CookFileDetail } from "../../../../src/helpers/contracts/content.gen";
import { FileRequestError } from "../../../../src/helpers/files/apiEnvelope";
import * as actualCookfileApi from "../../../../src/helpers/files/cookfileApi" with {
  rstest: "importActual",
};
import { queryKeys } from "../../../../src/helpers/query/keys";
import { testQueryClient } from "../../test-utils";

const fetchCookFileDetailMock = rs.fn();
const recoverCookFileMock = rs.fn();
rs.mock("../../../../src/helpers/files/cookfileApi", () => ({
  ...actualCookfileApi,
  fetchCookFileDetail: (...args: unknown[]) => fetchCookFileDetailMock(...args),
  recoverCookFile: (...args: unknown[]) => recoverCookFileMock(...args),
}));

// The metadata card has its own suite; stubbed here so this module's
// assertions stay about routing, loading and the recovery branch.
rs.mock("../../../../src/components/cookfiles/CommentList", () => ({
  CommentList: () => <div data-testid="comment-list" />,
}));

rs.mock("../../../../src/components/cookfiles/MediaPanel", () => ({
  MediaPanel: () => <div data-testid="media-panel" />,
}));

rs.mock("../../../../src/components/cookfiles/EventsTable", () => ({
  EventsTable: ({ units }: { units: string }) => (
    <div data-testid="events-table" data-units={units} />
  ),
}));

rs.mock("../../../../src/components/cookfiles/CookFileChart", () => ({
  CookFileChart: () => <div data-testid="cookfile-chart" />,
}));

rs.mock("../../../../src/components/cookfiles/CookFileMeta", () => ({
  CookFileMeta: ({ filename }: { filename: string }) => (
    <div data-testid="cookfile-meta" data-filename={filename} />
  ),
}));

const { CookFilePage } = await import("../../../../src/components/cookfiles/CookFilePage");

const DETAIL: CookFileDetail = {
  filename: "Sunday Brisket.pifire",
  metadata: {
    title: "Sunday Brisket",
    units: "F",
    thumbnail: "",
    id: "parent-id",
    version: "1.5.0",
    starttime: "12:00:00",
    endtime: "18:30:00",
    starttime_epoch: 1784942370612,
    endtime_epoch: 1784965970612,
  },
  graph_labels: { probes: { grill1: "Grill" }, targets: {}, primarysp: {} },
  events: [],
  event_totals: {},
  comments: [],
  assets: [],
};

function mount(filename = "Sunday Brisket.pifire", queryClient: QueryClient = testQueryClient()) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/cookfiles/${encodeURIComponent(filename)}`]}>
        <Routes>
          <Route path="/cookfiles/:filename" element={<CookFilePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CookFilePage", () => {
  beforeEach(() => {
    fetchCookFileDetailMock.mockReset();
    recoverCookFileMock.mockReset();
    fetchCookFileDetailMock.mockResolvedValue(DETAIL);
    recoverCookFileMock.mockResolvedValue(null);
  });

  afterEach(cleanup);

  it("renders the title once the detail resolves", async () => {
    mount();
    expect(await screen.findByRole("heading", { name: "Sunday Brisket" })).toBeInTheDocument();
  });

  it("shows a loading hint before it resolves", () => {
    fetchCookFileDetailMock.mockReturnValue(new Promise(() => {}));
    mount();
    expect(screen.getByText(/Loading cook file/)).toBeInTheDocument();
  });

  it("decodes the route parameter before asking the server", async () => {
    mount("Sunday Brisket #2.pifire");
    await waitFor(() =>
      expect(fetchCookFileDetailMock).toHaveBeenCalledWith("Sunday Brisket #2.pifire"),
    );
  });

  it("a 422 with errortype version offers Attempt Conversion", async () => {
    fetchCookFileDetailMock.mockRejectedValue(
      new FileRequestError({
        status: 422,
        message: "WARNING: Older cookfile version format! ",
        errortype: "version",
      }),
    );
    mount();
    expect(await screen.findByRole("button", { name: "Attempt Conversion" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Attempt Repair" })).not.toBeInTheDocument();
  });

  it.each(["asset", "other"] as const)(
    "a 422 with errortype %s offers Attempt Repair",
    async (errortype) => {
      fetchCookFileDetailMock.mockRejectedValue(
        new FileRequestError({ status: 422, message: "Error: broken", errortype }),
      );
      mount();
      expect(await screen.findByRole("button", { name: "Attempt Repair" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Attempt Conversion" })).not.toBeInTheDocument();
    },
  );

  it("recovering calls the matching action and refetches on success", async () => {
    const user = userEvent.setup();
    fetchCookFileDetailMock.mockRejectedValueOnce(
      new FileRequestError({ status: 422, message: "old", errortype: "version" }),
    );
    mount();

    await user.click(await screen.findByRole("button", { name: "Attempt Conversion" }));

    await waitFor(() =>
      expect(recoverCookFileMock).toHaveBeenCalledWith("Sunday Brisket.pifire", "upgrade"),
    );
    expect(await screen.findByTestId("cookfile-meta")).toBeInTheDocument();
  });

  it("a failed recovery keeps the prompt and reports why", async () => {
    const user = userEvent.setup();
    fetchCookFileDetailMock.mockRejectedValue(
      new FileRequestError({ status: 422, message: "old", errortype: "version" }),
    );
    recoverCookFileMock.mockRejectedValue(
      new FileRequestError({ status: 422, message: "Repair failed.", errortype: "other" }),
    );
    mount();

    await user.click(await screen.findByRole("button", { name: "Attempt Conversion" }));
    expect(await screen.findByText("Repair failed.")).toBeInTheDocument();
  });

  it("a 404 says the file is missing and offers no recovery", async () => {
    fetchCookFileDetailMock.mockRejectedValue(
      new FileRequestError({ status: 404, message: "not_found", errortype: null }),
    );
    mount();
    expect(await screen.findByText(/not in the history folder/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Attempt/ })).not.toBeInTheDocument();
  });

  it("a network failure reports the message rather than a blank page", async () => {
    fetchCookFileDetailMock.mockRejectedValue(new TypeError("Failed to fetch"));
    mount();
    expect(await screen.findByText(/Failed to fetch/)).toBeInTheDocument();
  });

  it("links back to the history page the list lives on", async () => {
    mount();
    await screen.findByTestId("cookfile-meta");
    expect(screen.getByRole("link", { name: "Back to history" })).toHaveAttribute(
      "href",
      "/history",
    );
  });

  it("hands the events table the COOK FILE's units, not the app's", async () => {
    fetchCookFileDetailMock.mockResolvedValue({
      ...DETAIL,
      metadata: { ...DETAIL.metadata, units: "C" },
    });
    mount();
    expect(await screen.findByTestId("events-table")).toHaveAttribute("data-units", "C");
  });

  it("surfaces the 422 errortype the repair prompt branches on", async () => {
    fetchCookFileDetailMock.mockRejectedValue(
      new FileRequestError({ status: 422, message: "old version", errortype: "version" }),
    );
    mount("cook.pifire");
    await waitFor(() => expect(screen.getByText(/old version/)).toBeVisible());
  });

  it("re-reads the file after a repair without a hand-rolled counter", async () => {
    // errortype "asset" (not "version"): the button under test is "Attempt
    // Repair", and "version" 422s offer "Attempt Conversion" instead (see the
    // "offers Attempt Conversion" test above) -- this test is about the
    // repair branch specifically.
    fetchCookFileDetailMock
      .mockRejectedValueOnce(
        new FileRequestError({ status: 422, message: "old version", errortype: "asset" }),
      )
      .mockResolvedValue(DETAIL);
    recoverCookFileMock.mockResolvedValue({ ok: true });
    mount("cook.pifire");
    await waitFor(() => expect(screen.getByText(/old version/)).toBeVisible());

    await userEvent.click(screen.getByRole("button", { name: /repair/i }));
    await waitFor(() => expect(screen.queryByText(/old version/)).not.toBeInTheDocument());
  });

  it("a successful repair invalidates the chart query too, not just the detail", async () => {
    // CookFileChart is mocked above, so it never observes its own query --
    // this asserts directly against the cache to prove the invalidate call
    // reaches the CHART's key, not only the detail's.
    const filename = "Sunday Brisket.pifire";
    // Not testQueryClient(): its gcTime: 0 garbage-collects an unobserved
    // query (CookFileChart is mocked, so nothing ever subscribes to the
    // chart key) before this test's own assertion can run.
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: Number.POSITIVE_INFINITY } },
    });
    queryClient.setQueryData(queryKeys.cookfileChart(filename), { seeded: true });

    fetchCookFileDetailMock.mockRejectedValueOnce(
      new FileRequestError({ status: 422, message: "old", errortype: "asset" }),
    );
    recoverCookFileMock.mockResolvedValue({ ok: true });
    mount(filename, queryClient);

    await userEvent.click(await screen.findByRole("button", { name: "Attempt Repair" }));

    await waitFor(() =>
      expect(queryClient.getQueryState(queryKeys.cookfileChart(filename))?.isInvalidated).toBe(
        true,
      ),
    );
  });
});
