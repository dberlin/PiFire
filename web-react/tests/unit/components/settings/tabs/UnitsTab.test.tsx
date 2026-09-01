import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

import { renderRoute } from "../../../test-utils";

const setUnitsMock = rs.fn();

// createCommand is a *direct* dependency of UnitsTab.tsx (not transitive
// through another mocked module), so a static `import { UnitsTab } from
// "./UnitsTab"` above this mock hits the rstest hoisting TDZ — import the
// module under test dynamically after the mock is registered instead.
rs.mock("@pifire/core/command", () => ({
  createCommand: () => ({
    setMode: rs.fn(),
    hold: rs.fn(),
    setSmokePlus: rs.fn(),
    setPMode: rs.fn(),
    prime: rs.fn(),
    timerStart: rs.fn(),
    timerStartWithOptions: rs.fn(),
    timerPause: rs.fn(),
    timerStop: rs.fn(),
    system: rs.fn(),
    setUnits: setUnitsMock,
  }),
}));

const { UnitsTab } = await import("../../../../../src/components/settings/tabs/UnitsTab");

beforeEach(() => {
  setUnitsMock.mockClear();
});

afterEach(cleanup);

describe("UnitsTab", () => {
  it("renders the current unit", () => {
    renderRoute(<UnitsTab />, { settings: { globals: { units: "F" } }, mode: "Stop" });

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("F");
  });

  it("opens the confirm modal, mentioning stopping the grill, when the other unit is selected", () => {
    renderRoute(<UnitsTab />, { settings: { globals: { units: "F" } }, mode: "Stop" });

    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "C" } });

    expect(screen.getByText(/stop the grill/i)).toBeInTheDocument();
  });

  it("on confirm, calls setUnits with the target unit and updates the displayed unit when ok", async () => {
    setUnitsMock.mockResolvedValueOnce({ ok: true, message: "" });

    renderRoute(<UnitsTab />, { settings: { globals: { units: "F" } }, mode: "Stop" });

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "C" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    // Wait on the RENDER, not the call. setUnits is invoked synchronously from
    // the click handler, so a waitFor on the mock can be satisfied on its first
    // check -- before the promise continuation that sets the new unit has run.
    // Waiting on the displayed value covers both.
    await waitFor(() =>
      expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("C"),
    );
    expect(setUnitsMock).toHaveBeenCalledWith("C");
    expect(screen.queryByText(/Switch to/)).not.toBeInTheDocument(); // modal closed
  });

  it("on confirm failure, shows the error and does NOT change the displayed unit", async () => {
    setUnitsMock.mockResolvedValueOnce({ ok: false, message: "grill is busy" });

    renderRoute(<UnitsTab />, { settings: { globals: { units: "F" } }, mode: "Stop" });

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "C" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    // Same as above: wait on the error the continuation renders, not on the
    // synchronous call, or this asserts one render too early. Observed failing
    // intermittently under full-suite load before the change.
    await waitFor(() => expect(screen.getByText("grill is busy")).toBeInTheDocument());
    expect(setUnitsMock).toHaveBeenCalledWith("C");
    // displayed unit must NOT have switched to C on failure
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("F");
  });

  it("cancelling the confirm modal leaves the unit unchanged and calls no command", () => {
    renderRoute(<UnitsTab />, { settings: { globals: { units: "F" } }, mode: "Stop" });

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "C" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(setUnitsMock).not.toHaveBeenCalled();
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("F");
    expect(screen.queryByText(/Switch to/)).not.toBeInTheDocument();
  });
});
