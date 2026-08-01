import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router";
// The two pure helpers this component also uses -- thumbnailUrl and
// recipeDownloadUrl -- are kept REAL, so the URL contract the server enforces
// is asserted here rather than restated by a stub.
import * as actualFilesApi from "../../../../src/helpers/files/filesApi" with {
  rstest: "importActual",
};
import type { FileListing } from "../../../../src/helpers/files/fileTypes";
import * as actualRecipeApi from "../../../../src/helpers/files/recipeApi" with {
  rstest: "importActual",
};

const fetchFileListingMock = rs.fn();
rs.mock("../../../../src/helpers/files/filesApi", () => ({
  ...actualFilesApi,
  fetchFileListing: (...args: unknown[]) => fetchFileListingMock(...args),
}));

const createRecipeMock = rs.fn();
const deleteRecipeMock = rs.fn();
const uploadRecipeMock = rs.fn();
rs.mock("../../../../src/helpers/files/recipeApi", () => ({
  ...actualRecipeApi,
  createRecipe: (...args: unknown[]) => createRecipeMock(...args),
  deleteRecipe: (...args: unknown[]) => deleteRecipeMock(...args),
  uploadRecipe: (...args: unknown[]) => uploadRecipeMock(...args),
}));

const { RecipeList } = await import("../../../../src/components/recipes/RecipeList");

const LISTING: FileListing = {
  items: [
    { filename: "sunday-brisket.pifire", title: "Sunday Brisket", thumbnail: "id-1/t.png" },
    { filename: "no-title.pifire", title: "", thumbnail: "" },
  ],
  page: 1,
  last_page: 3,
  per_page: 10,
  reverse: false,
  total: 24,
};

const EMPTY: FileListing = {
  items: [],
  page: 1,
  last_page: 1,
  per_page: 10,
  reverse: false,
  total: 0,
};

function mount() {
  return render(
    <MemoryRouter>
      <RecipeList />
    </MemoryRouter>,
  );
}

function lastOptions(): Record<string, unknown> {
  const calls = fetchFileListingMock.mock.calls;
  return calls[calls.length - 1][1] as Record<string, unknown>;
}

