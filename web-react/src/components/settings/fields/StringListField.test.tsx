// @vitest-environment jsdom
import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { StringListField } from "./StringListField";

describe("StringListField", () => {
  it("renders one input per value with the label", () => {
    render(<StringListField label="Locations" values={["a", "b"]} onChange={() => {}} />);
    expect(screen.getByText("Locations")).toBeInTheDocument();
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
  });
  it("editing a row emits the changed array", () => {
    const onChange = rs.fn();
    render(<StringListField label="L" values={["a", "b"]} onChange={onChange} />);
    fireEvent.change(screen.getAllByRole("textbox")[1], { target: { value: "z" } });
    expect(onChange).toHaveBeenCalledWith(["a", "z"]);
  });
  it("Add appends an empty row", () => {
    const onChange = rs.fn();
    render(<StringListField label="L" values={["a"]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /add/i }));
    expect(onChange).toHaveBeenCalledWith(["a", ""]);
  });
  it("remove drops that row", () => {
    const onChange = rs.fn();
    render(<StringListField label="L" values={["a", "b"]} onChange={onChange} />);
    fireEvent.click(screen.getAllByRole("button", { name: /remove/i })[0]);
    expect(onChange).toHaveBeenCalledWith(["b"]);
  });
});
