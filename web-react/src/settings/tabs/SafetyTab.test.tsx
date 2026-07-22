import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { renderRoute } from "../../test-utils";
import { SafetyTab } from "./SafetyTab";

const saveMock = rs.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
rs.mock("../useSaveSettings", () => ({
  useSaveSettings: () => ({
    save: saveMock,
    saving: false,
    baseUrl: "",
  }),
}));

beforeEach(() => {
  saveMock.mockClear();
});

afterEach(cleanup);

describe("SafetyTab", () => {
  it("renders safety fields with loaded values", () => {
    const context = {
      settings: {
        safety: {
          minstartuptemp: 80,
          maxstartuptemp: 110,
          maxtemp: 550,
          reigniteretries: 2,
          startup_check: true,
          allow_manual_changes: false,
          manual_override_time: 45,
        },
      },
      mode: "Stop",
    };

    renderRoute(<SafetyTab />, context);

    // Check that fields display the loaded values
    expect(screen.getByDisplayValue("80")).toBeInTheDocument();
    expect(screen.getByDisplayValue("110")).toBeInTheDocument();
    expect(screen.getByDisplayValue("550")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2")).toBeInTheDocument();
    expect(screen.getByDisplayValue("45")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Startup Check" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Allow Manual Output Changes" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("saves settings with empty flags when Save is clicked", async () => {
    const context = {
      settings: {
        safety: {
          minstartuptemp: 75,
          maxstartuptemp: 100,
          maxtemp: 550,
          reigniteretries: 1,
          startup_check: false,
          allow_manual_changes: false,
          manual_override_time: 30,
        },
      },
      mode: "Stop",
    };

    renderRoute(<SafetyTab />, context);

    // Change Max Grill Temp from 550 to 600
    const maxTempInput = screen.getByDisplayValue("550");
    fireEvent.change(maxTempInput, { target: { value: "600" } });

    // Click Save
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    // Wait for async save to complete and assert spy was called with correct delta and empty flags
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        safety: expect.objectContaining({
          maxtemp: 600,
        }),
      }),
      [],
    );
  });
});
