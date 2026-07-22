// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SetpointEntry } from "./SetpointEntry";

describe("SetpointEntry", () => {
  it("renders the initial value clamped into range", () => {
    const { container } = render(
      <SetpointEntry open initial={1000} units="F" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(container.querySelector(".pf-setpoint-val")).toHaveTextContent("500°F");
  });

  it("steps the value up/down by the unit step, clamped at the range bounds", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <SetpointEntry open initial={498} units="F" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    const val = () => container.querySelector(".pf-setpoint-val")?.textContent;
    expect(val()).toBe("498°F");

    await user.click(screen.getByRole("button", { name: "increase" }));
    expect(val()).toBe("500°F"); // clamped at max, not 503

    for (let i = 0; i < 71; i++) {
      await user.click(screen.getByRole("button", { name: "decrease" }));
    }
    expect(val()).toBe("150°F"); // clamped at min
  });

  it("submits the current value via onSubmit", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<SetpointEntry open initial={225} units="F" onSubmit={onSubmit} onCancel={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Set Hold" }));
    expect(onSubmit).toHaveBeenCalledWith(225);
  });

  it("calls onCancel when the scrim is clicked", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    const { container } = render(
      <SetpointEntry open initial={225} units="F" onSubmit={vi.fn()} onCancel={onCancel} />,
    );
    await user.click(container.querySelector(".pf-modal-scrim")!);
    expect(onCancel).toHaveBeenCalled();
  });
});
