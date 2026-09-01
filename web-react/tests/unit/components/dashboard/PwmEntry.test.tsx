import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PwmEntry } from "../../../../src/components/dashboard/PwmEntry";

afterEach(cleanup);

describe("PwmEntry", () => {
  it("calls onCancel on Escape", async () => {
    const user = userEvent.setup();
    const onCancel = rs.fn();
    render(<PwmEntry open initial={40} onSubmit={rs.fn()} onCancel={onCancel} />);
    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when closed", () => {
    const { container } = render(
      <PwmEntry open={false} initial={50} onSubmit={rs.fn()} onCancel={rs.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("seeds from the current duty and submits the chosen value", () => {
    const onSubmit = rs.fn();
    render(<PwmEntry open initial={40} onSubmit={onSubmit} onCancel={rs.fn()} />);
    const slider = screen.getByRole("slider", { name: /fan duty/i });
    expect(slider).toHaveValue("40");
    fireEvent.change(slider, { target: { value: "75" } });
    fireEvent.click(screen.getByRole("button", { name: /set/i }));
    expect(onSubmit).toHaveBeenCalledWith(75);
  });

  it("cancels without submitting", () => {
    const onSubmit = rs.fn();
    const onCancel = rs.fn();
    render(<PwmEntry open initial={40} onSubmit={onSubmit} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
