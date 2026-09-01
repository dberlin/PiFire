import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

import { PelletsTab } from "../../../../../src/components/settings/tabs/PelletsTab";
import { renderRoute } from "../../../test-utils";

const saveMock = rs.fn().mockResolvedValue(true);
const useSaveSettingsMock = rs.fn();

// Mock the useSaveSettings module
rs.mock("../../../../../src/helpers/settings/useSaveSettings", () => ({
  useSaveSettings: () => useSaveSettingsMock(),
}));

beforeEach(() => {
  saveMock.mockClear();
  useSaveSettingsMock.mockReset().mockReturnValue({
    save: saveMock,
    saving: false,
    status: { kind: "idle" } as const,
    errors: [],
    baseUrl: "",
  });
});

afterEach(cleanup);

// Field associates its <label> to the control via htmlFor/id, so
// getByLabelText(field) resolves the input directly.
function inputFor(label: string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement;
}

describe("PelletsTab", () => {
  it("renders pellets fields with loaded values", () => {
    const context = {
      settings: {
        pelletlevel: {
          warning_enabled: true,
          warning_time: 15,
          warning_level: 25,
          empty: 5,
          full: 90,
        },
        globals: {
          augerrate: 15.5,
          prime_ignition: false,
        },
      },
      mode: "Stop",
    };

    renderRoute(<PelletsTab />, context);

    // Check that fields display the loaded values
    expect(screen.getByDisplayValue("15")).toBeInTheDocument();
    expect(screen.getByDisplayValue("25")).toBeInTheDocument();
    expect(screen.getByDisplayValue("5")).toBeInTheDocument();
    expect(screen.getByDisplayValue("90")).toBeInTheDocument();
    expect(screen.getByDisplayValue("15.5")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Warning Enabled" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Prime Ignition" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("saves with settings_update flag when warning_time changes (no distance_update)", async () => {
    const context = {
      settings: {
        pelletlevel: {
          warning_enabled: true,
          warning_time: 15,
          warning_level: 25,
          empty: 5,
          full: 90,
        },
        globals: {
          augerrate: 15.5,
          prime_ignition: false,
        },
      },
      mode: "Stop",
    };

    renderRoute(<PelletsTab />, context);

    // Change warning_time from 15 to 20
    const warningTimeInput = screen.getByDisplayValue("15");
    fireEvent.change(warningTimeInput, { target: { value: "20" } });

    // Click Save
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    // Wait for the save call carrying the changed warning_time.
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        expect.objectContaining({
          pelletlevel: expect.objectContaining({
            warning_time: 20,
          }),
        }),
        ["settings_update"],
      ),
    );
  });

  it("saves with settings_update and distance_update flags when empty changes", async () => {
    const context = {
      settings: {
        pelletlevel: {
          warning_enabled: true,
          warning_time: 15,
          warning_level: 25,
          empty: 5,
          full: 90,
        },
        globals: {
          augerrate: 15.5,
          prime_ignition: false,
        },
      },
      mode: "Stop",
    };

    renderRoute(<PelletsTab />, context);

    // Change empty from 5 to 10
    const emptyInput = screen.getByDisplayValue("5");
    fireEvent.change(emptyInput, { target: { value: "10" } });

    // Click Save
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    // Wait for the save call carrying the changed empty level.
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        expect.objectContaining({
          pelletlevel: expect.objectContaining({
            empty: 10,
          }),
        }),
        ["settings_update", "distance_update"],
      ),
    );
  });

  it("saves with settings_update and distance_update flags when full changes", async () => {
    const context = {
      settings: {
        pelletlevel: {
          warning_enabled: true,
          warning_time: 15,
          warning_level: 25,
          empty: 5,
          full: 90,
        },
        globals: {
          augerrate: 15.5,
          prime_ignition: false,
        },
      },
      mode: "Stop",
    };

    renderRoute(<PelletsTab />, context);

    // Change full from 90 to 85
    const fullInput = screen.getByDisplayValue("90");
    fireEvent.change(fullInput, { target: { value: "85" } });

    // Click Save
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    // Wait for the save call carrying the changed full level.
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        expect.objectContaining({
          pelletlevel: expect.objectContaining({
            full: 85,
          }),
        }),
        ["settings_update", "distance_update"],
      ),
    );
  });
  describe("input bounds and prime-ignition danger copy", () => {
    const fixture = { settings: {}, mode: "Stop" };

    it("bounds warning_time 5-240 (index.html:1325)", () => {
      renderRoute(<PelletsTab />, fixture);
      const el = inputFor("Warning Time");
      expect(el).toHaveAttribute("min", "5");
      expect(el).toHaveAttribute("max", "240");
    });

    it("bounds empty 1-100 and full 0-100 (index.html:1354, 1362)", () => {
      renderRoute(<PelletsTab />, fixture);
      expect(inputFor("Empty")).toHaveAttribute("min", "1");
      expect(inputFor("Empty")).toHaveAttribute("max", "100");
      expect(inputFor("Full")).toHaveAttribute("min", "0");
      expect(inputFor("Full")).toHaveAttribute("max", "100");
    });

    it("leaves warning_level at its already-correct 0-100", () => {
      renderRoute(<PelletsTab />, fixture);
      expect(inputFor("Warning Level")).toHaveAttribute("min", "0");
      expect(inputFor("Warning Level")).toHaveAttribute("max", "100");
    });

    it("clamps warning_time on blur", () => {
      renderRoute(<PelletsTab />, fixture);
      const el = inputFor("Warning Time");
      fireEvent.change(el, { target: { value: "9999" } });
      fireEvent.blur(el);
      expect(inputFor("Warning Time")).toHaveValue(240);
    });

    // Safety copy on a control that lights a fire, dropped in the port
    // (index.html:1403-1412).
    it("renders the DANGER copy under the Prime Ignition toggle", () => {
      renderRoute(<PelletsTab />, fixture);
      const danger = screen.getByText(/DANGER/);
      expect(danger).toBeInTheDocument();
      expect(screen.getByText(/ignite pellets and start the firepot/i)).toBeInTheDocument();
    });
  });

  describe("per-field save errors", () => {
    const fixture = { settings: {}, mode: "Stop" };

    // Proves the field's `path` prop is load-bearing: it is what lets
    // Warning Time claim "pelletlevel.warning_time" and show the backend's
    // rejection inline instead of it falling to the save bar's generic
    // prefixed line.
    it("puts the backend's rejection on the field that caused it", () => {
      useSaveSettingsMock.mockReturnValue({
        save: saveMock,
        saving: false,
        status: { kind: "error", message: "Some settings were refused" },
        errors: [
          {
            path: "pelletlevel.warning_time",
            message: "Input should be less than or equal to 240",
          },
        ],
        baseUrl: "",
      });

      renderRoute(<PelletsTab />, fixture);

      const alerts = screen.getAllByRole("alert").map((n) => n.textContent);
      expect(alerts).toContain("Input should be less than or equal to 240");
      expect(alerts).not.toContain(
        "pelletlevel.warning_time: Input should be less than or equal to 240",
      );
    });
  });
});
