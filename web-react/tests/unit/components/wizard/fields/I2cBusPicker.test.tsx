import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { I2cBusPicker } from "../../../../../src/components/wizard/fields/I2cBusPicker";
import { scan } from "../../../../../src/helpers/wizard/wizardApi";
import type { SettingsDependency } from "../../../../../src/helpers/wizard/wizardTypes";

rs.mock("../../../../../src/helpers/wizard/wizardApi", () => ({
  scan: rs.fn(),
}));

afterEach(cleanup);

const dep: SettingsDependency = {
  friendly_name: "I2C Bus",
  type: "i2c_bus_num",
  options: { "1": "Bus 1", "2": "Bus 2" },
  settings: ["i2c_bus_num"],
};

// The real manifest shape: all 8 grillplatform i2c_bus_num deps (and all 5 on
// the probes side) carry a `default` and NO `options` key at all.
const optionlessDep: SettingsDependency = {
  friendly_name: "Distance Sensor Extended I2C Bus",
  description: "'CP2112' or 'MCP2221' auto-discovers the bridge by adapter name.",
  type: "i2c_bus_num",
  default: "CP2112",
  settings: ["platform", "devices", "distance", "i2c_bus_num"],
};

describe("I2cBusPicker", () => {
  it("renders a text input, not a select, so a manifest dep with no options is still fillable", () => {
    const { container } = render(
      <I2cBusPicker
        dep={optionlessDep}
        value="CP2112"
        kindValue="extended"
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "extended" })}
      />,
    );
    const input = screen.getByLabelText("Distance Sensor Extended I2C Bus");
    expect(input.tagName).toBe("INPUT");
    expect(input).toHaveValue("CP2112");
    expect(container.querySelector("select")).toBeNull();
  });

  it("typing an arbitrary bus value calls onChange", () => {
    const onChange = rs.fn();
    render(
      <I2cBusPicker
        dep={optionlessDep}
        value=""
        kindValue="extended"
        onChange={onChange}
        onScan={() => scan("", { kind: "extended" })}
      />,
    );
    fireEvent.change(screen.getByLabelText("Distance Sensor Extended I2C Bus"), {
      target: { value: "serial:0123ABC" },
    });
    expect(onChange).toHaveBeenCalledWith("serial:0123ABC");
  });

  it("renders the dep description, the only place the CP2112 / serial: syntax is documented", () => {
    render(
      <I2cBusPicker
        dep={optionlessDep}
        value=""
        kindValue="extended"
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "extended" })}
      />,
    );
    expect(
      screen.getByText("'CP2112' or 'MCP2221' auto-discovers the bridge by adapter name."),
    ).toBeInTheDocument();
  });

  it("offers manifest options as non-binding datalist suggestions when present", () => {
    const { container } = render(
      <I2cBusPicker
        dep={dep}
        value="1"
        kindValue="mcp23017"
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "mcp23017" })}
      />,
    );
    const input = screen.getByLabelText("I2C Bus");
    const listId = input.getAttribute("list");
    expect(listId).toBeTruthy();
    const datalist = container.querySelector(`datalist[id="${listId}"]`);
    expect(datalist).not.toBeNull();
    const optionValues = Array.from(datalist!.querySelectorAll("option"), (o) => o.value);
    expect(optionValues).toEqual(["1", "2"]);
    expect(datalist!.textContent).toContain("Bus 1");
    expect(datalist!.textContent).toContain("Bus 2");
  });

  it("renders the field and the current kind value", () => {
    render(
      <I2cBusPicker
        dep={dep}
        value="1"
        kindValue="mcp23017"
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "i2c_bus_num" })}
      />,
    );
    expect(screen.getByText("I2C Bus")).toBeInTheDocument();
    expect(screen.getByText(/mcp23017/)).toBeInTheDocument();
  });

  it("renders nothing when dep is hidden", () => {
    const { container } = render(
      <I2cBusPicker
        dep={{ ...dep, hidden: true }}
        value="1"
        kindValue="mcp23017"
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "i2c_bus_num" })}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("calls onScan on Discover and renders the results panel", async () => {
    (scan as ReturnType<typeof rs.fn>).mockResolvedValue({
      groups: [{ title: "By Bus", items: [{ value: "3", label: "i2c-3 (pca9548)" }] }],
      error: null,
    });
    const onChange = rs.fn();
    render(
      <I2cBusPicker
        dep={dep}
        value="1"
        kindValue="mcp23017"
        onChange={onChange}
        onScan={() => scan("", { kind: "i2c_bus_num" })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /discover/i }));

    expect(await screen.findByRole("button", { name: "i2c-3 (pca9548)" })).toBeInTheDocument();
    expect(scan).toHaveBeenCalledWith("", { kind: "i2c_bus_num" });

    fireEvent.click(screen.getByRole("button", { name: "i2c-3 (pca9548)" }));
    expect(onChange).toHaveBeenCalledWith("3");
  });

  it("renders the discovery error instead of a panel", async () => {
    (scan as ReturnType<typeof rs.fn>).mockResolvedValue({
      groups: [],
      error: "No I2C buses found.",
    });
    render(
      <I2cBusPicker
        dep={dep}
        value="1"
        kindValue="mcp23017"
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "i2c_bus_num" })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /discover/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No I2C buses found.");
  });
});
