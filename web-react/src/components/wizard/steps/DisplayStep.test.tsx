import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { DisplayStep } from "./DisplayStep";

const fetchModuleValues = rs.fn();
rs.mock("../../../helpers/wizard/wizardApi", () => ({
  scan: rs.fn().mockResolvedValue({ groups: [], error: null }),
  fetchModuleValues: (...args: unknown[]) => fetchModuleValues(...args),
}));

afterEach(() => {
  cleanup();
  rs.resetAllMocks();
});

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
  board_probe_maps: {},
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
  it("selecting a display module fetches its values and calls onChange with the new selection", async () => {
    fetchModuleValues.mockResolvedValue({ settings: {}, config: {} });
    const onChange = rs.fn();
    render(<DisplayStep state={state} working={baseWorking()} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "generic" },
    });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.selections.display).toBe("generic");
    expect(fetchModuleValues).toHaveBeenCalledWith("", "display", "generic");
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

  it("replaces the display dep map wholesale so a stale key from the previous module is gone", async () => {
    // 12 of 30 display modules carry `buttonslevel`; the rest carry none.
    // Switching must not leave the old module's key behind -- a stale key
    // reaches /finish and used to KeyError inside the detached installer.
    fetchModuleValues.mockResolvedValue({ settings: {}, config: {} });
    const onChange = rs.fn();
    const working = {
      ...baseWorking(),
      selections: { ...baseWorking().selections, display: "generic" },
      settings_dep_values: {
        ...baseWorking().settings_dep_values,
        display: { buttonslevel: "HIGH" },
      },
    };
    render(<DisplayStep state={state} working={working} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "other" },
    });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.settings_dep_values.display).toEqual({});
    expect(next.settings_dep_values.display.buttonslevel).toBeUndefined();
  });

  it("preserves an unsaved display_config edit across a module switch", async () => {
    // D1: the switch applies only `settings`; display_config stays client-held,
    // so the user's unsaved edit survives switching away (and back).
    //
    // The unsaved edit is seeded on BOTH the FROM module (generic) and the TO
    // module (other), with the server returning a DIFFERENT value ("F") for
    // the newly selected module. This closes a gap in the previous version of
    // this test: a regression that wrote `values.config` under the newly
    // selected module (instead of leaving display_config untouched) would
    // land on display_config.other and still pass, since that assertion only
    // checked the untouched `generic` entry.
    fetchModuleValues.mockResolvedValue({
      settings: {},
      config: { units: "F" }, // server copy -- must be IGNORED
    });
    const onChange = rs.fn();
    const working = {
      ...baseWorking(),
      selections: { ...baseWorking().selections, display: "generic" },
      display_config: {
        generic: { units: "C" }, // the user's unsaved edit on the FROM module
        other: { units: "C" }, // the user's unsaved edit on the TO module
      },
    };
    render(<DisplayStep state={state} working={working} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "other" },
    });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    // Server's "F" must be ignored for the newly selected module.
    expect(next.display_config.other.units).toBe("C");
    // The other module's unsaved edit is left untouched.
    expect(next.display_config.generic.units).toBe("C");
  });

  it("shows an error banner and does not call onChange when the fetch fails", async () => {
    fetchModuleValues.mockRejectedValue(new Error("boom"));
    const onChange = rs.fn();
    render(<DisplayStep state={state} working={baseWorking()} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "generic" },
    });

    await waitFor(() => expect(screen.getByText(/couldn't load the display/i)).toBeInTheDocument());
    expect(onChange).not.toHaveBeenCalled();
  });
});
