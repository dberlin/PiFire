import type { RecipeStep } from "@pifire/core/contracts/content";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as actualRecipeApi from "../../../../src/helpers/files/recipeApi" with {
  rstest: "importActual",
};

const insertStepMock = rs.fn();
const updateStepMock = rs.fn();
const deleteStepMock = rs.fn();
rs.mock("../../../../src/helpers/files/recipeApi", () => ({
  ...actualRecipeApi,
  insertStep: (...args: unknown[]) => insertStepMock(...args),
  updateStep: (...args: unknown[]) => updateStepMock(...args),
  deleteStep: (...args: unknown[]) => deleteStepMock(...args),
}));

const { StepsEditor } = await import("../../../../src/components/recipes/StepsEditor");

function step(overrides: Partial<RecipeStep> = {}): RecipeStep {
  return {
    mode: "Smoke",
    hold_temp: 0,
    timer: 0,
    notify: false,
    message: "",
    pause: false,
    trigger_temps: { primary: 0, food: [0] },
    ...overrides,
  };
}

function mount(steps: RecipeStep[], units = "F", onChanged = rs.fn()) {
  return {
    onChanged,
    ...render(
      <StepsEditor file="brisket.pfrecipe" steps={steps} units={units} onChanged={onChanged} />,
    ),
  };
}

