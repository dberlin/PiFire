import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GrillPlatformStep } from "../../../../../src/components/wizard/steps/GrillPlatformStep";
import type { ProbeMap } from "../../../../../src/helpers/wizard/probeTypes";
import type { WizardState, WizardWorking } from "../../../../../src/helpers/wizard/wizardTypes";

const fetchModuleValues = rs.fn();
rs.mock("../../../../../src/helpers/wizard/wizardApi", () => ({
  fetchModuleValues: (...args: unknown[]) => fetchModuleValues(...args),
  scan: rs.fn().mockResolvedValue({ groups: [], error: null }),
}));

afterEach(() => {
  cleanup();
  rs.resetAllMocks();
});

const boardDefault: ProbeMap = {
  probe_devices: [
    { device: "GRILL", module: "ads1115", module_filename: "ads1115", ports: ["ADC0"], config: {} },
  ],
  probe_info: [],
};
const boardOther: ProbeMap = {
  probe_devices: [
    { device: "OTHER", module: "ads1115", module_filename: "ads1115", ports: ["ADC1"], config: {} },
  ],
  probe_info: [],
};

function makeState(firstTime: boolean): WizardState {
  return {
    modules_metadata: {
      grillplatform: {
        pcb_4: {
          friendly_name: "PCB 4.x.x",
          settings_dependencies: {
            system_type: {
              friendly_name: "System",
              options: { raspberry_pi_all: "Pi" },
              settings: [],
            },
          },
        },
        pcb_2: {
          friendly_name: "PCB 2.00a",
          settings_dependencies: {
            system_type: {
              friendly_name: "System",
              options: { raspberry_pi_all: "Pi" },
              settings: [],
            },
          },
        },
      },
      probes: {},
      distance: {},
      display: {},
    },
    selections: { grillplatform: "pcb_4", probes: null, distance: null, display: null },
    settings_dep_values: {
      grillplatform: { system_type: "raspberry_pi_all" },
      probes: {},
      distance: {},
      display: {},
    },
    display_config: {},
    probe_map: boardDefault,
    probe_profiles: [],
    probes_units: "F",
    board_probe_maps: { pcb_4: boardDefault, pcb_2: boardOther },
    control_mode: "Stop",
    first_time_setup: firstTime,
    has_draft: false,
  };
}

function makeWorking(state: WizardState): WizardWorking {
  return {
    selections: { ...state.selections },
    settings_dep_values: structuredClone(state.settings_dep_values),
    display_config: {},
    probe_map: structuredClone(state.probe_map),
    probes_units: "F",
  };
}

describe("GrillPlatformStep", () => {
  it("switching platform fetches module values and applies them + reseeds probe_map", async () => {
    fetchModuleValues.mockResolvedValue({ settings: { system_type: "prototype" }, config: {} });
    const state = makeState(true);
    const onChange = rs.fn();
    render(
      <GrillPlatformStep
        state={state}
        working={makeWorking(state)}
        onChange={onChange}
        baseUrl=""
      />,
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "pcb_2" },
    });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.selections.grillplatform).toBe("pcb_2");
    expect(next.settings_dep_values.grillplatform.system_type).toBe("prototype");
    // fresh install + unedited current == prev board default -> reseed to pcb_2's board map
    expect(next.probe_map.probe_devices[0].device).toBe("OTHER");
    expect(fetchModuleValues).toHaveBeenCalledWith("", "grillplatform", "pcb_2");
  });

  it("does not reseed probe_map on an existing install", async () => {
    fetchModuleValues.mockResolvedValue({ settings: { system_type: "prototype" }, config: {} });
    const state = makeState(false);
    const onChange = rs.fn();
    render(
      <GrillPlatformStep
        state={state}
        working={makeWorking(state)}
        onChange={onChange}
        baseUrl=""
      />,
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "pcb_2" },
    });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.probe_map.probe_devices[0].device).toBe("GRILL"); // untouched
  });

  it("shows an error banner and does not call onChange when the fetch fails", async () => {
    fetchModuleValues.mockRejectedValue(new Error("boom"));
    const state = makeState(true);
    const onChange = rs.fn();
    render(
      <GrillPlatformStep
        state={state}
        working={makeWorking(state)}
        onChange={onChange}
        baseUrl=""
      />,
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "pcb_2" },
    });

    await waitFor(() =>
      expect(screen.getByText(/couldn't load the platform/i)).toBeInTheDocument(),
    );
    expect(onChange).not.toHaveBeenCalled();
  });
});
