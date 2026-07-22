// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TextField } from "./TextField";

describe("TextField", () => {
  it("renders value and calls onChange on typing", () => {
    const onChange = vi.fn();
    render(<TextField label="Name" value="John" onChange={onChange} />);
    const input = screen.getByRole("textbox");
    expect(input).toHaveValue("John");
    fireEvent.change(input, { target: { value: "Jane" } });
    expect(onChange).toHaveBeenCalledWith("Jane");
  });

  it("renders empty string value", () => {
    const onChange = vi.fn();
    render(<TextField label="Empty" value="" onChange={onChange} />);
    const input = screen.getByRole("textbox");
    expect(input).toHaveValue("");
  });

  it("renders label", () => {
    const onChange = vi.fn();
    render(<TextField label="My Label" value="test" onChange={onChange} />);
    expect(screen.getByText("My Label")).toBeInTheDocument();
  });

  it("calls onChange with string value", () => {
    const onChange = vi.fn();
    render(<TextField label="Input" value="" onChange={onChange} />);
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "hello" } });
    expect(onChange).toHaveBeenCalledWith("hello");
    fireEvent.change(input, { target: { value: "world" } });
    expect(onChange).toHaveBeenCalledWith("world");
  });

  it("handles special characters and spaces", () => {
    const onChange = vi.fn();
    render(<TextField label="Text" value="" onChange={onChange} />);
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Hello World! #123" } });
    expect(onChange).toHaveBeenCalledWith("Hello World! #123");
  });
});
