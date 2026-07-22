import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { renderRoute } from "../../../test-utils";
import { HistoryTab } from "./HistoryTab";

const saveMock = rs.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
rs.mock("../../../helpers/settings/useSaveSettings", () => ({
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

describe("HistoryTab", () => {
  it("renders history fields with loaded values when mode is Stop", () => {
    const context = {
      settings: {
        history_page: {
          minutes: 120,
          datapoints: 50,
          clearhistoryonstart: true,
          autorefresh: "on",
        },
        globals: {
          ext_data: true,
        },
      },
      mode: "Stop",
    };

    renderRoute(<HistoryTab />, context);

    // Check that fields display the loaded values
    expect(screen.getByDisplayValue("120")).toBeInTheDocument();
    expect(screen.getByDisplayValue("50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear History on Start" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Auto Refresh" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Extended Data Logging" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("ext_data toggle is enabled when mode is Stop and saving includes globals.ext_data", async () => {
    const context = {
      settings: {
        history_page: {
          minutes: 240,
          datapoints: 100,
          clearhistoryonstart: false,
          autorefresh: "off",
        },
        globals: {
          ext_data: false,
        },
      },
      mode: "Stop",
    };

    renderRoute(<HistoryTab />, context);

    const extDataToggle = screen.getByRole("button", { name: "Extended Data Logging" });
    expect(extDataToggle).not.toBeDisabled();

    // Toggle ext_data and save
    fireEvent.click(extDataToggle);
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        globals: expect.objectContaining({
          ext_data: true,
        }),
      }),
      [],
    );
  });

  it("ext_data toggle is disabled when mode is not Stop", () => {
    const context = {
      settings: {
        history_page: {
          minutes: 240,
          datapoints: 100,
          clearhistoryonstart: false,
          autorefresh: "off",
        },
        globals: {
          ext_data: false,
        },
      },
      mode: "Hold",
    };

    renderRoute(<HistoryTab />, context);

    const extDataToggle = screen.getByRole("button", { name: "Extended Data Logging" });
    expect(extDataToggle).toBeDisabled();
  });

  it("autorefresh toggle persists string on/off in the delta", async () => {
    const context = {
      settings: {
        history_page: {
          minutes: 240,
          datapoints: 100,
          clearhistoryonstart: false,
          autorefresh: "off",
        },
        globals: {
          ext_data: false,
        },
      },
      mode: "Stop",
    };

    renderRoute(<HistoryTab />, context);

    // Toggle autorefresh from off to on
    const autorefreshToggle = screen.getByRole("button", { name: "Auto Refresh" });
    fireEvent.click(autorefreshToggle);

    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        history_page: expect.objectContaining({
          autorefresh: "on",
        }),
      }),
      [],
    );
  });
});
