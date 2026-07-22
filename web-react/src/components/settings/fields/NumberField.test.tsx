import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { NumberField } from "./NumberField";

describe("NumberField", () => {
  it("renders value + suffix and emits parsed numbers", () => {
    const onChange = rs.fn();
    render(<NumberField label="Max Temp" value={550} onChange={onChange} suffix="°" />);
    const input = screen.getByRole("spinbutton");
    expect(input).toHaveValue(550);
    expect(screen.getByText("°")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "600" } });
    expect(onChange).toHaveBeenCalledWith(600);
  });

  it("renders without suffix", () => {
    const onChange = rs.fn();
    render(<NumberField label="Count" value={42} onChange={onChange} />);
    const input = screen.getByRole("spinbutton");
    expect(input).toHaveValue(42);
    expect(screen.queryByText(/°|%/)).not.toBeInTheDocument();
  });

  it("respects min/max/step props", () => {
    const onChange = rs.fn();
    render(<NumberField label="Temp" value={50} onChange={onChange} min={0} max={100} step={5} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    expect(input.min).toBe("0");
    expect(input.max).toBe("100");
    expect(input.step).toBe("5");
  });

  it("parses decimal input correctly", () => {
    const onChange = rs.fn();
    render(<NumberField label="Price" value={19.99} onChange={onChange} />);
    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "29.50" } });
    expect(onChange).toHaveBeenCalledWith(29.5);
  });

  it("renders label", () => {
    const onChange = rs.fn();
    render(<NumberField label="My Label" value={0} onChange={onChange} />);
    expect(screen.getByText("My Label")).toBeInTheDocument();
  });
});
