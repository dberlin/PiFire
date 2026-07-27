import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as actualRecipeApi from "../../helpers/files/recipeApi" with { rstest: "importActual" };
import type { RecipeMetadata, RecipeStep } from "../../helpers/files/recipeTypes";

const saveRecipeMetadataMock = rs.fn();
rs.mock("../../helpers/files/recipeApi", () => ({
  ...actualRecipeApi,
  saveRecipeMetadata: (...args: unknown[]) => saveRecipeMetadataMock(...args),
}));

const { RecipeMetaEditor } = await import("./RecipeMetaEditor");

function metadata(overrides: Partial<RecipeMetadata> = {}): RecipeMetadata {
  return {
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
    rating: 3,
    difficulty: "Hard",
    version: "1.0",
    food_probes: 2,
    ...overrides,
  };
}

function step(food: number[]): RecipeStep {
  return {
    mode: "Smoke",
    hold_temp: 0,
    timer: 0,
    notify: false,
    message: "",
    pause: false,
    trigger_temps: { primary: 0, food },
  };
}

function mount(m: RecipeMetadata, steps: RecipeStep[] = [], onChanged = rs.fn()) {
  return {
    onChanged,
    ...render(
      <RecipeMetaEditor file="brisket.pfrecipe" metadata={m} steps={steps} onChanged={onChanged} />,
    ),
  };
}

describe("RecipeMetaEditor", () => {
  beforeEach(() => {
    saveRecipeMetadataMock.mockReset();
    saveRecipeMetadataMock.mockResolvedValue(null);
  });

  afterEach(cleanup);

  it("seeds every field from metadata", () => {
    mount(metadata());
    expect(screen.getByLabelText("Title")).toHaveValue("Sunday Brisket");
    expect(screen.getByLabelText("Author")).toHaveValue("Alex");
    expect(screen.getByLabelText("Description")).toHaveValue("Low and slow.");
    // NumberField folds its suffix/hint text into the same <label>, so the
    // computed accessible name is more than just the bare field label.
    expect(screen.getByLabelText(/^Prep time/)).toHaveValue(20);
    expect(screen.getByLabelText(/^Cook time/)).toHaveValue(600);
    // NumberField folds its hint text into the same <label>, so the
    // computed accessible name is "Food probes" plus the hint sentence.
    expect(screen.getByLabelText(/^Food probes/)).toHaveValue(2);
    expect(screen.getByLabelText("Difficulty")).toHaveValue("Hard");
    expect(screen.getByLabelText("Units")).toHaveValue("F");
    expect(screen.getByRole("button", { name: "3 stars" })).toHaveAttribute("aria-pressed", "true");
  });

  it("has no unsaved-changes marker and a disabled-looking idle Save until something changes", async () => {
    const user = userEvent.setup();
    mount(metadata());
    expect(screen.queryByText("Unsaved changes")).toBeNull();

    await user.type(screen.getByLabelText("Title"), "!");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("saving posts the whole patch and refetches", async () => {
    const user = userEvent.setup();
    const { onChanged } = mount(metadata());

    const title = screen.getByLabelText("Title");
    await user.clear(title);
    await user.type(title, "Monday Brisket");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveRecipeMetadataMock).toHaveBeenCalledWith("brisket.pfrecipe", {
        title: "Monday Brisket",
        author: "Alex",
        description: "Low and slow.",
        difficulty: "Hard",
        units: "F",
        prep_time: 20,
        cook_time: 600,
        rating: 3,
        food_probes: 2,
      }),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(await screen.findByText("Saved ✓")).toBeInTheDocument();
  });

  it("clicking a star sets the rating and saves it", async () => {
    const user = userEvent.setup();
    mount(metadata({ rating: 1 }));

    await user.click(screen.getByRole("button", { name: "5 stars" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveRecipeMetadataMock).toHaveBeenCalledWith(
        "brisket.pfrecipe",
        expect.objectContaining({ rating: 5 }),
      ),
    );
  });

  it("raising food_probes saves straight through, no confirmation", async () => {
    const user = userEvent.setup();
    mount(metadata({ food_probes: 1 }), [step([50])]);

    const probes = screen.getByLabelText(/^Food probes/);
    await user.clear(probes);
    await user.type(probes, "3");
    await user.tab(); // blur -- NumberField clamps/commits on blur
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(screen.queryByText(/lose a trigger temperature/)).toBeNull();
    await waitFor(() => expect(saveRecipeMetadataMock).toHaveBeenCalled());
  });

  it("lowering food_probes below what any step needs confirms, naming the step count", async () => {
    const user = userEvent.setup();
    mount(metadata({ food_probes: 2 }), [step([100, 200]), step([150, 175])]);

    const probes = screen.getByLabelText(/^Food probes/);
    await user.clear(probes);
    await user.type(probes, "1");
    await user.tab();
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(screen.getByText(/2 program steps will lose a trigger temperature/)).toBeInTheDocument();
    expect(saveRecipeMetadataMock).not.toHaveBeenCalled();
  });

  it("cancelling the food-probe confirmation saves nothing", async () => {
    const user = userEvent.setup();
    mount(metadata({ food_probes: 2 }), [step([100, 200])]);

    const probes = screen.getByLabelText(/^Food probes/);
    await user.clear(probes);
    await user.type(probes, "0");
    await user.tab();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(saveRecipeMetadataMock).not.toHaveBeenCalled();
  });

  it("confirming the food-probe warning saves the lowered value", async () => {
    const user = userEvent.setup();
    mount(metadata({ food_probes: 2 }), [step([100, 200])]);

    const probes = screen.getByLabelText(/^Food probes/);
    await user.clear(probes);
    await user.type(probes, "0");
    await user.tab();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() =>
      expect(saveRecipeMetadataMock).toHaveBeenCalledWith(
        "brisket.pfrecipe",
        expect.objectContaining({ food_probes: 0 }),
      ),
    );
  });

  it("lowering food_probes when no step actually carries that many probes saves straight through", async () => {
    const user = userEvent.setup();
    // metadata says 2, but every step already has only 1 -- nothing truncates.
    mount(metadata({ food_probes: 2 }), [step([100])]);

    const probes = screen.getByLabelText(/^Food probes/);
    await user.clear(probes);
    await user.type(probes, "1");
    await user.tab();
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(screen.queryByText(/lose a trigger temperature/)).toBeNull();
    await waitFor(() => expect(saveRecipeMetadataMock).toHaveBeenCalled());
  });

  it("a rejected save surfaces the server's message", async () => {
    const user = userEvent.setup();
    saveRecipeMetadataMock.mockRejectedValue(new Error("recipe is read-only"));
    mount(metadata());

    await user.type(screen.getByLabelText("Title"), "!");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("recipe is read-only")).toBeInTheDocument();
  });
});
