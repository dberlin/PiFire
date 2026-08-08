import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { Field } from "../../../../../src/components/settings/fields/Field";

describe("Field", () => {
  it("describes the control with the hint id when only a hint renders", () => {
    render(
      <Field label="Duration" hint="Seconds to run">
        {({ describedBy, invalid }) => (
          <input aria-label="Duration" aria-describedby={describedBy} aria-invalid={invalid} />
        )}
      </Field>,
    );
    const input = screen.getByLabelText("Duration");
    const hint = screen.getByText("Seconds to run");
    expect(input.getAttribute("aria-describedby")).toBe(hint.id);
    expect(input.getAttribute("aria-invalid")).toBeNull();
  });

  it("describes the control with both ids and marks it invalid when both render", () => {
    render(
      <Field label="Duration" hint="Seconds to run" error="Must be an integer">
        {({ describedBy, invalid }) => (
          <input aria-label="Duration" aria-describedby={describedBy} aria-invalid={invalid} />
        )}
      </Field>,
    );
    const input = screen.getByLabelText("Duration");
    const ids = (input.getAttribute("aria-describedby") ?? "").split(" ");
    expect(ids).toContain(screen.getByText("Seconds to run").id);
    expect(ids).toContain(screen.getByText("Must be an integer").id);
    expect(input.getAttribute("aria-invalid")).toBe("true");
  });

  it("references no id at all when neither renders", () => {
    render(
      <Field label="Duration">
        {({ describedBy }) => <input aria-label="Duration" aria-describedby={describedBy} />}
      </Field>,
    );
    expect(screen.getByLabelText("Duration").getAttribute("aria-describedby")).toBeNull();
  });

  it("puts the error in an alert region", () => {
    render(
      <Field label="Duration" error="Must be an integer">
        {() => <input aria-label="Duration" />}
      </Field>,
    );
    expect(screen.getByRole("alert").textContent).toBe("Must be an integer");
  });
});
