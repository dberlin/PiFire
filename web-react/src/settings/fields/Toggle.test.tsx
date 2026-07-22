// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Toggle } from "./Toggle";

describe("Toggle", () => {
  it("reflects checked state via aria-pressed and calls onChange on click", () => {
    const onChange = vi.fn();
    render(<Toggle label="Enable" checked={true} onChange={onChange} />);
    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(button);
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("toggles aria-pressed when checked is false", () => {
    const onChange = vi.fn();
    render(<Toggle label="Disabled" checked={false} onChange={onChange} />);
    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(button);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("renders label", () => {
    const onChange = vi.fn();
    render(<Toggle label="My Toggle" checked={false} onChange={onChange} />);
    expect(screen.getByText("My Toggle")).toBeInTheDocument();
  });

  it("inverts the checked prop on click", () => {
    const onChange = vi.fn();
    const { rerender } = render(<Toggle label="Test" checked={true} onChange={onChange} />);
    let button = screen.getByRole("button");
    fireEvent.click(button);
    expect(onChange).toHaveBeenCalledWith(false);

    rerender(<Toggle label="Test" checked={false} onChange={onChange} />);
    button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-pressed", "false");
  });
});
