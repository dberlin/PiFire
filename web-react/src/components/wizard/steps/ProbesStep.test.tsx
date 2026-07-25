import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { ProbesStep } from "./ProbesStep";

afterEach(cleanup);

function fixtures(): { state: WizardState; working: WizardWorking } {
  const modules = {
    ads1115_adafruit: {
      friendly_name: "ADS1115 Adafruit",
      filename: "ads1115_adafruit",
      device_specific: { ports: ["ADC0"], type: "adc", config: [] },
    },
  };
  const state = {
    modules_metadata: { grillplatform: {}, probes: modules, display: {}, distance: {} },
    selections: { grillplatform: null, probes: null, display: null, distance: null },
    settings_dep_values: { grillplatform: {}, probes: {}, display: {}, distance: {} },
    display_config: {},
    probe_map: { probe_devices: [], probe_info: [] },
    probe_profiles: [],
    probes_units: "F",
    board_probe_maps: {},
    control_mode: "Stop",
    first_time_setup: false,
    has_draft: false,
  } as unknown as WizardState;
  const working: WizardWorking = {
    selections: state.selections,
    settings_dep_values: state.settings_dep_values,
    display_config: {},
    probe_map: state.probe_map,
    probes_units: "F",
  };
  return { state, working };
}

describe("ProbesStep", () => {
  it("renders both cards and a units selector", () => {
    const { state, working } = fixtures();
    render(<ProbesStep state={state} working={working} onChange={rs.fn()} baseUrl="" />);
    expect(screen.getByLabelText(/temp units/i)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /probe devices/i })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /probe ports/i })).toBeInTheDocument();
  });

  it("changing units emits updated working state", () => {
    const { state, working } = fixtures();
    const onChange = rs.fn();
    render(<ProbesStep state={state} working={working} onChange={onChange} baseUrl="" />);
    fireEvent.change(screen.getByLabelText(/temp units/i), { target: { value: "C" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ probes_units: "C" }));
  });

  it("a probe_map edit from DevicesCard is folded back into working.probe_map via onChange", () => {
    const { state, working } = fixtures();
    working.probe_map = {
      probe_devices: [
        {
          device: "ADC0",
          module: "ads1115_adafruit",
          module_filename: "ads1115_adafruit",
          ports: ["ADC0"],
          config: {},
        },
      ],
      probe_info: [],
    };
    const onChange = rs.fn();
    render(<ProbesStep state={state} working={working} onChange={onChange} baseUrl="" />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    // A device delete cascades to its probes, so DevicesCard confirms first.
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        probe_map: expect.objectContaining({ probe_devices: [] }),
      }),
    );
  });
});
