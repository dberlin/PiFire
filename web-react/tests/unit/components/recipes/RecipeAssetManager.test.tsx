import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  Ingredient,
  Instruction,
  RecipeAsset,
} from "../../../../src/helpers/contracts/content.gen";
import * as actualRecipeApi from "../../../../src/helpers/files/recipeApi" with {
  rstest: "importActual",
};

const uploadRecipeAssetsMock = rs.fn();
const deleteRecipeAssetsMock = rs.fn();
const setRecipeAssetsMock = rs.fn();
rs.mock("../../../../src/helpers/files/recipeApi", () => ({
  ...actualRecipeApi,
  uploadRecipeAssets: (...args: unknown[]) => uploadRecipeAssetsMock(...args),
  deleteRecipeAssets: (...args: unknown[]) => deleteRecipeAssetsMock(...args),
  setRecipeAssets: (...args: unknown[]) => setRecipeAssetsMock(...args),
}));

const { RecipeAssetManager } = await import(
  "../../../../src/components/recipes/RecipeAssetManager"
);

const ASSETS: RecipeAsset[] = [
  { id: "a1", filename: "a1.jpg", type: "image/jpeg" },
  { id: "a2", filename: "a2.jpg", type: "image/jpeg" },
];

const INGREDIENTS: Ingredient[] = [{ name: "Brisket", quantity: "1 whole", assets: ["a1.jpg"] }];
const INSTRUCTIONS: Instruction[] = [
  { text: "Trim the fat.", ingredients: ["Brisket"], assets: [], step: 0 },
];

function mount(
  overrides: Partial<{
    assets: RecipeAsset[];
    splash: string;
    ingredients: Ingredient[];
    instructions: Instruction[];
  }> = {},
  onChanged = rs.fn(),
) {
  return {
    onChanged,
    ...render(
      <RecipeAssetManager
        file="brisket.pfrecipe"
        parentId="recipe-id-1"
        assets={overrides.assets ?? ASSETS}
        splash={overrides.splash ?? ""}
        ingredients={overrides.ingredients ?? INGREDIENTS}
        instructions={overrides.instructions ?? INSTRUCTIONS}
        onChanged={onChanged}
      />,
    ),
  };
}

function png(name: string) {
  return new File([new Uint8Array([1])], name, { type: "image/png" });
}

