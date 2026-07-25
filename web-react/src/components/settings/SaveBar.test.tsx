import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SaveBar } from "./SaveBar";

afterEach(cleanup);

describe("SaveBar", () => {
  it("renders a plain Save button when idle", () => {
    render(<SaveBar onSave={() => {}} saving={false} status={{ kind: "idle" }} />);
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).not.toBeDisabled();
    expect(screen.queryByText("Saved ✓")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a disabled Saving… button while a save is in flight", () => {
    render(<SaveBar onSave={() => {}} saving={true} status={{ kind: "idle" }} />);
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  });

  it("shows the success marker when saved", () => {
    render(<SaveBar onSave={() => {}} saving={false} status={{ kind: "saved" }} />);
    expect(screen.getByText("Saved ✓")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("announces the rejection message, hides the success marker, and allows a retry", () => {
    render(
      <SaveBar
        onSave={() => {}}
        saving={false}
        status={{ kind: "error", message: "safety.maxtemp: Input should be a valid integer" }}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("safety.maxtemp: Input should be a valid integer");
    expect(screen.queryByText("Saved ✓")).toBeNull();
    // The user has to be able to fix the value and press Save again.
    expect(screen.getByRole("button", { name: "Save" })).not.toBeDisabled();
  });

  it("calls onSave exactly once per click", () => {
    const onSave = rs.fn();
    render(<SaveBar onSave={onSave} saving={false} status={{ kind: "idle" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});
