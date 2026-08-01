import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SelectField } from "../../../../../src/components/wizard/fields/SelectField";

afterEach(cleanup);

describe("SelectField", () => {
  const options = [
    { value: "1", label: "Bus 1" },
    { value: "2", label: "Bus 2" },
  ];

  it("renders label and options", () => {
    render(<SelectField label="I2C Bus" value="1" options={options} onChange={rs.fn()} />);
    expect(screen.getByText("I2C Bus")).toBeInTheDocument();
    expect(screen.getByText("Bus 1")).toBeInTheDocument();
    expect(screen.getByText("Bus 2")).toBeInTheDocument();
  });

  it("fires onChange with the selected value", () => {
    const onChange = rs.fn();
    render(<SelectField label="I2C Bus" value="1" options={options} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "2" } });
    expect(onChange).toHaveBeenCalledWith("2");
  });

  it("renders nothing when hidden", () => {
    const { container } = render(
      <SelectField label="I2C Bus" value="1" options={options} hidden onChange={rs.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
