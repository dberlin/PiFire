import type { ProbeModuleData } from "@pifire/core/contracts/wizard";
import { afterEach, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { DeviceForm } from "../../../../../src/components/wizard/probes/DeviceForm";

afterEach(cleanup);

const adsModule: ProbeModuleData = {
  friendly_name: "ADS1115 Adafruit",
  filename: "ads1115_adafruit",
  description: "An I2C ADC board.",
  image: "ads1115.png",
  device_specific: {
    ports: ["ADC0", "ADC1"],
    type: "adc",
    config: [
      {
        label: "i2c_bus_addr",
        friendly_name: "I2C Bus Address",
        type: "list",
        default: "0x48",
        list_values: ["0x48"],
        list_labels: ["0x48"],
      },
    ],
  },
};

const mcp9601Note =
  "Hardware fault detection is disabled by default. A disconnected or electrically shorted/collapsed thermocouple can read as ambient temperature instead of reporting a fault. Enable hardware detection only when the board includes the required MCP9601 VSENSE network; SEN-30010-W is verified.";

const softwareDetectionWarning =
  "WARNING: This amplifier does not have enabled, board-supported thermocouple fault detection. A disconnected or electrically shorted/collapsed probe may read as the cold-junction (ambient) temperature instead of reporting a fault. Software thermocouple fault detection is STRONGLY RECOMMENDED.";

const mcp9601Module: ProbeModuleData = {
  ...adsModule,
  friendly_name: "MCP9601 Thermocouple Amplifier (SEN-30010-W)",
  filename: "mcp9601_adafruit",
  image: "mcp9601.png",
  notes: mcp9601Note,
  device_specific: {
    ports: ["KTT0"],
    type: "thermocouple",
    config: [],
  },
};

it("add mode renders the name input and one config field, and reports name changes", () => {
  const onNameChange = rs.fn();
  render(
    <DeviceForm
      mode="add"
      moduleData={adsModule}
      values={{ i2c_bus_addr: "0x48" }}
      nameValue=""
      availableProbes={[]}
      baseUrl=""
      onNameChange={onNameChange}
      onFieldChange={rs.fn()}
      onSubmit={rs.fn()}
      onCancel={rs.fn()}
      error={null}
    />,
  );
  expect(screen.getByText("ADS1115 Adafruit")).toBeInTheDocument();
  expect(screen.getByText("I2C Bus Address")).toBeInTheDocument();
  const nameInput = screen.getByLabelText(/unique device name/i);
  fireEvent.change(nameInput, { target: { value: "MyDevice" } });
  expect(onNameChange).toHaveBeenCalledWith("MyDevice");
  expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument();
});

it("edit mode shows a value carried over from values and labels the submit button Save", () => {
  render(
    <DeviceForm
      mode="edit"
      moduleData={adsModule}
      values={{ i2c_bus_addr: "0x48" }}
      nameValue="ADS1115"
      availableProbes={[]}
      baseUrl=""
      onNameChange={rs.fn()}
      onFieldChange={rs.fn()}
      onSubmit={rs.fn()}
      onCancel={rs.fn()}
      error={null}
    />,
  );
  expect(screen.getByDisplayValue("ADS1115")).toBeInTheDocument();
  expect(screen.getByDisplayValue("0x48")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
});

it("surfaces the error and invokes onSubmit/onCancel", () => {
  const onSubmit = rs.fn();
  const onCancel = rs.fn();
  render(
    <DeviceForm
      mode="add"
      moduleData={adsModule}
      values={{}}
      nameValue="X"
      availableProbes={[]}
      baseUrl=""
      onNameChange={rs.fn()}
      onFieldChange={rs.fn()}
      onSubmit={onSubmit}
      onCancel={onCancel}
      error="Device name already exists."
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(/already exists/i);
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(onSubmit).toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
  expect(onCancel).toHaveBeenCalled();
});

it("replaces static thermocouple notes with a conditional software-detection warning", () => {
  render(
    <DeviceForm
      mode="add"
      moduleData={mcp9601Module}
      values={{ hardware_fault_detection: "False" }}
      nameValue=""
      availableProbes={[]}
      baseUrl=""
      onNameChange={rs.fn()}
      onFieldChange={rs.fn()}
      onSubmit={rs.fn()}
      onCancel={rs.fn()}
      error={null}
    />,
  );

  const warning = document.querySelector(".pf-module-notes");
  expect(warning).toHaveTextContent(softwareDetectionWarning);
  expect(warning?.textContent).toBe(softwareDetectionWarning);
  expect(screen.queryByText(mcp9601Note)).toBeNull();
  expect(
    screen.getByRole("img", {
      name: "MCP9601 Thermocouple Amplifier (SEN-30010-W)",
    }),
  ).toHaveAttribute("src", "/static/img/wizard/mcp9601.png");
});

it("shows the warning when a thermocouple module has no hardware detection setting", () => {
  render(
    <DeviceForm
      mode="add"
      moduleData={{ ...mcp9601Module, notes: undefined }}
      values={{}}
      nameValue=""
      availableProbes={[]}
      baseUrl=""
      onNameChange={rs.fn()}
      onFieldChange={rs.fn()}
      onSubmit={rs.fn()}
      onCancel={rs.fn()}
      error={null}
    />,
  );

  expect(document.querySelector(".pf-module-notes")).toHaveTextContent(softwareDetectionWarning);
});

it("shows no warning or duplicate static note when thermocouple hardware detection is enabled", () => {
  const { container } = render(
    <DeviceForm
      mode="edit"
      moduleData={mcp9601Module}
      values={{ hardware_fault_detection: "True" }}
      nameValue="MCP9601"
      availableProbes={[]}
      baseUrl=""
      onNameChange={rs.fn()}
      onFieldChange={rs.fn()}
      onSubmit={rs.fn()}
      onCancel={rs.fn()}
      error={null}
    />,
  );

  expect(container.querySelector(".pf-module-notes")).toBeNull();
  expect(screen.queryByText(mcp9601Note)).toBeNull();
});

it("preserves ordinary notes for non-thermocouple modules", () => {
  render(
    <DeviceForm
      mode="add"
      moduleData={{ ...adsModule, notes: "Install this ADC on the primary I2C bus." }}
      values={{}}
      nameValue=""
      availableProbes={[]}
      baseUrl=""
      onNameChange={rs.fn()}
      onFieldChange={rs.fn()}
      onSubmit={rs.fn()}
      onCancel={rs.fn()}
      error={null}
    />,
  );

  expect(document.querySelector(".pf-module-notes")).toHaveTextContent(
    "Install this ADC on the primary I2C bus.",
  );
});
