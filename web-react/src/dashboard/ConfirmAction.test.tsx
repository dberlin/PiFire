// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmAction } from "./ConfirmAction";

describe("ConfirmAction", () => {
  it("renders the title when open", () => {
    render(<ConfirmAction open title="Stop the cook?" onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText("Stop the cook?")).toBeInTheDocument();
  });

  it("calls onConfirm when Confirm is clicked", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmAction open title="Stop the cook?" onConfirm={onConfirm} onCancel={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalled();
  });

  it("calls onCancel when Cancel is clicked", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<ConfirmAction open title="Stop the cook?" onConfirm={vi.fn()} onCancel={onCancel} />);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("calls onCancel when the scrim is clicked", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    const { container } = render(
      <ConfirmAction open title="Stop the cook?" onConfirm={vi.fn()} onCancel={onCancel} />,
    );
    await user.click(container.querySelector(".pf-modal-scrim")!);
    expect(onCancel).toHaveBeenCalled();
  });

  it("renders nothing when open is false", () => {
    const { container } = render(
      <ConfirmAction open={false} title="Stop the cook?" onConfirm={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
