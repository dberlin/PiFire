import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LearningDialog } from "../../../../src/components/dashboard/learning/LearningDialog";

const DEFAULT_PROPS: React.ComponentProps<typeof LearningDialog> = {
  controllerLabel: "PID-SP",
  title: "PID-SP adaptation evidence",
  closeLabel: "Close PID-SP adaptation evidence",
  status: "ready-for-review",
  currentMode: "Hold",
  displayMode: "Hold",
  criticalError: false,
  loading: false,
  loadingLabel: "Loading PID-SP evidence…",
  error: null,
  retryLabel: "Retry PID-SP evidence",
  onRetry: () => undefined,
  children: (
    <>
      <p>Retained prior estimate</p>
      <button type="button">Inspect estimate</button>
    </>
  ),
};

afterEach(() => cleanup());

function renderDialog(props: Partial<React.ComponentProps<typeof LearningDialog>> = {}) {
  return render(<LearningDialog {...DEFAULT_PROPS} {...props} />);
}

async function openDialog() {
  const trigger = screen.getByRole("button", {
    name: "PID-SP learning: ready for review",
  });
  await userEvent.click(trigger);
  const dialog = screen.getByRole("dialog", {
    name: "PID-SP adaptation evidence",
  });
  return { dialog, trigger };
}

describe("LearningDialog", () => {
  it("renders the supplied controller label and normalized status in its pill", () => {
    renderDialog({ status: "  READY_for-review  " });

    expect(screen.getByRole("button", { name: "PID-SP learning: ready for review" })).toBeVisible();
  });

  it.each([
    {
      caseName: "stopped collection",
      status: "collecting",
      currentMode: "Stop",
      displayMode: "Stop",
      expected: "PID-SP learning: idle",
    },
    {
      caseName: "recipe-driven Hold collection",
      status: "collecting",
      currentMode: "Recipe",
      displayMode: "Hold",
      expected: "PID-SP learning: collecting",
    },
    {
      caseName: "grill error",
      status: "collecting",
      currentMode: "Error",
      displayMode: "Error",
      expected: "PID-SP learning: error",
    },
    {
      caseName: "critical error while stopped",
      status: "collecting",
      currentMode: "Stop",
      displayMode: "Stop",
      criticalError: true,
      expected: "PID-SP learning: error",
    },
    {
      caseName: "report error while stopped",
      status: "error",
      currentMode: "Stop",
      displayMode: "Stop",
      expected: "PID-SP learning: error",
    },
  ])(
    "projects $caseName into the pill as $expected",
    ({ status, currentMode, displayMode, criticalError = false, expected }) => {
      renderDialog({ status, currentMode, displayMode, criticalError });

      expect(screen.getByRole("button", { name: expected })).toBeVisible();
    },
  );

  it("moves focus to Close when opened", async () => {
    renderDialog();

    await openDialog();

    expect(screen.getByRole("button", { name: "Close PID-SP adaptation evidence" })).toHaveFocus();
  });

  it("closes on Escape and restores focus to its trigger", async () => {
    renderDialog();
    const { trigger } = await openDialog();

    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("closes from the scrim but not from a content click", async () => {
    renderDialog();
    const { dialog } = await openDialog();
    const scrim = dialog.parentElement;
    expect(scrim).not.toBeNull();

    fireEvent.click(dialog);
    expect(screen.getByRole("dialog")).toBeVisible();

    fireEvent.click(scrim!);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("wraps Tab and Shift+Tab within the dialog", async () => {
    renderDialog();
    await openDialog();
    const close = screen.getByRole("button", {
      name: "Close PID-SP adaptation evidence",
    });
    const inspect = screen.getByRole("button", { name: "Inspect estimate" });

    await userEvent.tab({ shift: true });
    expect(inspect).toHaveFocus();

    await userEvent.tab();
    expect(close).toHaveFocus();
  });

  it("includes native selects and textareas in forward and reverse focus traversal", async () => {
    renderDialog({
      children: (
        <>
          <label htmlFor="learning-strategy">Learning strategy</label>
          <select id="learning-strategy" defaultValue="balanced">
            <option value="balanced">Balanced</option>
          </select>
          <textarea aria-label="Operator note" />
        </>
      ),
    });
    await openDialog();
    const close = screen.getByRole("button", {
      name: "Close PID-SP adaptation evidence",
    });
    const strategy = screen.getByRole("combobox", {
      name: "Learning strategy",
    });
    const note = screen.getByRole("textbox", { name: "Operator note" });

    await userEvent.tab();
    expect(strategy).toHaveFocus();
    await userEvent.tab();
    expect(note).toHaveFocus();
    await userEvent.tab();
    expect(close).toHaveFocus();

    await userEvent.tab({ shift: true });
    expect(note).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(strategy).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(close).toHaveFocus();
  });

  it("marks loading as busy without removing prior content", async () => {
    renderDialog({ loading: true });
    const trigger = screen.getByRole("button", {
      name: "PID-SP learning: ready for review",
    });
    expect(trigger).toHaveAttribute("aria-busy", "true");

    await userEvent.click(trigger);
    const dialog = screen.getByRole("dialog", {
      name: "PID-SP adaptation evidence",
    });

    expect(dialog).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Loading PID-SP evidence…");
    expect(screen.getByText("Retained prior estimate")).toBeVisible();
  });

  it("renders errors as alerts and invokes the supplied retry callback", async () => {
    const onRetry = rs.fn();
    renderDialog({ error: "PID-SP evidence unavailable", onRetry });
    await openDialog();

    expect(screen.getByRole("alert")).toHaveTextContent("PID-SP evidence unavailable");
    expect(screen.getByText("Retained prior estimate")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Retry PID-SP evidence" }));

    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("takes the dialog title and Close accessible name from controller props", async () => {
    renderDialog();
    await userEvent.click(
      screen.getByRole("button", { name: "PID-SP learning: ready for review" }),
    );

    expect(screen.getByRole("dialog", { name: "PID-SP adaptation evidence" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Close PID-SP adaptation evidence" })).toBeVisible();
    expect(screen.queryByText(/MPC model learning/i)).not.toBeInTheDocument();
  });
});
