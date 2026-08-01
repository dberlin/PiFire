import { afterEach, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DeviceForm } from "../../../../../src/components/wizard/probes/DeviceForm";
import type { ProbeModuleData } from "../../../../../src/helpers/wizard/probeTypes";

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
