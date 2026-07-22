// @vitest-environment jsdom

import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderRoute } from "../../test-utils";
import { PelletsTab } from "./PelletsTab";

const saveMock = vi.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
vi.mock("../useSaveSettings", () => ({
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

    // Wait for async save to complete and assert spy was called with correct flags
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        pelletlevel: expect.objectContaining({
          warning_time: 20,
        }),
      }),
      ["settings_update"],
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

    // Wait for async save to complete and assert spy was called with correct flags
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        pelletlevel: expect.objectContaining({
          empty: 10,
        }),
      }),
      ["settings_update", "distance_update"],
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

    // Wait for async save to complete and assert spy was called with correct flags
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        pelletlevel: expect.objectContaining({
          full: 85,
        }),
      }),
      ["settings_update", "distance_update"],
    );
  });
});
