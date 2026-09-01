import type { WizardState } from "@pifire/core/contracts/wizard";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { DistanceStep } from "../../../../../src/components/wizard/steps/DistanceStep";
import type { WizardWorking } from "../../../../../src/helpers/wizard/wizardTypes";

const fetchModuleValues = rs.fn();
rs.mock("../../../../../src/helpers/wizard/wizardApi", () => ({
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
    display: {},
    distance: {
      // 6 of 7 real distance modules have no deps and no config -- bare card.
      none: { friendly_name: "None", settings_dependencies: {} },
      // sen0628 is the lone exception: one usb_serial_device field.
      sen0628: {
        friendly_name: "SEN0628 USB ToF",
        settings_dependencies: {
          sen0628_device: {
            friendly_name: "Serial Device (USB)",
            type: "usb_serial_device",
            settings: ["platform", "devices", "distance", "device"],
            options: {
              "/dev/ttyACM0": "/dev/ttyACM0",
              "/dev/ttyUSB0": "/dev/ttyUSB0",
            },
          },
        },
      },
    },
  },
  selections: { grillplatform: null, probes: null, display: null, distance: "none" },
  settings_dep_values: { grillplatform: {}, probes: {}, display: {}, distance: {} },
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
    selections: { grillplatform: null, probes: null, display: null, distance: "none" },
    settings_dep_values: { grillplatform: {}, probes: {}, display: {}, distance: {} },
    display_config: {},
    probe_map: { probe_devices: [], probe_info: [] },
    probes_units: "F",
  };
}

describe("DistanceStep", () => {
  it("renders a bare card for a module with no settings dependencies", () => {
    render(<DistanceStep state={state} working={baseWorking()} onChange={rs.fn()} baseUrl="" />);
    expect(screen.getByRole("heading", { name: "Distance / Hopper" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Module" })).toBeInTheDocument();
    // no dep fields for `none`
    expect(screen.queryByLabelText("Serial Device (USB)")).not.toBeInTheDocument();
  });

  it("renders the sen0628 USB serial field when that module is selected", () => {
    const working = {
      ...baseWorking(),
      selections: { ...baseWorking().selections, distance: "sen0628" },
    };
    render(<DistanceStep state={state} working={working} onChange={rs.fn()} baseUrl="" />);
    expect(screen.getByLabelText("Serial Device (USB)")).toBeInTheDocument();
  });

  it("changing the sen0628 serial device field routes through setDepValue without a fetch round-trip", () => {
    const onChange = rs.fn();
    const working = {
      ...baseWorking(),
      selections: { ...baseWorking().selections, distance: "sen0628" },
    };
    render(<DistanceStep state={state} working={working} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByLabelText("Serial Device (USB)"), {
      target: { value: "/dev/ttyUSB0" },
    });

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.settings_dep_values.distance.sen0628_device).toBe("/dev/ttyUSB0");
    expect(fetchModuleValues).not.toHaveBeenCalled();
  });

  it("switching modules fetches values and applies them to the distance dep map", async () => {
    fetchModuleValues.mockResolvedValue({
      settings: { sen0628_device: "/dev/ttyACM0" },
      config: {},
    });
    const onChange = rs.fn();
    render(<DistanceStep state={state} working={baseWorking()} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "sen0628" },
    });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.selections.distance).toBe("sen0628");
    expect(next.settings_dep_values.distance.sen0628_device).toBe("/dev/ttyACM0");
    expect(fetchModuleValues).toHaveBeenCalledWith("", "distance", "sen0628");
  });

  it("shows an error banner and does not call onChange when the fetch fails", async () => {
    fetchModuleValues.mockRejectedValue(new Error("boom"));
    const onChange = rs.fn();
    render(<DistanceStep state={state} working={baseWorking()} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "sen0628" },
    });

    await waitFor(() => expect(screen.getByText(/couldn't load the sensor/i)).toBeInTheDocument());
    expect(onChange).not.toHaveBeenCalled();
  });
});
