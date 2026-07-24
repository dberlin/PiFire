import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { DisplayStep } from "./DisplayStep";

rs.mock("../../../helpers/wizard/wizardApi", () => ({
  scan: rs.fn().mockResolvedValue({ groups: [], error: null }),
}));

afterEach(cleanup);

const state: WizardState = {
  modules_metadata: {
    grillplatform: {},
    probes: {},
    distance: {},
    display: {
      generic: {
        friendly_name: "Generic 128x64",
        settings_dependencies: {
          mode: {
            friendly_name: "Mode",
            options: { a: "Option A", b: "Option B" },
            settings: [],
          },
        },
        config: [
          {
            option_name: "units",
            option_friendly_name: "Units",
            option_type: "list",
            list_values: ["F", "C"],
            list_labels: ["Fahrenheit", "Celsius"],
            default: "F",
          },
        ],
      },
      other: { friendly_name: "Other Display", settings_dependencies: {} },
    },
  },
  selections: { grillplatform: null, probes: null, distance: null, display: null },
  settings_dep_values: { grillplatform: {}, probes: {}, distance: {}, display: {} },
  display_config: {},
  probe_map: { probe_devices: [], probe_info: [] },
  probe_profiles: [],
  probes_units: "F",
  control_mode: "Stop",
  first_time_setup: false,
  has_draft: false,
};

function baseWorking(): WizardWorking {
  return {
    selections: { grillplatform: null, probes: null, distance: null, display: null },
    settings_dep_values: { grillplatform: {}, probes: {}, distance: {}, display: {} },
    display_config: {},
    probe_map: { probe_devices: [], probe_info: [] },
    probes_units: "F",
  };
}

describe("DisplayStep", () => {
  it("selecting a display module calls onChange with an updated display selection", () => {
    const onChange = rs.fn();
    render(<DisplayStep state={state} working={baseWorking()} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "generic" },
    });

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.selections.display).toBe("generic");
  });

  it("editing a config option calls onChange with updated display_config for the selected module", () => {
    const onChange = rs.fn();
    const working = {
      ...baseWorking(),
      selections: { ...baseWorking().selections, display: "generic" },
    };
    render(<DisplayStep state={state} working={working} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Units" }), {
      target: { value: "C" },
    });

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.display_config.generic.units).toBe("C");
  });

  it("changing a settings dependency field calls onChange with updated settings_dep_values.display", () => {
    const onChange = rs.fn();
    const working = {
      ...baseWorking(),
      selections: { ...baseWorking().selections, display: "generic" },
    };
    render(<DisplayStep state={state} working={working} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Mode" }), {
      target: { value: "b" },
    });

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.settings_dep_values.display.mode).toBe("b");
  });

  it("handles a null display selection without throwing (displayConfigFor('') fallback)", () => {
    expect(() =>
      render(<DisplayStep state={state} working={baseWorking()} onChange={rs.fn()} baseUrl="" />),
    ).not.toThrow();
    expect(screen.getByText("Display")).toBeInTheDocument();
  });
});