describe("RecipeList", () => {
  beforeEach(() => {
    fetchFileListingMock.mockReset();
    createRecipeMock.mockReset();
    deleteRecipeMock.mockReset();
    uploadRecipeMock.mockReset();
    fetchFileListingMock.mockResolvedValue(LISTING);
    deleteRecipeMock.mockResolvedValue(null);
    uploadRecipeMock.mockResolvedValue("Uploaded.pifire");
  });

  afterEach(cleanup);

  it("lists recipes with titles and links to the detail route", async () => {
    mount();
    const link = await screen.findByRole("link", { name: "Sunday Brisket" });
    expect(link).toHaveAttribute("href", "/recipes/sunday-brisket.pifire");
    expect(fetchFileListingMock.mock.calls[0][0]).toBe("recipes");
  });

  // Flask's recipes.js opens gotoRFPage(1, false, 10) -- reverse=false -- the
  // opposite of the cook-file list, whose helper default (reverse=true) is
  // overridden explicitly in RecipeList.
  it("opens on the same window the Flask recipe list does", async () => {
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });
    expect(lastOptions()).toMatchObject({ page: 1, perPage: 10, reverse: false });
  });

  it("falls back to the placeholder thumbnail when the recipe has none", async () => {
    const { container } = mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });
    const images = container.querySelectorAll("img.pf-rcp-thumb");
    expect(images[0]).toHaveAttribute("src", "/static/img/tmp/id-1/t.png");
    expect(images[1]).toHaveAttribute("src", "/static/img/pifire-cf-thumb.png");
  });

  it("shows the filename when a recipe has no title", async () => {
    mount();
    expect(await screen.findByRole("link", { name: "no-title.pifire" })).toBeInTheDocument();
  });

  it("next page refetches with page+1 and does not blank the table while loading", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });

    const pending: { resolve?: (value: FileListing) => void } = {};
    fetchFileListingMock.mockImplementationOnce(
      () =>
        new Promise<FileListing>((r) => {
          pending.resolve = r;
        }),
    );
    await user.click(screen.getByRole("button", { name: "Next page" }));

    expect(lastOptions()).toMatchObject({ page: 2 });
    expect(screen.getByRole("link", { name: "Sunday Brisket" })).toBeInTheDocument();
    pending.resolve?.({ ...LISTING, page: 2, items: [] });
    await waitFor(() => expect(screen.getByText(/No recipes yet/)).toBeInTheDocument());
  });

  it("the sort button flips reverse and refetches", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });

    // Starts false, so the button reads "oldest first" -- clicking flips it.
    await user.click(screen.getByRole("button", { name: "Sort: oldest first" }));
    await waitFor(() => expect(lastOptions()).toMatchObject({ reverse: true }));
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

    await user.click(screen.getByRole("button", { name: "Delete sunday-brisket.pifire" }));
    expect(deleteRecipeMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(deleteRecipeMock).toHaveBeenCalledWith("sunday-brisket.pifire"));
    await waitFor(() => expect(fetchFileListingMock.mock.calls.length).toBeGreaterThan(before));
  });

  it("dismissing the delete confirmation deletes nothing", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });

    await user.click(screen.getByRole("button", { name: "Delete sunday-brisket.pifire" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(deleteRecipeMock).not.toHaveBeenCalled();
  });

  it("a failed listing shows an error banner and keeps the last good rows", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });

    fetchFileListingMock.mockRejectedValueOnce(new Error("HTTP 500"));
    await user.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() => expect(screen.getByText(/Couldn't load recipes/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Sunday Brisket" })).toBeInTheDocument();
  });

  it("an empty folder shows an empty state, not an empty table", async () => {
    fetchFileListingMock.mockResolvedValue(EMPTY);
    mount();
    expect(await screen.findByText(/No recipes yet/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("upload posts the chosen file then refetches", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });
    const before = fetchFileListingMock.mock.calls.length;

    const archive = new File([new Uint8Array([1])], "New.pifire");
    await user.upload(screen.getByLabelText("Upload a recipe"), archive);

    await waitFor(() => expect(uploadRecipeMock).toHaveBeenCalledWith(archive));
    await waitFor(() => expect(fetchFileListingMock.mock.calls.length).toBeGreaterThan(before));
  });

  it("reports a rejected upload without blaming the listing", async () => {
    const user = userEvent.setup({ applyAccept: false });
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });

    uploadRecipeMock.mockRejectedValueOnce(new Error("disallowed_file"));
    await user.upload(
      screen.getByLabelText("Upload a recipe"),
      new File([new Uint8Array([1])], "payload.sh"),
    );

    expect(await screen.findByText(/disallowed_file/)).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load recipes/)).not.toBeInTheDocument();
  });

  it("New recipe creates a recipe then navigates to its detail route", async () => {
    createRecipeMock.mockResolvedValue({ filename: "fresh-batch.pifire" });
    const user = userEvent.setup();
    const router = createMemoryRouter(
      [
        { path: "/recipes", element: <RecipeList /> },
        {
          path: "/recipes/:filename",
          element: <div data-testid="detail-route">detail</div>,
        },
      ],
      { initialEntries: ["/recipes"] },
    );
    render(<RouterProvider router={router} />);
    await screen.findByRole("link", { name: "Sunday Brisket" });

    await user.click(screen.getByRole("button", { name: "New recipe" }));

    expect(await screen.findByTestId("detail-route")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/recipes/fresh-batch.pifire");
  });

  it("a failed create is reported without navigating away", async () => {
    createRecipeMock.mockRejectedValueOnce(new Error("no_space"));
    const user = userEvent.setup();
    mount();
    await screen.findByRole("link", { name: "Sunday Brisket" });

    await user.click(screen.getByRole("button", { name: "New recipe" }));

    expect(await screen.findByText(/no_space/)).toBeInTheDocument();
  });
});
