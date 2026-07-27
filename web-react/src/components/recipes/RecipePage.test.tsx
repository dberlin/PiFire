import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { FileRequestError } from "../../helpers/files/apiEnvelope";
import type { RecipeDetail } from "../../helpers/files/recipeTypes";
import { FIXTURE_DASH } from "../../helpers/fixture";
import type { LiveState } from "../../helpers/types";

const fetchRecipeDetailMock = rs.fn();
// SAFETY: runRecipe starts a real cook, so it is stubbed here too --
// RecipePage renders RecipeRunStatus, which imports it, even though this
// file never clicks Run.
const runRecipeMock = rs.fn();
rs.mock("../../helpers/files/recipeApi", () => ({
  fetchRecipeDetail: (...a: unknown[]) => fetchRecipeDetailMock(...a),
  runRecipe: (...a: unknown[]) => runRecipeMock(...a),
  assetUrl: (parentId: string, name: string) => `/static/img/tmp/${parentId}/${name}`,
}));

const useShellStateMock = rs.fn();
rs.mock("../../helpers/shellContext", () => ({
  useShellState: () => useShellStateMock(),
}));

const { RecipePage } = await import("./RecipePage");

const DETAIL: RecipeDetail = {
  filename: "brisket.pfrecipe",
  metadata: {
    author: "Alex",
    username: "alex",
    id: "recipe-id-1",
    title: "Sunday Brisket",
    description: "Low and slow.",
    image: "",
    thumbnail: "",
    units: "F",
    prep_time: 20,
    cook_time: 600,
    rating: 4,
    difficulty: "Hard",
    version: "1.0",
    food_probes: 2,
  },
  recipe: { ingredients: [], instructions: [], steps: [] },
  assets: [],
};

function mount(filename: string, live: LiveState = FIXTURE_DASH) {
  useShellStateMock.mockReturnValue({ live });
  render(
    <MemoryRouter initialEntries={[`/recipes/${filename}`]}>
      <Routes>
        <Route path="/recipes/:filename" element={<RecipePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  fetchRecipeDetailMock.mockReset();
  runRecipeMock.mockReset();
});

afterEach(cleanup);

describe("RecipePage", () => {
  it("shows a loading hint, then the recipe once it resolves", async () => {
    fetchRecipeDetailMock.mockResolvedValue(DETAIL);
    mount("brisket.pfrecipe");
    expect(screen.getByText("Loading recipe…")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Sunday Brisket")).toBeInTheDocument());
    expect(fetchRecipeDetailMock).toHaveBeenCalledWith("brisket.pfrecipe");
  });

  // Unlike a cook file's 422, there is no repair/upgrade path for a
  // .pfrecipe -- any error just renders the message and nothing else.
  it("renders a 422 as a plain error, with no recover/repair prompt", async () => {
    fetchRecipeDetailMock.mockRejectedValue(
      new FileRequestError({ status: 422, message: "bad recipe archive", errortype: "other" }),
    );
    mount("broken.pfrecipe");
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load this recipe: bad recipe archive/)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /repair/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /conversion/i })).toBeNull();
  });

  it("renders a 404 with a not-found message", async () => {
    fetchRecipeDetailMock.mockRejectedValue(
      new FileRequestError({ status: 404, message: "missing", errortype: null }),
    );
    mount("gone.pfrecipe");
    await waitFor(() =>
      expect(screen.getByText("That recipe is not in the recipes folder.")).toBeInTheDocument(),
    );
  });

  it("wires this file's recipeStatus into RecipeRunStatus, off the live socket state", async () => {
    fetchRecipeDetailMock.mockResolvedValue(DETAIL);
    mount("brisket.pfrecipe", {
      ...FIXTURE_DASH,
      recipeStatus: {
        recipeMode: true,
        filename: "brisket.pfrecipe",
        mode: "Recipe",
        paused: false,
        step: 1,
      },
    });
    await waitFor(() => expect(screen.getByText(/on step 1/)).toBeInTheDocument());
  });
});
