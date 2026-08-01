import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { I2cBusField } from "../../../../../src/components/wizard/fields/I2cBusField";
import type { I2cBusValue } from "../../../../../src/helpers/wizard/i2cBusTypes";

const dep = { friendly_name: "I2C Bus", settings: [], type: "i2c_bus" as const };
const noScan = () => Promise.resolve({ groups: [] });

function renderField(value: I2cBusValue, onChange = rs.fn()) {
  render(<I2cBusField dep={dep} value={value} onChange={onChange} onScan={noScan} />);
  return onChange;
}

afterEach(cleanup);

describe("I2cBusField", () => {
  it("renders no other field for a basic bus", () => {
    renderField({ kind: "basic" });
    expect(screen.queryByRole("textbox")).toBe(null);
    expect(screen.queryByRole("radio")).toBe(null);
  });

  it("renders the three ways to address a kernel bus", () => {
    renderField({ kind: "kernel", adapter: "CP2112" });
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    expect(screen.getByDisplayValue("CP2112")).toBeTruthy();
  });

  it("renders one input for ft232h and one for mcp2221", () => {
    renderField({ kind: "ft232h", url: "ftdi://ftdi:232h:FT9/1" });
    expect(screen.getByDisplayValue("ftdi://ftdi:232h:FT9/1")).toBeTruthy();
    expect(screen.queryByRole("radio")).toBe(null);
  });

  it("replaces the value when the kind changes, so no stale field survives", () => {
    const onChange = renderField({ kind: "kernel", adapter: "CP2112" });
    fireEvent.change(screen.getByLabelText("I2C Bus"), { target: { value: "ft232h" } });
    expect(onChange).toHaveBeenCalledWith({ kind: "ft232h", url: "" });
  });

  it("replaces the value when the kernel radio changes", () => {
    const onChange = renderField({ kind: "kernel", adapter: "CP2112" });
    fireEvent.click(screen.getByLabelText("Bus number"));
    expect(onChange).toHaveBeenCalledWith({ kind: "kernel", bus_num: null });
  });

  it("shows the validation error inline while typing", () => {
    renderField({ kind: "kernel", adapter: "" });
    expect(screen.getByRole("alert").textContent).toMatch(/adapter/i);
  });

  it("shows no error for a blank ft232h url", () => {
    renderField({ kind: "ft232h", url: "" });
    expect(screen.queryByRole("alert")).toBe(null);
  });

  it("writes a discovered value into the selected kernel field", async () => {
    const onChange = rs.fn();
    render(
      <I2cBusField
        dep={dep}
        value={{ kind: "kernel", adapter: "" }}
        onChange={onChange}
        onScan={() =>
          Promise.resolve({
            groups: [
              { title: "By Adapter Name", items: [{ value: "CP2112", label: "CP2112 (bus 7)" }] },
            ],
          })
        }
      />,
    );
    fireEvent.click(screen.getByText("Discover"));
    fireEvent.click(await screen.findByText("CP2112 (bus 7)"));
    expect(onChange).toHaveBeenCalledWith({ kind: "kernel", adapter: "CP2112" });
  });

  it("renders nothing when the dep is hidden", () => {
    const { container } = render(
      <I2cBusField
        dep={{ ...dep, hidden: true }}
        value={{ kind: "basic" }}
        onChange={rs.fn()}
        onScan={noScan}
      />,
    );
    expect(container.textContent).toBe("");
  });
});
