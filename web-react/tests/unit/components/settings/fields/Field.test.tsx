import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";

import { Field } from "../../../../../src/components/settings/fields/Field";

describe("Field", () => {
  it("describes the control with the hint id when only a hint renders", () => {
    render(
      <Field label="Duration" hint="Seconds to run">
        {({ id, describedBy, invalid }) => (
          <input id={id} aria-describedby={describedBy} aria-invalid={invalid} />
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
        {({ id, describedBy, invalid }) => (
          <input id={id} aria-describedby={describedBy} aria-invalid={invalid} />
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
        {({ id, describedBy }) => <input id={id} aria-describedby={describedBy} />}
      </Field>,
    );
    expect(screen.getByLabelText("Duration").getAttribute("aria-describedby")).toBeNull();
  });

  it("puts the error in an alert region", () => {
    render(
      <Field label="Duration" error="Must be an integer">
        {({ id }) => <input id={id} />}
      </Field>,
    );
    expect(screen.getByRole("alert").textContent).toBe("Must be an integer");
  });

  // The label is a sibling connected by htmlFor, not a wrapper -- this is
  // what makes getByLabelText resolve through the real association instead
  // of an aria-label the render prop happened to set. Without id={id} wired
  // to the control, this test would fail with "Unable to find a label..."
  it("associates the label with the control via htmlFor/id, not by wrapping it", () => {
    render(<Field label="Duration">{({ id }) => <input id={id} />}</Field>);
    const label = screen.getByText("Duration");
    expect(label.tagName).toBe("LABEL");
    const input = screen.getByLabelText("Duration");
    expect(label.getAttribute("for")).toBe(input.id);
    // A wrapper would put the input inside the label; htmlFor puts it
    // beside it.
    expect(label.contains(input)).toBe(false);
  });
});
