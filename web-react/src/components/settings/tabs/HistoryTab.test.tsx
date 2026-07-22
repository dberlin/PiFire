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

const PROBE_CONFIG = {
  Grill: {
    name: "Grill",
    type: "Primary",
    enabled: true,
    line_color: "rgb(0, 64, 255, 1)",
    bg_color: "rgb(0, 64, 255, 1)",
    line_color_setpoint: "rgb(0, 64, 255, 1)",
    bg_color_setpoint: "rgb(0, 64, 255, 1)",
    line_color_target: "rgb(0, 128, 255, 1)",
    bg_color_target: "rgb(0, 128, 255, 1)",
    dash_setpoint: true,
    fill: false,
  },
  Probe1: {
    name: "Probe-1",
    type: "Food",
    enabled: true,
    line_color: "rgb(0, 200, 64, 1)",
    bg_color: "rgb(0, 200, 64, 1)",
    line_color_target: "rgb(0, 232, 126, 1)",
    bg_color_target: "rgb(0, 232, 126, 1)",
    dash_setpoint: true,
    fill: false,
  },
};

function contextWithProbeConfig(probe_config: unknown) {
  return {
    settings: {
      history_page: {
        minutes: 240,
        datapoints: 100,
        clearhistoryonstart: false,
        autorefresh: "off",
        probe_config,
      },
      globals: {
        ext_data: false,
      },
    },
    mode: "Stop",
  };
}

describe("HistoryTab — Chart Colors", () => {
  it("renders one card per label showing name and type chip", () => {
    renderRoute(<HistoryTab />, contextWithProbeConfig(PROBE_CONFIG));

    expect(screen.getByText("Grill")).toBeInTheDocument();
    expect(screen.getByText("Primary")).toBeInTheDocument();
    expect(screen.getByText("Probe-1")).toBeInTheDocument();
    expect(screen.getByText("Food")).toBeInTheDocument();
  });

  it("Grill shows setpoint ColorFields, Probe-1 does not", () => {
    renderRoute(<HistoryTab />, contextWithProbeConfig(PROBE_CONFIG));

    // Setpoint fields exist only for Grill (Primary).
    expect(screen.getAllByLabelText("Grill Line Color (Setpoint)")).toHaveLength(1);
    expect(screen.getAllByLabelText("Grill Background Color (Setpoint)")).toHaveLength(1);
    expect(screen.queryByLabelText("Probe1 Line Color (Setpoint)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Probe1 Background Color (Setpoint)")).not.toBeInTheDocument();

    // Both probes have the always-present line/bg/target color fields.
    expect(screen.getByLabelText("Grill Line Color")).toBeInTheDocument();
    expect(screen.getByLabelText("Probe1 Line Color")).toBeInTheDocument();
    expect(screen.getByLabelText("Grill Line Color (Target)")).toBeInTheDocument();
    expect(screen.getByLabelText("Probe1 Line Color (Target)")).toBeInTheDocument();
  });

  it("changing Grill's line color and saving includes the new color plus the untouched Probe1 subtree, flags []", async () => {
    renderRoute(<HistoryTab />, contextWithProbeConfig(PROBE_CONFIG));

    const lineColorInput = screen.getByLabelText("Grill Line Color");
    fireEvent.change(lineColorInput, { target: { value: "#ff0000" } });

    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        history_page: expect.objectContaining({
          probe_config: expect.objectContaining({
            Grill: expect.objectContaining({ line_color: "rgb(255, 0, 0, 1)" }),
            Probe1: PROBE_CONFIG.Probe1,
          }),
        }),
      }),
      [],
    );
  });

  it("toggling Fill for a probe lands in the save delta", async () => {
    renderRoute(<HistoryTab />, contextWithProbeConfig(PROBE_CONFIG));

    const fillToggle = screen.getByRole("button", { name: "Grill Fill" });
    fireEvent.click(fillToggle);

    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        history_page: expect.objectContaining({
          probe_config: expect.objectContaining({
            Grill: expect.objectContaining({ fill: true }),
          }),
        }),
      }),
      [],
    );
  });

  it("renders the no-probes hint and does not crash when probe_config is empty", () => {
    renderRoute(<HistoryTab />, contextWithProbeConfig({}));

    expect(screen.getByText("No probes configured.")).toBeInTheDocument();
  });
});
