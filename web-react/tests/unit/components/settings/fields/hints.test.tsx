import { describe, expect, it, rs } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { NumberField } from "../../../../../src/components/settings/fields/NumberField";
import { Toggle } from "../../../../../src/components/settings/fields/Toggle";

describe("field hints are announced", () => {
  it("associates a NumberField's hint with its input", () => {
    render(
      <NumberField label="Sleep Timeout" value={300} onChange={rs.fn()} hint="0 = never sleep." />,
    );
    const input = screen.getByRole("spinbutton");
    const describedBy = input.getAttribute("aria-describedby");

    // The attribute alone proves nothing -- it has to resolve to the hint.
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy!)?.textContent).toBe("0 = never sleep.");
  });

  it("leaves aria-describedby off a NumberField with no hint", () => {
    render(<NumberField label="Frequency" value={25000} onChange={rs.fn()} />);
    expect(screen.getByRole("spinbutton").getAttribute("aria-describedby")).toBeNull();
  });

  it("keeps a NumberField's accessible name as the label alone, not label + hint", () => {
    render(
      <NumberField label="Sleep Timeout" value={300} onChange={rs.fn()} hint="0 = never sleep." />,
    );
    // A <label> wrapping a control folds all of its descendant text into
    // that control's accessible name -- a hint left inside would double as
    // part of the name instead of staying a separate description. Exact
    // match: any leakage of the hint text into the name fails this.
    expect(screen.getByRole("spinbutton", { name: "Sleep Timeout" })).toBeInTheDocument();
  });

  it("associates a Toggle's hint with its control", () => {
    render(
      <Toggle
        label="Extended Data Logging"
        checked={false}
        onChange={rs.fn()}
        disabled
        hint="Stop the grill to change extended-data logging"
      />,
    );
    const button = screen.getByRole("button", { name: "Extended Data Logging" });
    const describedBy = button.getAttribute("aria-describedby");

    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy!)?.textContent).toBe(
      "Stop the grill to change extended-data logging",
    );
  });
});
