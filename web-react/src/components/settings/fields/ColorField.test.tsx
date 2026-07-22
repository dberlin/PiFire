import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { ColorField } from "./ColorField";

describe("ColorField", () => {
  it("renders the label", () => {
    const onChange = rs.fn();
    render(<ColorField label="Pick Color" value="rgb(0, 64, 255, 1)" onChange={onChange} />);
    expect(screen.getByText("Pick Color")).toBeInTheDocument();
  });

  it("input's value is the hex of the stored string", () => {
    const onChange = rs.fn();
    const { container } = render(
      <ColorField label="Color" value="rgb(255, 138, 43, 1)" onChange={onChange} />,
    );
    const input = container.querySelector('input[type="color"]') as HTMLInputElement;
    expect(input.value).toBe("#ff8a2b");
  });

  it("onChange called with rgb string when input changes", () => {
    const onChange = rs.fn();
    const { container } = render(
      <ColorField label="Color" value="rgb(0, 0, 0, 1)" onChange={onChange} />,
    );
    const input = container.querySelector('input[type="color"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "#ff8a2b" } });
    expect(onChange).toHaveBeenCalledWith("rgb(255, 138, 43, 1)");
  });
});
