import type { Ingredient, Instruction, RecipeStep } from "@pifire/core/contracts/content";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as actualRecipeApi from "../../../../src/helpers/files/recipeApi" with {
  rstest: "importActual",
};

const addInstructionMock = rs.fn();
const updateInstructionMock = rs.fn();
const deleteInstructionMock = rs.fn();
rs.mock("../../../../src/helpers/files/recipeApi", () => ({
  ...actualRecipeApi,
  addInstruction: (...args: unknown[]) => addInstructionMock(...args),
  updateInstruction: (...args: unknown[]) => updateInstructionMock(...args),
  deleteInstruction: (...args: unknown[]) => deleteInstructionMock(...args),
}));

const { InstructionsEditor } =
  await import("../../../../src/components/recipes/InstructionsEditor");

function ingredient(overrides: Partial<Ingredient> = {}): Ingredient {
  return { name: "Salt", quantity: "1 tsp", assets: [], ...overrides };
}

function instruction(overrides: Partial<Instruction> = {}): Instruction {
  return { text: "Season the brisket", ingredients: [], assets: [], step: 0, ...overrides };
}

function step(overrides: Partial<RecipeStep> = {}): RecipeStep {
  return {
    mode: "Smoke",
    hold_temp: 0,
    timer: 0,
    notify: false,
    message: "",
    pause: false,
    trigger_temps: { primary: 0, food: [] },
    ...overrides,
  };
}

function mount(
  instructions: Instruction[],
  ingredients: Ingredient[] = [ingredient()],
  steps: RecipeStep[] = [step(), step()],
  onChanged = rs.fn(),
) {
  return {
    onChanged,
    ...render(
      <InstructionsEditor
        file="brisket.pfrecipe"
        ingredients={ingredients}
        instructions={instructions}
        steps={steps}
        onChanged={onChanged}
      />,
    ),
  };
}

describe("InstructionsEditor", () => {
  beforeEach(() => {
    for (const mock of [addInstructionMock, updateInstructionMock, deleteInstructionMock]) {
      mock.mockReset();
      mock.mockResolvedValue(null);
    }
  });

  afterEach(cleanup);

  it("says so when there are no instructions yet", () => {
    mount([]);
    expect(screen.getByText("No instructions listed.")).toBeInTheDocument();
  });

  it("renders the direction text and offers the current ingredient list as checkboxes, never free text", () => {
    mount(
      [instruction({ ingredients: ["Salt"] })],
      [ingredient({ name: "Salt" }), ingredient({ name: "Pepper" })],
    );
    expect(screen.getByLabelText("Direction 1")).toHaveValue("Season the brisket");
    // Exactly the recipe's current ingredients -- nothing the endpoint would
    // reject as an unknown name, and no free-text field to type one into.
    const fieldset = screen.getByRole("group", { name: "Ingredients used in direction 1" });
    expect(fieldset.querySelectorAll('input[type="checkbox"]')).toHaveLength(2);
    expect(screen.getByLabelText("Salt")).toBeChecked();
    expect(screen.getByLabelText("Pepper")).not.toBeChecked();
  });

  it("step 0 renders as Prep, matching RecipeView's read-only rendering", () => {
    mount([instruction({ step: 0 })]);
    expect(screen.getByLabelText("Program step for direction 1")).toHaveValue("0");
    expect(screen.getByRole("option", { name: "Prep" })).toBeInTheDocument();
  });

  it("offers one option per program step, valued by array index", () => {
    // `step` indexes recipe.steps; "Prep" is only step 0's LABEL. Numbering the
    // options 1..N instead stores the wrong index for every step and offers a
    // trailing option one past the end of the array -- which is what this
    // component did before, and what _macro_recipes.html:375-384 does not do.
    mount([instruction({ step: 0 })], [ingredient({ name: "Salt" })], [step(), step(), step()]);
    const select = screen.getByLabelText("Program step for direction 1");
    const options = [...select.querySelectorAll("option")].map((o) => [o.value, o.textContent]);
    expect(options).toEqual([
      ["0", "Prep"],
      ["1", "Step 1"],
      ["2", "Step 2"],
    ]);
  });

  it("editing text, ingredients and step then saving posts the whole replacement and refetches", async () => {
    const user = userEvent.setup();
    const { onChanged } = mount(
      [instruction({ text: "Season", ingredients: [], step: 0 })],
      [ingredient({ name: "Salt" })],
      [step(), step()],
    );

    const text = screen.getByLabelText("Direction 1");
    await user.clear(text);
    await user.type(text, "Season generously");
    await user.click(screen.getByLabelText("Salt"));
    await user.selectOptions(screen.getByLabelText("Program step for direction 1"), "1");
    await user.click(screen.getByRole("button", { name: "Save direction 1" }));

    await waitFor(() =>
      expect(updateInstructionMock).toHaveBeenCalledWith(
        "brisket.pfrecipe",
        0,
        "Season generously",
        ["Salt"],
        1,
      ),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("Save is disabled until something actually changes", () => {
    mount([instruction()]);
    expect(screen.getByRole("button", { name: "Save direction 1" })).toBeDisabled();
  });

  it("Add instruction posts the add action and refetches -- it never appends a row locally", async () => {
    const user = userEvent.setup();
    const { onChanged } = mount([instruction()]);

    await user.click(screen.getByRole("button", { name: "Add instruction" }));

    await waitFor(() => expect(addInstructionMock).toHaveBeenCalledWith("brisket.pfrecipe"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(screen.getAllByLabelText(/^Direction \d+$/)).toHaveLength(1);
  });

  it("delete asks for confirmation before posting", async () => {
    const user = userEvent.setup();
    mount([instruction()]);

    await user.click(screen.getByRole("button", { name: "Delete direction 1" }));
    expect(deleteInstructionMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(deleteInstructionMock).toHaveBeenCalledWith("brisket.pfrecipe", 0));
  });

  it("cancelling a delete deletes nothing", async () => {
    const user = userEvent.setup();
    mount([instruction()]);

    await user.click(screen.getByRole("button", { name: "Delete direction 1" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(deleteInstructionMock).not.toHaveBeenCalled();
  });

  // THE RULE THIS EDITOR EXISTS TO HONOUR: when the ingredients editor renames
  // an ingredient, this component must show the NEW name for a row that was
  // never re-rendered by anything this component did itself -- reflecting
  // whatever the `instructions`/`ingredients` props say NOW, not reconciling
  // its own state from the old ones.
  it("reflects a renamed ingredient the moment fresh props arrive, with no local reconciliation", () => {
    const { rerender } = render(
      <InstructionsEditor
        file="brisket.pfrecipe"
        ingredients={[ingredient({ name: "Salt" })]}
        instructions={[instruction({ text: "Add salt", ingredients: ["Salt"] })]}
        steps={[step()]}
        onChanged={rs.fn()}
      />,
    );
    expect(screen.getByLabelText("Salt")).toBeChecked();

    // Simulate the page's refetch after IngredientsEditor renamed "Salt" to
    // "Sea Salt" -- the server rewrote this instruction's own ingredients
    // list as part of that write (recipes_api.py's update_ingredient cascade).
    rerender(
      <InstructionsEditor
        file="brisket.pfrecipe"
        ingredients={[ingredient({ name: "Sea Salt" })]}
        instructions={[instruction({ text: "Add salt", ingredients: ["Sea Salt"] })]}
        steps={[step()]}
        onChanged={rs.fn()}
      />,
    );

    expect(screen.queryByLabelText("Salt")).toBeNull();
    expect(screen.getByLabelText("Sea Salt")).toBeChecked();
  });
});
