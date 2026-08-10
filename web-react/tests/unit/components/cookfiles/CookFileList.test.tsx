import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
// The two pure helpers this component also uses -- thumbnailUrl and
// cookFileDownloadUrl -- are kept REAL, so the URL contract the server enforces
// is asserted here rather than restated by a stub.
import * as actualCookfileApi from "../../../../src/helpers/files/cookfileApi" with {
  rstest: "importActual",
};
import * as actualFilesApi from "../../../../src/helpers/files/filesApi" with {
  rstest: "importActual",
};
import type { FileListing } from "../../../../src/helpers/contracts/content.gen";
import { queryKeys } from "../../../../src/helpers/query/keys";
import { testQueryClient } from "../../test-utils";

const fetchFileListingMock = rs.fn();
rs.mock("../../../../src/helpers/files/filesApi", () => ({
  ...actualFilesApi,
  fetchFileListing: (...args: unknown[]) => fetchFileListingMock(...args),
}));

const deleteCookFileMock = rs.fn();
const uploadCookFileMock = rs.fn();
rs.mock("../../../../src/helpers/files/cookfileApi", () => ({
  ...actualCookfileApi,
  deleteCookFile: (...args: unknown[]) => deleteCookFileMock(...args),
  uploadCookFile: (...args: unknown[]) => uploadCookFileMock(...args),
}));

const { CookFileList } = await import("../../../../src/components/cookfiles/CookFileList");

const LISTING: FileListing = {
  items: [
    {
      filename: "2026-07-20--1400-CookFile.pifire",
      title: "Sunday Brisket",
      thumbnail: "id-1/t.png",
    },
    { filename: "2026-07-19--0900-CookFile.pifire", title: "", thumbnail: "" },
  ],
  page: 1,
  last_page: 3,
  per_page: 10,
  reverse: true,
  total: 24,
};

const EMPTY: FileListing = {
  items: [],
  page: 1,
  last_page: 1,
  per_page: 10,
  reverse: true,
  total: 0,
};

function mount(queryClient: QueryClient = testQueryClient()) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <CookFileList />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function lastOptions(): Record<string, unknown> {
  const calls = fetchFileListingMock.mock.calls;
  return calls[calls.length - 1][1] as Record<string, unknown>;
}

