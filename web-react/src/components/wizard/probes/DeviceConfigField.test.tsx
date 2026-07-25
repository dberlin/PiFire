import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ProbeConfigField } from "../../../helpers/wizard/probeTypes";
import { scan } from "../../../helpers/wizard/wizardApi";
import { DeviceConfigField } from "./DeviceConfigField";

rs.mock("../../../helpers/wizard/wizardApi", () => ({
  scan: rs.fn(),
  scanBluetooth: rs.fn(),
  scanThermoworks: rs.fn(),
}));

afterEach(cleanup);

const base = { friendly_name: "F", description: "" };

it("renders an int field and emits numeric-string changes", () => {
  const f: ProbeConfigField = { ...base, label: "ADC0_rd", type: "int", min: 1, step: 1 };
  const onChange = rs.fn();
  render(
    <DeviceConfigField
      field={f}
      value={10000}
      allValues={{}}
      availableProbes={[]}
      baseUrl=""
      onChange={onChange}
    />,
  );
  fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "5000" } });
  expect(onChange).toHaveBeenCalledWith("ADC0_rd", "5000");
});

it("hidden field renders null", () => {
  const f: ProbeConfigField = {
    ...base,
    label: "transient",
    type: "list",
    hidden: true,
    list_values: ["False"],
    list_labels: ["Fixed"],
  };
  const { container } = render(
    <DeviceConfigField
      field={f}
      value="False"
      allValues={{}}
      availableProbes={[]}
      baseUrl=""
      onChange={rs.fn()}
    />,
  );
  expect(container).toBeEmptyDOMElement();
});

it("device_serial stays visible despite hidden (Test Connection widget)", () => {
  const f: ProbeConfigField = { ...base, label: "device_serial", type: "string", hidden: true };
  render(
    <DeviceConfigField
      field={f}
      value=""
      allValues={{ email: "a@b.c", password: "x" }}
      availableProbes={[]}
      baseUrl=""
      onChange={rs.fn()}
    />,
  );
  expect(screen.getByRole("button", { name: /test connection/i })).toBeInTheDocument();
});

it("probes_list renders a multi-select of availableProbes", () => {
  const f: ProbeConfigField = { ...base, label: "probes_list", type: "probes_list" };
  render(
    <DeviceConfigField
      field={f}
      value={["Grill"]}
      allValues={{}}
      availableProbes={["Grill", "Food"]}
      baseUrl=""
      onChange={rs.fn()}
    />,
  );
  expect(screen.getByRole("listbox")).toBeInTheDocument();
  expect(screen.getAllByRole("option")).toHaveLength(2);
});

describe("remaining dispatch branches", () => {
  it("renders a list field via SelectField", () => {
    const f: ProbeConfigField = {
      ...base,
      label: "units",
      type: "list",
      list_values: ["F", "C"],
      list_labels: ["Fahrenheit", "Celsius"],
    };
    const onChange = rs.fn();
    render(
      <DeviceConfigField
        field={f}
        value="F"
        allValues={{}}
        availableProbes={[]}
        baseUrl=""
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "C" } });
    expect(onChange).toHaveBeenCalledWith("units", "C");
  });

  it("renders a plain string field via the default text branch", () => {
    const f: ProbeConfigField = { ...base, label: "name", type: "string" };
    const onChange = rs.fn();
    render(
      <DeviceConfigField
        field={f}
        value="a"
        allValues={{}}
        availableProbes={[]}
        baseUrl=""
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "b" } });
    expect(onChange).toHaveBeenCalledWith("name", "b");
  });

  it("renders i2c_bus_num via I2cBusPicker, forwarding kind and scan", async () => {
    (scan as ReturnType<typeof rs.fn>).mockResolvedValue({
      groups: [{ title: "By Bus", items: [{ value: "3", label: "i2c-3" }] }],
      error: null,
    });
    const f: ProbeConfigField = { ...base, label: "i2c_bus_num", type: "i2c_bus_num" };
    const onChange = rs.fn();
    render(
      <DeviceConfigField
        field={f}
        value="1"
        allValues={{ i2c_bus_kind: "mcp23017" }}
        availableProbes={[]}
        baseUrl=""
        onChange={onChange}
      />,
    );
    expect(screen.getByText(/mcp23017/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /discover/i }));
    expect(await screen.findByRole("button", { name: "i2c-3" })).toBeInTheDocument();
    expect(scan).toHaveBeenCalledWith("", { kind: "mcp23017" });

    fireEvent.click(screen.getByRole("button", { name: "i2c-3" }));
    expect(onChange).toHaveBeenCalledWith("i2c_bus_num", "3");
  });

  it("carries an i2c_bus_num field's manifest default into the picker as a free-text placeholder", () => {
    // 5 probe modules ship an i2c_bus_num field and none of them carries an
    // option list, so the control has to be fillable free text or a fresh
    // install cannot configure its ADC at all.
    const f: ProbeConfigField = {
      ...base,
      label: "i2c_bus_num",
      friendly_name: "I2C Bus",
      type: "i2c_bus_num",
      default: "CP2112",
    };
    const { container } = render(
      <DeviceConfigField
        field={f}
        value={undefined}
        allValues={{ i2c_bus_kind: "extended" }}
        availableProbes={[]}
        baseUrl=""
        onChange={rs.fn()}
      />,
    );
    const input = screen.getByLabelText("I2C Bus");
    expect(input.tagName).toBe("INPUT");
    expect(input).toHaveAttribute("placeholder", "CP2112");
    expect(container.querySelector("select")).toBeNull();
  });

  it("renders bt_address via BluetoothPicker", () => {
    const f: ProbeConfigField = { ...base, label: "bt_address", type: "bt_address" };
    render(
      <DeviceConfigField
        field={f}
        value="AA:BB"
        allValues={{}}
        availableProbes={[]}
        baseUrl=""
        onChange={rs.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /^scan$/i })).toBeInTheDocument();
  });

  it("renders usb_serial_device via UsbSerialPicker and invokes onScan", async () => {
    (scan as ReturnType<typeof rs.fn>).mockResolvedValue({
      groups: [{ title: "By Serial", items: [{ value: "/dev/ttyUSB1", label: "ttyUSB1" }] }],
      error: null,
    });
    const f: ProbeConfigField = {
      ...base,
      label: "usb_serial_device",
      type: "usb_serial_device",
    };
    render(
      <DeviceConfigField
        field={f}
        value="/dev/ttyUSB0"
        allValues={{}}
        availableProbes={[]}
        baseUrl=""
        onChange={rs.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /discover/i }));
    expect(await screen.findByRole("button", { name: "ttyUSB1" })).toBeInTheDocument();
    expect(scan).toHaveBeenCalledWith("", { kind: "usb_serial" });
  });
});
