import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";

import { NumberField } from "../../../../../src/components/settings/fields/NumberField";

describe("NumberField integer coercion", () => {
  it("rounds a typed fraction when the field is integer-backed", () => {
    // The backend is strict: 2.5 into an int field is refused on save, with a
    // message the user cannot connect to what they typed.
    const onChange = rs.fn();
    render(<NumberField label="P-Mode" value={2} onChange={onChange} integer min={0} max={9} />);

    const input = screen.getByRole("spinbutton");
    // Matches the sibling clamp tests below: the target value rides on the
    // blur event itself. A separate fireEvent.change first would let React's
    // controlled-input reconciliation revert the DOM value back to `value`
    // before blur ever reads it, since this onChange mock never calls
    // setState — that would make the assertion pass or fail independent of
    // NumberField's own rounding logic.
    fireEvent.blur(input, { target: { value: "2.5" } });

    expect(onChange).toHaveBeenCalledWith(3);
  });

  it("leaves a fraction alone when the field is not integer-backed", () => {
    const onChange = rs.fn();
    render(<NumberField label="PB" value={60} onChange={onChange} />);

    const input = screen.getByRole("spinbutton");
    fireEvent.blur(input, { target: { value: "60.5" } });

    expect(onChange).not.toHaveBeenCalled();
  });

  it("still clamps an integer field to its bounds", () => {
    const onChange = rs.fn();
    render(<NumberField label="P-Mode" value={2} onChange={onChange} integer min={0} max={9} />);

    const input = screen.getByRole("spinbutton");
    fireEvent.blur(input, { target: { value: "99" } });

    expect(onChange).toHaveBeenCalledWith(9);
  });
});
