import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import * as actualCookfileApi from "../../helpers/files/cookfileApi" with {
  rstest: "importActual",
};
import type { CookFileDetail } from "../../helpers/files/cookfileApi";

const fetchCookFileDetailMock = rs.fn();
const recoverCookFileMock = rs.fn();
rs.mock("../../helpers/files/cookfileApi", () => ({
  ...actualCookfileApi,
  fetchCookFileDetail: (...args: unknown[]) => fetchCookFileDetailMock(...args),
  recoverCookFile: (...args: unknown[]) => recoverCookFileMock(...args),
}));

// The metadata card has its own suite; stubbed here so this module's
// assertions stay about routing, loading and the recovery branch.
rs.mock("./EventsTable", () => ({
  EventsTable: ({ units }: { units: string }) => (
    <div data-testid="events-table" data-units={units} />
  ),
}));

rs.mock("./CookFileChart", () => ({
  CookFileChart: () => <div data-testid="cookfile-chart" />,
}));

rs.mock("./CookFileMeta", () => ({
  CookFileMeta: ({ filename }: { filename: string }) => (
    <div data-testid="cookfile-meta" data-filename={filename} />
  ),
}));

const { CookFilePage } = await import("./CookFilePage");
const { CookFileRequestError } = actualCookfileApi;

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

function mount(filename = "Sunday Brisket.pifire") {
  return render(
    <MemoryRouter initialEntries={[`/cookfiles/${encodeURIComponent(filename)}`]}>
      <Routes>
        <Route path="/cookfiles/:filename" element={<CookFilePage />} />
      </Routes>
    </MemoryRouter>,
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
      new CookFileRequestError({
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
        new CookFileRequestError({ status: 422, message: "Error: broken", errortype }),
      );
      mount();
      expect(await screen.findByRole("button", { name: "Attempt Repair" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Attempt Conversion" })).not.toBeInTheDocument();
    },
  );

  it("recovering calls the matching action and refetches on success", async () => {
    const user = userEvent.setup();
    fetchCookFileDetailMock.mockRejectedValueOnce(
      new CookFileRequestError({ status: 422, message: "old", errortype: "version" }),
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
      new CookFileRequestError({ status: 422, message: "old", errortype: "version" }),
    );
    recoverCookFileMock.mockRejectedValue(
      new CookFileRequestError({ status: 422, message: "Repair failed.", errortype: "other" }),
    );
    mount();

    await user.click(await screen.findByRole("button", { name: "Attempt Conversion" }));
    expect(await screen.findByText("Repair failed.")).toBeInTheDocument();
  });

  it("a 404 says the file is missing and offers no recovery", async () => {
    fetchCookFileDetailMock.mockRejectedValue(
      new CookFileRequestError({ status: 404, message: "not_found", errortype: null }),
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
});
