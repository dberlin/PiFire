import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { HistoryTab } from "../../../../../src/components/settings/tabs/HistoryTab";
import { renderRoute } from "../../../test-utils";

const saveMock = rs.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
rs.mock("../../../../../src/helpers/settings/useSaveSettings", () => ({
  useSaveSettings: () => ({
    save: saveMock,
    saving: false,
    status: { kind: "idle" } as const,
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
    expect(screen.getByText(/stop the grill to change/i)).toBeInTheDocument();
  });

  it("explains an unknown grill mode instead of telling you to stop the grill", () => {
    // getMode() returns "" when it cannot read the mode. That still gates the
    // toggle (fail closed -- changing extended data mid-cook changes the
    // history schema), but "stop the grill" would be a misleading reason: the
    // grill may already be stopped and we simply could not confirm it.
    const context = {
      settings: {
        history_page: {
          minutes: 240,
          datapoints: 100,
          clearhistoryonstart: false,
          autorefresh: "on",
        },
        globals: { ext_data: false },
      },
      mode: "",
    };

    renderRoute(<HistoryTab />, context);

    expect(screen.getByRole("button", { name: "Extended Data Logging" })).toBeDisabled();
    expect(screen.getByText(/can't confirm the grill is stopped/i)).toBeInTheDocument();
    expect(screen.queryByText(/stop the grill to change/i)).not.toBeInTheDocument();
  });

  it("labels datapoints as a downsampling threshold, not a point count", async () => {
    // history_page.datapoints is the size at which downsampling STARTS (see
    // file_mgmt/downsample.py select_indices: every index is returned when the
    // window holds min_points or fewer). Labelling it "Data Points" read as
    // "how many points to draw", which is the opposite of what raising it does.
    renderRoute(<HistoryTab />, {
      settings: {
        history_page: { minutes: 240, datapoints: 5000, fidelity_degrees: 1.5 },
        globals: {},
      },
      mode: "Stop",
    });

    const field = screen.getByLabelText(/downsample above/i);
    expect(field).toHaveValue(5000);
    expect(field).toHaveAttribute("min", "2"); // a threshold of 0 or 1 draws nothing
    expect(screen.queryByLabelText("Data Points")).not.toBeInTheDocument();
    expect(screen.getByText(/or fewer are drawn from every sample/i)).toBeInTheDocument();
  });

  it("falls back to the real defaults (10000 samples, 2 degrees) when the keys are absent", async () => {
    // A missing key means the server is using common/defaults.py's value, so
    // showing anything else would make the next save silently rewrite it.
    renderRoute(<HistoryTab />, {
      settings: { history_page: { minutes: 240 }, globals: {} },
      mode: "Stop",
    });

    expect(screen.getByLabelText(/downsample above/i)).toHaveValue(10000);
    expect(screen.getByLabelText(/chart fidelity/i)).toHaveValue(2);
  });

  it("exposes fidelity_degrees and includes it in the save delta", async () => {
    renderRoute(<HistoryTab />, {
      settings: {
        history_page: { minutes: 240, datapoints: 10000, fidelity_degrees: 2.0 },
        globals: {},
      },
      mode: "Stop",
    });

    const fidelity = screen.getByLabelText(/chart fidelity/i);
    expect(fidelity).toHaveValue(2);
    expect(screen.getByText("degrees")).toBeInTheDocument();
    expect(screen.getByText(/deviate from the real reading/i)).toBeInTheDocument();

    fireEvent.change(fidelity, { target: { value: "0.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        history_page: expect.objectContaining({ fidelity_degrees: 0.5, datapoints: 10000 }),
      }),
      [],
    );
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

  // The schema declares the setpoint colors as `string | null` defaulting to
  // null, while common/defaults.py omits the keys outright for Food probes.
  // Both spellings mean "not applicable to this probe", so an explicit null
  // must render nothing rather than an empty color input.
  it("treats a null setpoint color the same as an absent one", () => {
    renderRoute(
      <HistoryTab />,
      contextWithProbeConfig({
        Probe1: {
          ...PROBE_CONFIG.Probe1,
          line_color_setpoint: null,
          bg_color_setpoint: null,
        },
      }),
    );

    expect(screen.queryByLabelText("Probe-1 Line Color (Setpoint)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Probe-1 Background Color (Setpoint)")).not.toBeInTheDocument();
    // The non-nullable colors still render, so this is not a blanket blank-out.
    expect(screen.getByLabelText("Probe-1 Line Color")).toBeInTheDocument();
  });

  it("Grill shows setpoint ColorFields, Probe-1 does not", () => {
    renderRoute(<HistoryTab />, contextWithProbeConfig(PROBE_CONFIG));

    // Setpoint fields exist only for Grill (Primary).
    expect(screen.getAllByLabelText("Grill Line Color (Setpoint)")).toHaveLength(1);
    expect(screen.getAllByLabelText("Grill Background Color (Setpoint)")).toHaveLength(1);
    expect(screen.queryByLabelText("Probe-1 Line Color (Setpoint)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Probe-1 Background Color (Setpoint)")).not.toBeInTheDocument();

    // Both probes have the always-present line/bg/target color fields, labeled
    // with the probe's display name (entry.name), not the probeConfig dict key.
    expect(screen.getByLabelText("Grill Line Color")).toBeInTheDocument();
    expect(screen.getByLabelText("Probe-1 Line Color")).toBeInTheDocument();
    expect(screen.getByLabelText("Grill Line Color (Target)")).toBeInTheDocument();
    expect(screen.getByLabelText("Probe-1 Line Color (Target)")).toBeInTheDocument();
    // The dict key is no longer used as the label prefix when a unique name exists.
    expect(screen.queryByLabelText("Probe1 Line Color")).not.toBeInTheDocument();
  });

  it("falls back to the dict key as the label prefix when name is missing or duplicated", () => {
    const duplicateNameConfig = {
      ProbeA: { ...PROBE_CONFIG.Probe1, name: "Same Name" },
      ProbeB: { ...PROBE_CONFIG.Probe1, name: "Same Name" },
      ProbeC: { ...PROBE_CONFIG.Probe1, name: "" },
    };

    renderRoute(<HistoryTab />, contextWithProbeConfig(duplicateNameConfig));

    // Duplicate names fall back to the dict key for both entries sharing it.
    expect(screen.getByLabelText("ProbeA Line Color")).toBeInTheDocument();
    expect(screen.getByLabelText("ProbeB Line Color")).toBeInTheDocument();
    // A falsy name also falls back to the dict key.
    expect(screen.getByLabelText("ProbeC Line Color")).toBeInTheDocument();
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
