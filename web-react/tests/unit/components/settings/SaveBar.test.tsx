import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { SaveBar } from "../../../../src/components/settings/SaveBar";
import { SettingsFieldErrorsProvider } from "../../../../src/helpers/settings/fieldErrorContext";

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

  // No field on screen claims any settings path outside a
  // SettingsFieldErrorsProvider, so every per-field error the context knows
  // about falls through to the bar. This is the "no provider at all" case;
  // fieldErrorContext.test.tsx covers claimed-vs-unmatched with real fields.
  it("renders no field-error alerts without a SettingsFieldErrorsProvider", () => {
    render(<SaveBar onSave={() => {}} saving={false} status={{ kind: "idle" }} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("names every unclaimed field error inside a SettingsFieldErrorsProvider", () => {
    const errors = [
      { path: "safety.maxtemp", message: "Input should be a valid integer" },
      { path: "startup.duration", message: "Input should be greater than 0" },
    ];
    render(
      <SettingsFieldErrorsProvider errors={errors}>
        <SaveBar onSave={() => {}} saving={false} status={{ kind: "idle" }} />
      </SettingsFieldErrorsProvider>,
    );
    expect(screen.getAllByRole("alert").map((n) => n.textContent)).toEqual([
      "safety.maxtemp: Input should be a valid integer",
      "startup.duration: Input should be greater than 0",
    ]);
  });
});