describe("StepsEditor", () => {
  beforeEach(() => {
    insertStepMock.mockReset();
    updateStepMock.mockReset();
    deleteStepMock.mockReset();
    insertStepMock.mockResolvedValue(null);
    updateStepMock.mockResolvedValue(null);
    deleteStepMock.mockResolvedValue(null);
  });

  afterEach(cleanup);

  // THE RULE THIS ENFORCES: 0 is the disabled sentinel for every
  // trigger_temps member (and the timer), not a real temperature/count. An
  // enable switch OFF must write 0, and a field already at 0 must render
  // disabled rather than as an editable "0". Because `enabled` is derived
  // straight from `value > 0` (not a separate boolean), the switch and the
  // field can never disagree about which state they are in.
  it("SENTINEL: the primary-trigger switch off writes 0 and the field renders disabled, on writes 100 and enables it", async () => {
    const user = userEvent.setup();
    mount([step({ trigger_temps: { primary: 0, food: [] } })]);

    const primaryField = screen.getByLabelText(/^Primary trigger temperature/);
    expect(primaryField).toHaveValue(0);
    expect(primaryField).toBeDisabled();
    expect(screen.getByRole("button", { name: "Enable primary trigger" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    await user.click(screen.getByRole("button", { name: "Enable primary trigger" }));
    expect(screen.getByLabelText(/^Primary trigger temperature/)).toHaveValue(100);
    expect(screen.getByLabelText(/^Primary trigger temperature/)).not.toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Save step 0" }));
    await waitFor(() =>
      expect(updateStepMock).toHaveBeenCalledWith(
        "brisket.pfrecipe",
        0,
        expect.objectContaining({ trigger_temps: { primary: 100, food: [] } }),
      ),
    );

    // Flip it back off: writes 0 and disables again.
    await user.click(screen.getByRole("button", { name: "Enable primary trigger" }));
    expect(screen.getByLabelText(/^Primary trigger temperature/)).toHaveValue(0);
    expect(screen.getByLabelText(/^Primary trigger temperature/)).toBeDisabled();
  });

  it("SENTINEL: one food-probe trigger field per entry in trigger_temps.food, each independently gated", async () => {
    const user = userEvent.setup();
    mount([step({ trigger_temps: { primary: 0, food: [0, 165] } })]);

    expect(screen.getByLabelText(/^Food probe 1 trigger temperature/)).toBeDisabled();
    expect(screen.getByLabelText(/^Food probe 2 trigger temperature/)).not.toBeDisabled();
    expect(screen.getByLabelText(/^Food probe 2 trigger temperature/)).toHaveValue(165);

    await user.click(screen.getByRole("button", { name: "Enable food probe 1 trigger" }));
    expect(screen.getByLabelText(/^Food probe 1 trigger temperature/)).toHaveValue(100);

    await user.click(screen.getByRole("button", { name: "Save step 0" }));
    await waitFor(() =>
      expect(updateStepMock).toHaveBeenCalledWith(
        "brisket.pfrecipe",
        0,
        expect.objectContaining({ trigger_temps: { primary: 0, food: [100, 165] } }),
      ),
    );
  });

  it("SENTINEL: the timer switch off writes 0 minutes, on writes 1 minute, labelled in minutes", async () => {
    const user = userEvent.setup();
    mount([step({ timer: 0 })]);

    expect(screen.getByLabelText(/^Timer \(minutes\)/)).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Enable timer" }));
    expect(screen.getByLabelText(/^Timer \(minutes\)/)).toHaveValue(1);
    expect(screen.getByLabelText(/^Timer \(minutes\)/)).not.toBeDisabled();
  });

  it("hold temperature only shows for Hold mode", () => {
    const { rerender } = render(
      <StepsEditor
        file="f.pfrecipe"
        steps={[step({ mode: "Smoke" })]}
        units="F"
        onChanged={rs.fn()}
      />,
    );
    expect(screen.queryByLabelText(/^Hold temperature/)).toBeNull();

    rerender(
      <StepsEditor
        file="f.pfrecipe"
        steps={[step({ mode: "Hold", hold_temp: 225 })]}
        units="F"
        onChanged={rs.fn()}
      />,
    );
    expect(screen.getByLabelText(/^Hold temperature/)).toHaveValue(225);
  });

  it("bounds: max temperature is 600 for F and 300 for C", () => {
    const { rerender } = render(
      <StepsEditor
        file="f.pfrecipe"
        steps={[step({ mode: "Hold" })]}
        units="F"
        onChanged={rs.fn()}
      />,
    );
    expect(screen.getByLabelText(/^Hold temperature/)).toHaveAttribute("max", "600");

    rerender(
      <StepsEditor
        file="f.pfrecipe"
        steps={[step({ mode: "Hold" })]}
        units="C"
        onChanged={rs.fn()}
      />,
    );
    expect(screen.getByLabelText(/^Hold temperature/)).toHaveAttribute("max", "300");
  });

  it("the mode select only offers Smoke and Hold", () => {
    mount([step({ mode: "Smoke" })]);
    const select = screen.getByLabelText("Mode") as HTMLSelectElement;
    const options = within(select)
      .getAllByRole("option")
      .map((o) => (o as HTMLOptionElement).value);
    expect(options).toEqual(["Smoke", "Hold"]);
  });

  it("Startup and Shutdown steps render as a read-only card, not the edit form", () => {
    mount([step({ mode: "Startup" }), step({ mode: "Shutdown" })]);
    expect(screen.getByText("Step 0 -- Startup")).toBeInTheDocument();
    expect(screen.getByText("Step 1 -- Shutdown")).toBeInTheDocument();
    expect(screen.getByText(/Transitions once startup completes/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Mode")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save step 0" })).toBeNull();
    // Deleting a read-only step still works.
    expect(screen.getByRole("button", { name: "Delete step 0" })).toBeInTheDocument();
  });

  it("insert is positional: N steps offer N+1 insert points, each carrying its own index", async () => {
    const user = userEvent.setup();
    const { onChanged } = mount([step({ mode: "Smoke" }), step({ mode: "Hold" })]);

    expect(screen.getByRole("button", { name: "Insert a step above Step 0" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Insert a step above Step 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Insert a step at the end" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Insert a step above Step 1" }));
    await waitFor(() => expect(insertStepMock).toHaveBeenCalledWith("brisket.pfrecipe", 1));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("the empty-recipe insert point offers index 0", async () => {
    const user = userEvent.setup();
    mount([]);
    expect(screen.getByText("No program steps yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Insert a step" }));
    await waitFor(() => expect(insertStepMock).toHaveBeenCalledWith("brisket.pfrecipe", 0));
  });

  it("deleting a step confirms, then calls deleteStep with its index", async () => {
    const user = userEvent.setup();
    const { onChanged } = mount([step(), step()]);

    await user.click(screen.getByRole("button", { name: "Delete step 1" }));
    expect(deleteStepMock).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(deleteStepMock).toHaveBeenCalledWith("brisket.pfrecipe", 1));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("pause and notify are plain toggles, and message stays editable regardless of notify", async () => {
    const user = userEvent.setup();
    mount([step({ notify: false, message: "" })]);

    await user.type(screen.getByLabelText("Notification message"), "Wrap it up");
    await user.click(screen.getByRole("button", { name: "Send a notification" }));
    await user.click(screen.getByRole("button", { name: "Pause for input" }));
    await user.click(screen.getByRole("button", { name: "Save step 0" }));

    await waitFor(() =>
      expect(updateStepMock).toHaveBeenCalledWith(
        "brisket.pfrecipe",
        0,
        expect.objectContaining({ notify: true, pause: true, message: "Wrap it up" }),
      ),
    );
  });

  it("a rejected save surfaces the server's message", async () => {
    const user = userEvent.setup();
    updateStepMock.mockRejectedValue(new Error("bad_food_probes"));
    mount([step({ notify: true })]);

    await user.click(screen.getByRole("button", { name: "Send a notification" }));
    await user.click(screen.getByRole("button", { name: "Save step 0" }));

    expect(await screen.findByText("bad_food_probes")).toBeInTheDocument();
  });
});
