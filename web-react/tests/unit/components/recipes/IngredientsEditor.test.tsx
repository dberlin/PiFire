import type { Ingredient, Instruction } from "@pifire/core/contracts/content";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as actualRecipeApi from "../../../../src/helpers/files/recipeApi" with {
  rstest: "importActual",
};

const addIngredientMock = rs.fn();
const updateIngredientMock = rs.fn();
const deleteIngredientMock = rs.fn();
rs.mock("../../../../src/helpers/files/recipeApi", () => ({
  ...actualRecipeApi,
  addIngredient: (...args: unknown[]) => addIngredientMock(...args),
  updateIngredient: (...args: unknown[]) => updateIngredientMock(...args),
  deleteIngredient: (...args: unknown[]) => deleteIngredientMock(...args),
}));

const { IngredientsEditor } = await import("../../../../src/components/recipes/IngredientsEditor");

function ingredient(overrides: Partial<Ingredient> = {}): Ingredient {
  return { name: "Salt", quantity: "1 tsp", assets: [], ...overrides };
}

function instruction(overrides: Partial<Instruction> = {}): Instruction {
  return { text: "Season the brisket", ingredients: [], assets: [], step: 0, ...overrides };
}

function mount(ingredients: Ingredient[], instructions: Instruction[] = [], onChanged = rs.fn()) {
  return {
    onChanged,
    ...render(
      <IngredientsEditor
        file="brisket.pfrecipe"
        ingredients={ingredients}
        instructions={instructions}
        onChanged={onChanged}
      />,
    ),
  };
}

describe("IngredientsEditor", () => {
  beforeEach(() => {
    for (const mock of [addIngredientMock, updateIngredientMock, deleteIngredientMock]) {
      mock.mockReset();
      mock.mockResolvedValue(null);
    }
  });

  afterEach(cleanup);

  it("says so when there are no ingredients yet", () => {
    mount([]);
    expect(screen.getByText("No ingredients listed.")).toBeInTheDocument();
  });

  it("renders each row's quantity and name", () => {
    mount([ingredient(), ingredient({ name: "Pepper", quantity: "2 tsp" })]);
    expect(screen.getByLabelText("Name for ingredient 1")).toHaveValue("Salt");
    expect(screen.getByLabelText("Quantity for ingredient 1")).toHaveValue("1 tsp");
    expect(screen.getByLabelText("Name for ingredient 2")).toHaveValue("Pepper");
  });

  it("Save is disabled until a field actually changes", async () => {
    const user = userEvent.setup();
    mount([ingredient()]);

    expect(screen.getByRole("button", { name: "Save ingredient 1" })).toBeDisabled();
    await user.type(screen.getByLabelText("Name for ingredient 1"), "!");
    expect(screen.getByRole("button", { name: "Save ingredient 1" })).toBeEnabled();
  });

  it("editing a row and saving posts the index, name and quantity, then refetches", async () => {
    const user = userEvent.setup();
    const { onChanged } = mount([ingredient()]);

    const nameField = screen.getByLabelText("Name for ingredient 1");
    await user.clear(nameField);
    await user.type(nameField, "Sea Salt");
    await user.click(screen.getByRole("button", { name: "Save ingredient 1" }));

    await waitFor(() =>
      expect(updateIngredientMock).toHaveBeenCalledWith("brisket.pfrecipe", 0, "Sea Salt", "1 tsp"),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("Add ingredient posts the add action and refetches -- it never appends a row locally", async () => {
    const user = userEvent.setup();
    const { onChanged } = mount([ingredient()]);

    await user.click(screen.getByRole("button", { name: "Add ingredient" }));

    await waitFor(() => expect(addIngredientMock).toHaveBeenCalledWith("brisket.pfrecipe"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    // Still one row: the new blank row only appears once the page refetches
    // and passes a longer `ingredients` prop back down.
    expect(screen.getAllByLabelText(/Name for ingredient/)).toHaveLength(1);
  });

  it("delete asks for confirmation naming how many instructions reference the ingredient", async () => {
    const user = userEvent.setup();
    mount(
      [ingredient({ name: "Salt" })],
      [instruction({ ingredients: ["Salt"] }), instruction({ ingredients: ["Salt"] })],
    );

    await user.click(screen.getByRole("button", { name: "Delete ingredient 1" }));
    expect(screen.getByText(/used in 2 instructions/)).toBeInTheDocument();
    expect(deleteIngredientMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(deleteIngredientMock).toHaveBeenCalledWith("brisket.pfrecipe", 0));
  });

  it("delete confirmation says nothing references it when no instruction does", async () => {
    const user = userEvent.setup();
    mount([ingredient({ name: "Salt" })], []);

    await user.click(screen.getByRole("button", { name: "Delete ingredient 1" }));
    expect(screen.getByText(/will be removed/)).toBeInTheDocument();
    expect(screen.queryByText(/used in/)).toBeNull();
  });

  it("cancelling a delete deletes nothing", async () => {
    const user = userEvent.setup();
    mount([ingredient()]);

    await user.click(screen.getByRole("button", { name: "Delete ingredient 1" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(deleteIngredientMock).not.toHaveBeenCalled();
  });

  it("a failed save surfaces the error without losing the draft", async () => {
    const user = userEvent.setup();
    updateIngredientMock.mockRejectedValue(new Error("recipe is locked"));
    mount([ingredient()]);

    const nameField = screen.getByLabelText("Name for ingredient 1");
    await user.clear(nameField);
    await user.type(nameField, "Sea Salt");
    await user.click(screen.getByRole("button", { name: "Save ingredient 1" }));

    expect(await screen.findByText("recipe is locked")).toBeInTheDocument();
    expect(screen.getByLabelText("Name for ingredient 1")).toHaveValue("Sea Salt");
  });
});