describe("RecipeAssetManager", () => {
  beforeEach(() => {
    uploadRecipeAssetsMock.mockReset();
    deleteRecipeAssetsMock.mockReset();
    setRecipeAssetsMock.mockReset();
    uploadRecipeAssetsMock.mockResolvedValue([]);
    deleteRecipeAssetsMock.mockResolvedValue(null);
    setRecipeAssetsMock.mockResolvedValue({ assets: [] });
  });

  afterEach(cleanup);

  it("renders a grid over every archive asset, linking to the full-size image", () => {
    mount();
    expect(screen.getByAltText("a1.jpg")).toHaveAttribute(
      "src",
      "/static/img/tmp/recipe-id-1/a1.jpg",
    );
    expect(screen.getByAltText("a1.jpg").closest("a")).toHaveAttribute(
      "href",
      "/static/img/tmp/recipe-id-1/a1.jpg",
    );
  });

  it("the empty state invites an upload instead of showing an empty grid", () => {
    const { container } = mount({ assets: [] });
    expect(screen.getByText(/upload one to illustrate this recipe/)).toBeInTheDocument();
    expect(container.querySelector(".pf-rcp-media-grid")).toBeNull();
  });

  it("uploading images posts them under this recipe's filename and refetches", async () => {
    const user = userEvent.setup();
    const { onChanged } = mount();
    const image = png("shot.png");
    await user.upload(screen.getByLabelText("Upload photos"), image);

    await waitFor(() =>
      expect(uploadRecipeAssetsMock).toHaveBeenCalledWith("brisket.pfrecipe", [image]),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  // SPLASH is a section too (recipes_api.py's set_assets): a single choice,
  // not an arbitrary list, and it is set/cleared by index-less calls.
  it("choosing a splash image posts a single-item list with no index", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Use a2.jpg as splash image" }));
    await waitFor(() =>
      expect(setRecipeAssetsMock).toHaveBeenCalledWith("brisket.pfrecipe", "splash", ["a2.jpg"]),
    );
  });

  it("indicates the current splash image and does not offer to re-pick it", () => {
    mount({ splash: "a1.jpg" });
    expect(screen.getByRole("button", { name: "Current splash image" })).toBeDisabled();
    expect(screen.getByAltText("a1.jpg").className).toContain("pf-rcp-media-img--selected");
    expect(screen.getByAltText("a2.jpg").className).not.toContain("pf-rcp-media-img--selected");
  });

  it("clearing the splash image posts an empty list", async () => {
    const user = userEvent.setup();
    mount({ splash: "a1.jpg" });
    await user.click(screen.getByRole("button", { name: "Clear splash image" }));
    await waitFor(() =>
      expect(setRecipeAssetsMock).toHaveBeenCalledWith("brisket.pfrecipe", "splash", []),
    );
  });

  it("no splash image set offers nothing to clear", () => {
    mount({ splash: "" });
    expect(screen.queryByRole("button", { name: "Clear splash image" })).toBeNull();
  });

  it("selecting assets and confirming deletes exactly those, from the archive", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByLabelText("Select a2.jpg"));
    await user.click(screen.getByRole("button", { name: "Remove selected photos (1)" }));
    expect(deleteRecipeAssetsMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(deleteRecipeAssetsMock).toHaveBeenCalledWith("brisket.pfrecipe", ["a2.jpg"]),
    );
  });

  it("dismissing the delete confirmation removes nothing", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByLabelText("Select a1.jpg"));
    await user.click(screen.getByRole("button", { name: "Remove selected photos (1)" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(deleteRecipeAssetsMock).not.toHaveBeenCalled();
  });

  // INGREDIENTS/INSTRUCTIONS sections: attaching is a WHOLE-LIST write keyed
  // by the item's own index, seeded from that item's current asset list.
  it("attaching photos to an ingredient seeds the picker from its current assets and saves the whole selection", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByRole("button", { name: "Manage photos for Brisket" }));
    expect(screen.getByLabelText("Attach a1.jpg")).toBeChecked();
    expect(screen.getByLabelText("Attach a2.jpg")).not.toBeChecked();

    await user.click(screen.getByLabelText("Attach a2.jpg"));
    await user.click(screen.getByRole("button", { name: "Save attachments" }));

    await waitFor(() =>
      expect(setRecipeAssetsMock).toHaveBeenCalledWith(
        "brisket.pfrecipe",
        "ingredients",
        ["a1.jpg", "a2.jpg"],
        0,
      ),
    );
  });

  it("attaching photos to an instruction addresses it by its own index", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByRole("button", { name: "Manage photos for direction 1" }));
    await user.click(screen.getByLabelText("Attach a1.jpg"));
    await user.click(screen.getByRole("button", { name: "Save attachments" }));

    await waitFor(() =>
      expect(setRecipeAssetsMock).toHaveBeenCalledWith(
        "brisket.pfrecipe",
        "instructions",
        ["a1.jpg"],
        0,
      ),
    );
  });

  it("cancelling attach saves nothing", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Manage photos for Brisket" }));
    await user.click(screen.getByLabelText("Attach a2.jpg"));
    await user.click(screen.getByRole("button", { name: "Cancel attach" }));
    expect(setRecipeAssetsMock).not.toHaveBeenCalled();
  });

  it("no assets in the archive offers nothing to attach", () => {
    mount({ assets: [] });
    expect(screen.queryByRole("button", { name: "Manage photos for Brisket" })).toBeNull();
  });

  it("an unnamed ingredient falls back to a positional label", () => {
    mount({ ingredients: [{ name: "", quantity: "1 tsp", assets: [] }] });
    expect(
      screen.getByRole("button", { name: "Manage photos for ingredient 1" }),
    ).toBeInTheDocument();
  });

  it("a rejected upload surfaces the reason", async () => {
    const user = userEvent.setup({ applyAccept: false });
    uploadRecipeAssetsMock.mockRejectedValue(new Error("disallowed_file"));
    mount();
    await user.upload(
      screen.getByLabelText("Upload photos"),
      new File([new Uint8Array([1])], "evil.svg", { type: "image/svg+xml" }),
    );
    expect(await screen.findByText("disallowed_file")).toBeInTheDocument();
  });
});
