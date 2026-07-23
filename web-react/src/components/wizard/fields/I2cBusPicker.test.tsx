import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { scan } from "../../../helpers/wizard/wizardApi";
import type { SettingsDependency } from "../../../helpers/wizard/wizardTypes";
import { I2cBusPicker } from "./I2cBusPicker";

rs.mock("../../../helpers/wizard/wizardApi", () => ({
  scan: rs.fn(),
}));

afterEach(cleanup);

const dep: SettingsDependency = {
  friendly_name: "I2C Bus",
  type: "i2c_bus_num",
  options: { "1": "Bus 1", "2": "Bus 2" },
  settings: ["i2c_bus_num"],
};

describe("I2cBusPicker", () => {
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
