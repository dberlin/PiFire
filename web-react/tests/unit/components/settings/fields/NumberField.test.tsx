import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { NumberField } from "../../../../../src/components/settings/fields/NumberField";

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

  // React's settings tree has no <form>, so `min`/`max` are advisory: nothing
  // stops a user typing 500 into a field marked max={9}. The blur clamp is what
  // makes them enforcement. Clamping in onChange instead would make a bounded
  // field untypeable (with min={20}, typing "25" clamps the intermediate "2" to
  // 20 and the user ends up with "205").
  it("does not clamp mid-typing — an out-of-range change emits the raw value", () => {
    const onChange = rs.fn();
    render(<NumberField label="Temp" value={50} onChange={onChange} min={20} max={100} />);
    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "2" } });
    expect(onChange).toHaveBeenCalledWith(2);
  });

  it("clamps an above-max value to max on blur", () => {
    const onChange = rs.fn();
    render(<NumberField label="PMode" value={5} onChange={onChange} min={0} max={9} />);
    const input = screen.getByRole("spinbutton");
    fireEvent.blur(input, { target: { value: "500" } });
    expect(onChange).toHaveBeenCalledWith(9);
  });

  it("clamps a below-min value to min on blur", () => {
    const onChange = rs.fn();
    render(<NumberField label="Warning Time" value={30} onChange={onChange} min={5} max={240} />);
    const input = screen.getByRole("spinbutton");
    fireEvent.blur(input, { target: { value: "1" } });
    expect(onChange).toHaveBeenCalledWith(5);
  });

  it("emits no extra onChange when blurring an in-range value", () => {
    const onChange = rs.fn();
    render(<NumberField label="Temp" value={50} onChange={onChange} min={0} max={100} />);
    const input = screen.getByRole("spinbutton");
    fireEvent.blur(input, { target: { value: "50" } });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("never clamps on blur when neither min nor max is supplied", () => {
    const onChange = rs.fn();
    render(<NumberField label="Auger Rate" value={0.3} onChange={onChange} />);
    const input = screen.getByRole("spinbutton");
    fireEvent.blur(input, { target: { value: "-999" } });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("still forwards min/max/step as DOM attributes (spinner + a11y semantics)", () => {
    const onChange = rs.fn();
    render(<NumberField label="PMode" value={5} onChange={onChange} min={0} max={9} step={1} />);
    const input = screen.getByRole("spinbutton");
    expect(input).toHaveAttribute("min", "0");
    expect(input).toHaveAttribute("max", "9");
    expect(input).toHaveAttribute("step", "1");
  });

  it("renders a hint when provided and omits it otherwise", () => {
    const onChange = rs.fn();
    const { unmount } = render(
      <NumberField label="Prime on Startup" value={0} onChange={onChange} hint="0 = disabled" />,
    );
    expect(screen.getByText("0 = disabled")).toBeInTheDocument();
    unmount();

    render(<NumberField label="Prime on Startup" value={0} onChange={onChange} />);
    expect(screen.queryByText("0 = disabled")).toBeNull();
  });
});
