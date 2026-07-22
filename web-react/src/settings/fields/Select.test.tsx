// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Select } from "./Select";

describe("Select", () => {
  const options = [
    { value: "small", label: "Small" },
    { value: "medium", label: "Medium" },
    { value: "large", label: "Large" },
  ];

  it("renders options and calls onChange on selection", () => {
    const onChange = vi.fn();
    render(<Select label="Size" value="small" options={options} onChange={onChange} />);
    expect(screen.getByText("Small")).toBeInTheDocument();
    expect(screen.getByText("Medium")).toBeInTheDocument();
    expect(screen.getByText("Large")).toBeInTheDocument();
    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "large" } });
    expect(onChange).toHaveBeenCalledWith("large");
  });

  it("reflects current value in the select", () => {
    const onChange = vi.fn();
    render(<Select label="Mode" value="medium" options={options} onChange={onChange} />);
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("medium");
  });

  it("renders label", () => {
    const onChange = vi.fn();
    render(<Select label="My Select" value="small" options={options} onChange={onChange} />);
    expect(screen.getByText("My Select")).toBeInTheDocument();
  });

  it("calls onChange with the selected option value", () => {
    const onChange = vi.fn();
    render(<Select label="Choice" value="small" options={options} onChange={onChange} />);
    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "medium" } });
    expect(onChange).toHaveBeenCalledWith("medium");
    fireEvent.change(select, { target: { value: "large" } });
    expect(onChange).toHaveBeenCalledWith("large");
  });
});
