import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ActionMenu, type MenuItem } from "../../../../src/components/dashboard/ActionMenu";

afterEach(cleanup);

const ITEMS: MenuItem[] = [
  { label: "Prime 10g", value: "10:stop" },
  { label: "Prime 25g", value: "25:stop" },
];

describe("ActionMenu", () => {
  it("renders nothing while closed", () => {
    const { container } = render(
      <ActionMenu open={false} title="Prime" items={ITEMS} onPick={rs.fn()} onCancel={rs.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the title and one button per item, in order", () => {
    render(<ActionMenu open title="Prime" items={ITEMS} onPick={rs.fn()} onCancel={rs.fn()} />);
    expect(screen.getByText("Prime")).toBeInTheDocument();
    const labels = ITEMS.map((i) => screen.getByRole("button", { name: i.label }));
    expect(labels).toHaveLength(2);
  });

  it("reports the picked item's value", () => {
    const onPick = rs.fn();
    render(<ActionMenu open title="Prime" items={ITEMS} onPick={onPick} onCancel={rs.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Prime 25g" }));
    expect(onPick).toHaveBeenCalledWith("25:stop");
  });

  it("cancels on the Cancel button, on a scrim click and on Escape", () => {
    const onCancel = rs.fn();
    const { container } = render(
      <ActionMenu open title="Prime" items={ITEMS} onPick={rs.fn()} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);

    const scrim = container.querySelector(".pf-modal-scrim");
    expect(scrim).not.toBeNull();
    fireEvent.click(scrim!);
    expect(onCancel).toHaveBeenCalledTimes(2);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(3);
  });

  it("does not cancel when the panel itself is clicked", () => {
    const onCancel = rs.fn();
    const { container } = render(
      <ActionMenu open title="Prime" items={ITEMS} onPick={rs.fn()} onCancel={onCancel} />,
    );
    const panel = container.querySelector(".pf-modal");
    expect(panel).not.toBeNull();
    fireEvent.click(panel!);
    expect(onCancel).not.toHaveBeenCalled();
  });
});