describe("CookFileList", () => {
  beforeEach(() => {
    fetchFileListingMock.mockReset();
    deleteCookFileMock.mockReset();
    uploadCookFileMock.mockReset();
    fetchFileListingMock.mockResolvedValue(LISTING);
    deleteCookFileMock.mockResolvedValue(null);
    uploadCookFileMock.mockResolvedValue("Uploaded.pifire");
  });

  afterEach(cleanup);

  it("lists saved cooks with titles and links to the detail route", async () => {
    mount();
    const link = await screen.findByRole("link", { name: "Sunday Brisket" });
    expect(link).toHaveAttribute("href", "/cookfiles/2026-07-20--1400-CookFile.pifire");
    expect(fetchFileListingMock.mock.calls[0][0]).toBe("cookfiles");
  });

  it("opens on the same window the Flask list does", async () => {
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });
    expect(lastOptions()).toMatchObject({ page: 1, perPage: 10, reverse: true });
  });

  it("falls back to the placeholder thumbnail when the archive has none", async () => {
    const { container } = mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });
    //  alt="" on purpose -- the thumbnail carries no information the row's
    //  title does not, so it is decorative and has no img role to query.
    const images = container.querySelectorAll("img.pf-cf-thumb");
    expect(images[0]).toHaveAttribute("src", "/static/img/tmp/id-1/t.png");
    expect(images[1]).toHaveAttribute("src", "/static/img/pifire-cf-thumb.png");
  });

  it("shows the filename when a cook has no title", async () => {
    mount();
    expect(
      await screen.findByRole("link", { name: "2026-07-19--0900-CookFile.pifire" }),
    ).toBeInTheDocument();
  });

  it("next page refetches with page+1 and does not blank the table while loading", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });

    //  Deliberately a box rather than a bare `let`: TS narrows an assigned-in-
    //  callback `let` to `never` at the later call site.
    const pending: { resolve?: (value: FileListing) => void } = {};
    fetchFileListingMock.mockImplementationOnce(
      () =>
        new Promise<FileListing>((r) => {
          pending.resolve = r;
        }),
    );
    await user.click(screen.getByRole("button", { name: "Next page" }));

    expect(lastOptions()).toMatchObject({ page: 2 });
    // Still showing the previous page's rows while the next one is in flight.
    expect(screen.getByRole("link", { name: "Sunday Brisket" })).toBeInTheDocument();
    pending.resolve?.({ ...LISTING, page: 2, items: [] });
    await waitFor(() => expect(screen.getByText(/No saved cooks/)).toBeInTheDocument());
  });

  it("the sort button flips reverse and refetches", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });

    await user.click(screen.getByRole("button", { name: "Sort: newest first" }));
    await waitFor(() => expect(lastOptions()).toMatchObject({ reverse: false }));
  });

  it("changing per-page refetches and resets to page 1", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(lastOptions()).toMatchObject({ page: 2 }));

    await user.selectOptions(screen.getByLabelText("Per page"), "25");
    await waitFor(() => expect(lastOptions()).toMatchObject({ page: 1, perPage: 25 }));
  });

  it("delete asks for confirmation first, then refetches the listing", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });
    const before = fetchFileListingMock.mock.calls.length;

    await user.click(
      screen.getByRole("button", { name: "Delete 2026-07-20--1400-CookFile.pifire" }),
    );
    expect(deleteCookFileMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(deleteCookFileMock).toHaveBeenCalledWith("2026-07-20--1400-CookFile.pifire"),
    );
    await waitFor(() => expect(fetchFileListingMock.mock.calls.length).toBeGreaterThan(before));
  });

  it("a successful delete invalidates the deleted file's own cache entry", async () => {
    // This list keeps its own listing state, not react-query's -- reload()
    // already covers ITS refetch. What it does not cover is CookFilePage's
    // cache, keyed on queryKeys.cookfileRoot(file), which knows nothing about
    // a delete that happened from this list. Seed it on a real QueryClient and
    // assert against cache state directly: fetchFileListingMock refetching is
    // not evidence this specific key was touched.
    const filename = "2026-07-20--1400-CookFile.pifire";
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: Number.POSITIVE_INFINITY } },
    });
    queryClient.setQueryData(queryKeys.cookfileDetail(filename), { seeded: true });
    queryClient.setQueryData(queryKeys.cookfileChart(filename), { seeded: true });

    const user = userEvent.setup();
    mount(queryClient);
    await screen.findByRole("link", { name: "Sunday Brisket" });

    await user.click(screen.getByRole("button", { name: `Delete ${filename}` }));
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect(queryClient.getQueryState(queryKeys.cookfileDetail(filename))?.isInvalidated).toBe(
        true,
      );
      expect(queryClient.getQueryState(queryKeys.cookfileChart(filename))?.isInvalidated).toBe(
        true,
      );
    });
  });

  it("dismissing the delete confirmation deletes nothing", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });

    await user.click(
      screen.getByRole("button", { name: "Delete 2026-07-20--1400-CookFile.pifire" }),
    );
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(deleteCookFileMock).not.toHaveBeenCalled();
  });

  it("a failed listing shows an error banner and keeps the last good rows", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });

    fetchFileListingMock.mockRejectedValueOnce(new Error("HTTP 500"));
    await user.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() => expect(screen.getByText(/Couldn't load saved cooks/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Sunday Brisket" })).toBeInTheDocument();
  });

  it("an empty folder shows an empty state, not an empty table", async () => {
    fetchFileListingMock.mockResolvedValue(EMPTY);
    mount();
    expect(await screen.findByText(/No saved cooks/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("upload posts the chosen file then refetches", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });
    const before = fetchFileListingMock.mock.calls.length;

    const archive = new File([new Uint8Array([1])], "New.pifire");
    await user.upload(screen.getByLabelText("Upload a cook file"), archive);

    await waitFor(() => expect(uploadCookFileMock).toHaveBeenCalledWith(archive));
    await waitFor(() => expect(fetchFileListingMock.mock.calls.length).toBeGreaterThan(before));
  });

  it("reports a rejected upload without blaming the listing", async () => {
    //  applyAccept: false so userEvent does not filter the file out on the
    //  input's accept=".pifire" hint. The real gate is the server's
    //  ALLOWED_EXTENSIONS check, which is what this asserts is surfaced.
    const user = userEvent.setup({ applyAccept: false });
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });

    uploadCookFileMock.mockRejectedValueOnce(new Error("disallowed_file"));
    await user.upload(
      screen.getByLabelText("Upload a cook file"),
      new File([new Uint8Array([1])], "payload.sh"),
    );

    expect(await screen.findByText(/disallowed_file/)).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load saved cooks/)).not.toBeInTheDocument();
  });

  it("an ERROR-titled row is still openable so the repair prompt is reachable", async () => {
    fetchFileListingMock.mockResolvedValue({
      ...LISTING,
      items: [{ filename: "Broken.pifire", title: "ERROR", thumbnail: "" }],
    });
    mount();
    const row = (await screen.findByText("Broken.pifire")).closest("tr");
    expect(row).not.toBeNull();
    //  browse_files reports title "ERROR" for an archive it cannot open, and
    //  that row is precisely the one a user must click to reach the repair
    //  prompt -- so it stays a live link.
    const link = within(row as HTMLElement).getByRole("link", { name: "ERROR" });
    expect(link).toHaveAttribute("href", "/cookfiles/Broken.pifire");
    expect(link).not.toHaveAttribute("aria-disabled");
  });
});
