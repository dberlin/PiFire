import type { RecipeDetail } from "@pifire/core/contracts/content";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { type QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { FileRequestError } from "../../../../src/helpers/files/apiEnvelope";
import { FIXTURE_DASH } from "../../../../src/helpers/fixture";
import { testQueryClient } from "../../test-utils";

const fetchRecipeDetailMock = rs.fn();
// SAFETY: runRecipe starts a real cook, so it is stubbed here too --
// RecipePage renders RecipeRunStatus, which imports it, even though this
// file never clicks Run. The metadata/ingredients/instructions writers are
// stubbed for the same reason: RecipeMetaEditor/IngredientsEditor/
// InstructionsEditor are rendered on every successful load, even though this
// file's tests never click their buttons -- their own test files cover the
// actual save/refetch behaviour.
const runRecipeMock = rs.fn();
// Hoisted (rather than inlined) because the refetch-cascade test below needs
// to control what it resolves to; the rest are exercised by their own
// editors' dedicated test files, not here.
const updateIngredientMock = rs.fn();
rs.mock("../../../../src/helpers/files/recipeApi", () => ({
  fetchRecipeDetail: (...a: unknown[]) => fetchRecipeDetailMock(...a),
  runRecipe: (...a: unknown[]) => runRecipeMock(...a),
  assetUrl: (parentId: string, name: string) => `/static/img/tmp/${parentId}/${name}`,
  saveRecipeMetadata: rs.fn(),
  addIngredient: rs.fn(),
  updateIngredient: (...a: unknown[]) => updateIngredientMock(...a),
  deleteIngredient: rs.fn(),
  addInstruction: rs.fn(),
  updateInstruction: rs.fn(),
  deleteInstruction: rs.fn(),
  // StepsEditor / RecipeAssetManager render on every successful load too --
  // their own test files cover the actual insert/update/delete/upload/attach
  // behaviour, so these only need to exist, not do anything.
  insertStep: rs.fn(),
  updateStep: rs.fn(),
  deleteStep: rs.fn(),
  uploadRecipeAssets: rs.fn(),
  setRecipeAssets: rs.fn(),
  deleteRecipeAssets: rs.fn(),
}));

const useShellStateMock = rs.fn();
rs.mock("../../../../src/helpers/shellContext", () => ({
  useShellState: () => useShellStateMock(),
}));

const { RecipePage } = await import("../../../../src/components/recipes/RecipePage");

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

function mount(
  filename: string,
  live: DashSocketPayload = FIXTURE_DASH,
  queryClient: QueryClient = testQueryClient(),
) {
  useShellStateMock.mockReturnValue({ live });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/recipes/${filename}`]}>
        <Routes>
          <Route path="/recipes/:filename" element={<RecipePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  fetchRecipeDetailMock.mockReset();
  runRecipeMock.mockReset();
  updateIngredientMock.mockReset();
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

  // THE RULE TASK 14+15 EXISTS TO ENFORCE: recipes_api.py's update_ingredient
  // rewrites the OLD ingredient name to the new one inside every instruction
  // that referenced it. IngredientsEditor and InstructionsEditor are siblings
  // that each only see their own slice of `detail.recipe` -- the ONLY way the
  // instruction list can show the renamed name is for the save to trigger a
  // refetch of the WHOLE detail (onChanged -> RecipePage's reload), not a
  // local patch of either editor's own state.
  it("renaming an ingredient refetches the whole detail and updates the instruction that referenced it", async () => {
    const user = userEvent.setup();
    const before: RecipeDetail = {
      ...DETAIL,
      recipe: {
        ingredients: [{ name: "Salt", quantity: "1 tsp", assets: [] }],
        instructions: [{ text: "Add salt", ingredients: ["Salt"], assets: [], step: 0 }],
        steps: [],
      },
    };
    const after: RecipeDetail = {
      ...DETAIL,
      recipe: {
        ingredients: [{ name: "Sea Salt", quantity: "1 tsp", assets: [] }],
        instructions: [{ text: "Add salt", ingredients: ["Sea Salt"], assets: [], step: 0 }],
        steps: [],
      },
    };
    fetchRecipeDetailMock.mockResolvedValueOnce(before).mockResolvedValueOnce(after);
    updateIngredientMock.mockResolvedValue(null);

    mount("brisket.pfrecipe");
    await waitFor(() => expect(screen.getAllByText("Salt").length).toBeGreaterThan(0));

    const nameField = screen.getByLabelText("Name for ingredient 1");
    await user.clear(nameField);
    await user.type(nameField, "Sea Salt");
    await user.click(screen.getByRole("button", { name: "Save ingredient 1" }));

    await waitFor(() =>
      expect(updateIngredientMock).toHaveBeenCalledWith("brisket.pfrecipe", 0, "Sea Salt", "1 tsp"),
    );
    // The save's success handler calls onChanged -> reload -> a SECOND fetch
    // of the whole detail. Nothing here patches instruction.ingredients
    // locally -- it can only come from that refetch.
    await waitFor(() => expect(fetchRecipeDetailMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryAllByText("Salt")).toHaveLength(0));
    expect(screen.getAllByText("Sea Salt").length).toBeGreaterThan(0);
  });

  // Task 11 (brief step 2): the requestId counter is gone -- an editor save
  // must now invalidate the recipe's query key rather than bump a counter.
  it("re-reads the recipe after an editor saves", async () => {
    const withIngredient: RecipeDetail = {
      ...DETAIL,
      recipe: {
        ingredients: [{ name: "Pork", quantity: "1 slab", assets: [] }],
        instructions: [],
        steps: [],
      },
    };
    const afterSave: RecipeDetail = {
      ...withIngredient,
      recipe: {
        ...withIngredient.recipe,
        ingredients: [{ name: "brisket", quantity: "1", assets: [] }],
      },
    };
    fetchRecipeDetailMock.mockResolvedValueOnce(withIngredient).mockResolvedValue(afterSave);
    updateIngredientMock.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    mount("brisket.pfrecipe");
    await waitFor(() => expect(screen.getByText("Pork")).toBeVisible());
    expect(fetchRecipeDetailMock).toHaveBeenCalledTimes(1);

    // The save button starts disabled (nothing is dirty yet) -- an edit is
    // required before the button is even clickable.
    const nameField = screen.getByLabelText("Name for ingredient 1");
    await user.clear(nameField);
    await user.type(nameField, "brisket");
    await user.click(screen.getByRole("button", { name: "Save ingredient 1" }));
    await waitFor(() => expect(screen.getByText("brisket")).toBeVisible());
  });

  // Deliberate-break coverage (task instructions #2): a query key that
  // dropped `filename` would let two different recipes' data collide in the
  // same QueryClient. Both instances stay mounted (no cleanup between) so a
  // shared cache entry would flip BOTH headings to whichever fetch resolves
  // last, not just the second one.
  it("keeps two different recipes' data separate in the same query cache", async () => {
    const queryClient = testQueryClient();
    const other: RecipeDetail = {
      ...DETAIL,
      filename: "other.pfrecipe",
      metadata: { ...DETAIL.metadata, title: "Other Recipe" },
    };
    fetchRecipeDetailMock.mockImplementation((filename: string) =>
      Promise.resolve(filename === "brisket.pfrecipe" ? DETAIL : other),
    );

    mount("brisket.pfrecipe", FIXTURE_DASH, queryClient);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Sunday Brisket" })).toBeInTheDocument(),
    );

    mount("other.pfrecipe", FIXTURE_DASH, queryClient);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Other Recipe" })).toBeInTheDocument(),
    );
    // Still there -- proves the two recipes occupy separate cache entries
    // rather than one clobbering the other.
    expect(screen.getByRole("heading", { name: "Sunday Brisket" })).toBeInTheDocument();
  });
});
