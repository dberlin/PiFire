import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import type { ControllerMetadata } from "../../../helpers/settings/settingsApi";
import { renderRoute } from "../../../test-utils";
import { ControllerTab } from "./ControllerTab";

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

const controllerMeta: ControllerMetadata = {
  metadata: {
    pid: {
      friendly_name: "PID Standard",
      description: "The standard PID controller for PiFire.",
      config: [
        {
          option_name: "PB",
          option_friendly_name: "Proportional Band(PB)",
          option_description: "Proportional band.",
          option_type: "float",
          option_default: 60.0,
          option_min: null,
          option_max: null,
        },
        {
          option_name: "Td",
          option_friendly_name: "Derivative Time (Td)",
          option_description: "Derivative time.",
          option_type: "float",
          option_default: 45.0,
          option_min: null,
          option_max: null,
        },
        {
          option_name: "Ti",
          option_friendly_name: "Integral Time (Ti)",
          option_description: "Integral time.",
          option_type: "float",
          option_default: 180.0,
          option_min: null,
          option_max: null,
        },
        {
          option_name: "center",
          option_friendly_name: "Center Ratio",
          option_description: "Center of cycle ratio.",
          option_type: "float",
          option_default: 0.5,
          option_min: null,
          option_max: null,
        },
      ],
    },
    fuzzy: {
      friendly_name: "Fuzzy Logic Controller",
      description: "Experimental fuzzy logic controller.",
      config: [],
    },
    pid_parallel: {
      friendly_name: "Parallel PID w/ optional Integrator Clamping",
      description: "PID in parallel form with optional integral anti-windup protection.",
      config: [
        {
          option_name: "Clamping",
          option_friendly_name: "Integral Windup Protection",
          option_description: "Stops integration when output limits are exceeded.",
          option_type: "bool",
          option_default: true,
          option_min: null,
          option_max: null,
        },
      ],
    },
  },
};

function makeContext(overrides: Partial<{ controllerMeta: ControllerMetadata | null }> = {}) {
  return {
    settings: { controller: { selected: "pid", config: { pid: { PB: 55 } } } },
    mode: "Stop",
    controllerMeta,
    ...overrides,
  };
}

describe("ControllerTab", () => {
  it("renders the Select with the selected controller and shows config value + fallback defaults", () => {
    renderRoute(<ControllerTab />, makeContext());

    expect(screen.getByRole("combobox")).toHaveValue("pid");
    expect(screen.getByText("PID Standard")).toBeInTheDocument();
    expect(screen.getByDisplayValue("55")).toBeInTheDocument(); // PB from config
    expect(screen.getByDisplayValue("45")).toBeInTheDocument(); // Td default
    expect(screen.getByDisplayValue("180")).toBeInTheDocument(); // Ti default
    expect(screen.getByDisplayValue("0.5")).toBeInTheDocument(); // center default
  });

  it("switching the Select to fuzzy hides fields and shows the no-config hint without saving", () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "fuzzy" } });

    expect(screen.queryByDisplayValue("55")).not.toBeInTheDocument();
    expect(screen.getByText(/no configuration options/i)).toBeInTheDocument();
    expect(saveMock).not.toHaveBeenCalled();
  });

  it("renders a Toggle for a bool-typed option when a controller with one is selected", () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "pid_parallel" } });

    expect(screen.getByRole("button", { name: "Integral Windup Protection" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("saves the exact delta after editing PB, rebuilding the whole pid config with coerced floats", async () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByDisplayValue("55"), { target: { value: "62.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(saveMock).toHaveBeenCalledWith(
      {
        controller: {
          selected: "pid",
          config: {
            pid: { PB: 62.5, Td: 45, Ti: 180, center: 0.5 },
          },
        },
      },
      ["controller_update"],
    );
  });

  it("shows an error state and no Select when controllerMeta is null", () => {
    renderRoute(<ControllerTab />, makeContext({ controllerMeta: null }));

    expect(screen.getByText(/controller metadata unavailable/i)).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});
