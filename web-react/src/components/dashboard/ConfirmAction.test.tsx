import { describe, expect, it, rs } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmAction } from "./ConfirmAction";

describe("ConfirmAction", () => {
  it("renders the title when open", () => {
    render(<ConfirmAction open title="Stop the cook?" onConfirm={rs.fn()} onCancel={rs.fn()} />);
    expect(screen.getByText("Stop the cook?")).toBeInTheDocument();
  });

  it("calls onConfirm when Confirm is clicked", async () => {
    const user = userEvent.setup();
    const onConfirm = rs.fn();
    render(<ConfirmAction open title="Stop the cook?" onConfirm={onConfirm} onCancel={rs.fn()} />);
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalled();
  });

  it("calls onCancel when Cancel is clicked", async () => {
    const user = userEvent.setup();
    const onCancel = rs.fn();
    render(<ConfirmAction open title="Stop the cook?" onConfirm={rs.fn()} onCancel={onCancel} />);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("calls onCancel when the scrim is clicked", async () => {
    const user = userEvent.setup();
    const onCancel = rs.fn();
    const { container } = render(
      <ConfirmAction open title="Stop the cook?" onConfirm={rs.fn()} onCancel={onCancel} />,
    );
    await user.click(container.querySelector(".pf-modal-scrim")!);
    expect(onCancel).toHaveBeenCalled();
  });

  it("renders an optional message body below the title", () => {
    render(
      <ConfirmAction
        open
        title="Delete Probe Device?"
        message="All probes associated with this device will also be deleted."
        onConfirm={rs.fn()}
        onCancel={rs.fn()}
      />,
    );
    expect(screen.getByText("Delete Probe Device?")).toBeInTheDocument();
    expect(
      screen.getByText("All probes associated with this device will also be deleted."),
    ).toBeInTheDocument();
  });

  it("keeps the message out of the bold centred title element", () => {
    // .pf-modal-title is `font: 700 20px` + centred (dashboard.css:183-187);
    // consequence copy belongs in its own slot, not jammed into the headline.
    const { container } = render(
      <ConfirmAction
        open
        title="Delete Probe Device?"
        message="All probes associated with this device will also be deleted."
        onConfirm={rs.fn()}
        onCancel={rs.fn()}
      />,
    );
    expect(container.querySelector(".pf-modal-title")).toHaveTextContent("Delete Probe Device?");
    expect(container.querySelector(".pf-modal-title")).not.toHaveTextContent(/also be deleted/);
    expect(container.querySelector(".pf-modal-message")).toHaveTextContent(/also be deleted/);
  });

  it("renders no message element when none is given", () => {
    const { container } = render(
      <ConfirmAction open title="Stop the cook?" onConfirm={rs.fn()} onCancel={rs.fn()} />,
    );
    expect(container.querySelector(".pf-modal-message")).toBeNull();
  });

  it("renders nothing when open is false", () => {
    const { container } = render(
      <ConfirmAction open={false} title="Stop the cook?" onConfirm={rs.fn()} onCancel={rs.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
