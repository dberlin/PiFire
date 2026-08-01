import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FileRequestError } from "../../../../src/helpers/files/apiEnvelope";
import type { RecipeRunView } from "../../../../src/helpers/recipes/runStatus";

// SAFETY: runRecipe starts a real cook. It is stubbed at the module boundary
// so no test can ever reach the real endpoint.
const runRecipeMock = rs.fn();
rs.mock("../../../../src/helpers/files/recipeApi", () => ({
  runRecipe: (...a: unknown[]) => runRecipeMock(...a),
}));

const { RecipeRunStatus } = await import("../../../../src/components/recipes/RecipeRunStatus");

const STOPPED: RecipeRunView = {
  active: false,
  otherFilename: null,
  paused: false,
  step: 0,
  canRun: true,
};

beforeEach(() => {
  runRecipeMock.mockReset();
  runRecipeMock.mockResolvedValue({ filename: "f.pfrecipe" });
});

afterEach(cleanup);

describe("RecipeRunStatus", () => {
  it("disables Run unless the grill is stopped", () => {
    render(<RecipeRunStatus filename="f.pfrecipe" status={{ ...STOPPED, canRun: false }} />);
    expect(screen.getByRole("button", { name: "Run this recipe" })).toBeDisabled();
  });

  it("enables Run when recipeStatus.mode is Stop", () => {
    render(<RecipeRunStatus filename="f.pfrecipe" status={STOPPED} />);
    expect(screen.getByRole("button", { name: "Run this recipe" })).not.toBeDisabled();
  });

  it("goes through ConfirmAction before calling runRecipe -- clicking Run alone must not start a cook", () => {
    render(<RecipeRunStatus filename="f.pfrecipe" status={STOPPED} />);
    fireEvent.click(screen.getByRole("button", { name: "Run this recipe" }));
    expect(runRecipeMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(runRecipeMock).toHaveBeenCalledWith("f.pfrecipe");
  });

  it("cancelling the confirm modal never calls runRecipe", () => {
    render(<RecipeRunStatus filename="f.pfrecipe" status={STOPPED} />);
    fireEvent.click(screen.getByRole("button", { name: "Run this recipe" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(runRecipeMock).not.toHaveBeenCalled();
  });

  it("surfaces a 409 not_stopped message if the grill state raced ahead of the click", async () => {
    runRecipeMock.mockRejectedValue(
      new FileRequestError({ status: 409, message: "not_stopped", errortype: null }),
    );
    render(<RecipeRunStatus filename="f.pfrecipe" status={STOPPED} />);
    fireEvent.click(screen.getByRole("button", { name: "Run this recipe" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(screen.getByText("not_stopped")).toBeInTheDocument());
  });

  it("shows which recipe is running when a DIFFERENT recipe is active (G9)", () => {
    render(
      <RecipeRunStatus
        filename="brisket.pfrecipe"
        status={{ ...STOPPED, canRun: false, otherFilename: "ribs.pfrecipe" }}
      />,
    );
    expect(screen.getByText(/ribs\.pfrecipe is currently running/)).toBeInTheDocument();
  });

  it("shows the active step and a paused indicator when THIS recipe is running", () => {
    render(
      <RecipeRunStatus
        filename="brisket.pfrecipe"
        status={{ ...STOPPED, canRun: false, active: true, step: 2, paused: true }}
      />,
    );
    expect(screen.getByText(/on step 2/)).toBeInTheDocument();
    expect(screen.getByText("Paused.")).toBeInTheDocument();
  });
});
