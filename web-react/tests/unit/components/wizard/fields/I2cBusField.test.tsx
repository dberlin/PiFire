import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { I2cBusField } from "../../../../../src/components/wizard/fields/I2cBusField";
import type { I2CBusValue } from "../../../../../src/helpers/contracts/wizard.gen";
import { allStylesheets, classesStylingButtons } from "../../../../../src/helpers/cssCoverage";

const dep = { friendly_name: "I2C Bus", settings: [], type: "i2c_bus" as const };
const noScan = () => Promise.resolve({ groups: [], error: null });

function renderField(
  value: I2CBusValue & { kind: NonNullable<I2CBusValue["kind"]> },
  onChange = rs.fn(),
) {
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

  it("emits null, not NaN, when the bus number is cleared", () => {
    const onChange = renderField({ kind: "kernel", bus_num: 3 });
    fireEvent.change(screen.getByRole("textbox", { name: "Bus number" }), {
      target: { value: "" },
    });
    expect(onChange).toHaveBeenCalledWith({ kind: "kernel", bus_num: null });
    // NaN would survive the assertion above under some matchers but not a JSON
    // round trip, which is how a saved draft actually travels.
    const [emitted] = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(JSON.parse(JSON.stringify(emitted))).toEqual({ kind: "kernel", bus_num: null });
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
            error: null,
          })
        }
      />,
    );
    fireEvent.click(screen.getByText("Discover"));
    fireEvent.click(await screen.findByText("CP2112 (bus 7)"));
    expect(onChange).toHaveBeenCalledWith({ kind: "kernel", adapter: "CP2112" });
  });

  it("writes a discovered bus number into the selected kernel field, as a number", async () => {
    const onChange = rs.fn();
    render(
      <I2cBusField
        dep={dep}
        value={{ kind: "kernel", bus_num: null }}
        onChange={onChange}
        onScan={() =>
          Promise.resolve({
            groups: [
              {
                title: "By Bus Number",
                items: [{ value: "7", label: "CP2112 SMBus Bridge (bus 7)" }],
              },
            ],
            error: null,
          })
        }
      />,
    );
    fireEvent.click(screen.getByText("Discover"));
    fireEvent.click(await screen.findByText("CP2112 SMBus Bridge (bus 7)"));
    expect(onChange).toHaveBeenCalledWith({ kind: "kernel", bus_num: 7 });
  });

  it("writes a discovered serial into the selected kernel field", async () => {
    const onChange = rs.fn();
    render(
      <I2cBusField
        dep={dep}
        value={{ kind: "kernel", serial: "" }}
        onChange={onChange}
        onScan={() =>
          Promise.resolve({
            groups: [
              {
                title: "By Serial",
                items: [{ value: "AB12", label: "CP2112 SMBus Bridge [AB12]" }],
              },
            ],
            error: null,
          })
        }
      />,
    );
    fireEvent.click(screen.getByText("Discover"));
    fireEvent.click(await screen.findByText("CP2112 SMBus Bridge [AB12]"));
    expect(onChange).toHaveBeenCalledWith({ kind: "kernel", serial: "AB12" });
  });

  it("writes a discovered value into the ft232h url", async () => {
    const onChange = rs.fn();
    render(
      <I2cBusField
        dep={dep}
        value={{ kind: "ft232h", url: "" }}
        onChange={onChange}
        onScan={() =>
          Promise.resolve({
            groups: [
              {
                title: "FT232H Devices",
                items: [{ value: "ftdi://ftdi:232h:FT9/1", label: "FT232H #1" }],
              },
            ],
            error: null,
          })
        }
      />,
    );
    fireEvent.click(screen.getByText("Discover"));
    fireEvent.click(await screen.findByText("FT232H #1"));
    expect(onChange).toHaveBeenCalledWith({ kind: "ft232h", url: "ftdi://ftdi:232h:FT9/1" });
  });

  it("writes a discovered value into the mcp2221 serial", async () => {
    const onChange = rs.fn();
    render(
      <I2cBusField
        dep={dep}
        value={{ kind: "mcp2221", serial: "" }}
        onChange={onChange}
        onScan={() =>
          Promise.resolve({
            groups: [{ title: "MCP2221 Devices", items: [{ value: "XY99", label: "XY99" }] }],
            error: null,
          })
        }
      />,
    );
    fireEvent.click(screen.getByText("Discover"));
    fireEvent.click(await screen.findByText("XY99"));
    expect(onChange).toHaveBeenCalledWith({ kind: "mcp2221", serial: "XY99" });
  });

  // Preflight strips buttons back to inherited text, so "has no rule" and "is
  // not a button" are the same state on screen. The serial picker's Discover
  // button looks right only because its wrapper happens to carry the class the
  // shared rule keys on; this field reached the same layout under a name of its
  // own and silently missed the styling that came with it.
  it("puts its Discover button under a rule that styles buttons", () => {
    const styled = classesStylingButtons(allStylesheets());
    // Negative control: an extractor that matched nothing would let the
    // assertion below pass by finding no rule to hold the field to.
    expect(styled.has("pf-field-column")).toBe(true);

    renderField({ kind: "kernel", adapter: "CP2112" });
    const discover = screen.getByRole("button", { name: "Discover" });
    const selector = [...styled].map((name) => `.${name}`).join(",");
    expect(discover.parentElement?.closest(selector) ?? null).not.toBe(null);
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
