import { describe, expect, it } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { computeAccessibleName } from "dom-accessibility-api";
import { ColorField } from "../../../../../src/components/settings/fields/ColorField";
import { SecretField } from "../../../../../src/components/settings/fields/SecretField";
import { Select } from "../../../../../src/components/settings/fields/Select";
import { StringListField } from "../../../../../src/components/settings/fields/StringListField";
import { TextField } from "../../../../../src/components/settings/fields/TextField";
import { Toggle } from "../../../../../src/components/settings/fields/Toggle";

describe("every settings field carries a description and an error", () => {
  it("Select renders both and links them", () => {
    render(
      <Select
        label="Mode"
        value="a"
        options={[{ value: "a", label: "A" }]}
        onChange={() => {}}
        hint="Which mode to start in"
        error="Not a valid mode"
      />,
    );
    const control = screen.getByLabelText("Mode");
    const ids = (control.getAttribute("aria-describedby") ?? "").split(" ");
    expect(ids).toContain(screen.getByText("Which mode to start in").id);
    expect(ids).toContain(screen.getByText("Not a valid mode").id);
    expect(control.getAttribute("aria-invalid")).toBe("true");
  });

  it("Toggle renders an error", () => {
    render(<Toggle label="On" checked={false} onChange={() => {}} error="Refused" />);
    expect(screen.getByRole("alert").textContent).toBe("Refused");
  });

  it("TextField renders a description", () => {
    render(<TextField label="Name" value="" onChange={() => {}} hint="Shown on the dashboard" />);
    const control = screen.getByLabelText("Name");
    expect(control.getAttribute("aria-describedby")).toBe(
      screen.getByText("Shown on the dashboard").id,
    );
  });
});

// Field.test.tsx's fixtures used to set aria-label on the injected control,
// which outranks a <label> for accessible-name computation and would have
// proven the aria-describedby/aria-invalid wiring without ever proving the
// <label> actually names the control. Field now associates label to control
// via htmlFor/id (a genuine sibling relationship, not a wrapper -- see
// Field.tsx and the note below), so a field with no aria-label
// of its own resolving by its visible label text IS that proof.
describe("Field's <label> names its control by htmlFor/id association, not aria-label", () => {
  it("resolves a field by its visible label text alone", () => {
    render(<ColorField label="Line Color" value="rgb(0, 64, 255, 1)" onChange={() => {}} />);
    const control = screen.getByLabelText("Line Color");
    expect(control).toBeInTheDocument();
    expect(control.tagName).toBe("INPUT");
  });
});

// Field must not wrap its render-prop output in the <label> itself. For
// SecretField and StringListField, which render more than one element (an
// input plus a reveal button; N row inputs plus remove/add buttons), a
// wrapping <label> computes the FIRST control's accessible name from every
// descendant's text -- "MQTT Password" becomes "MQTT Password Show", and
// StringListField's rows 2+ get no name at all. getByLabelText could not see
// this: it strips nested form-control text before matching, so it kept
// resolving "MQTT Password" to the input even while a screen reader would
// announce "MQTT Password Show". These tests use getByRole(name:) and
// dom-accessibility-api's computeAccessibleName directly -- the same AccName
// computation testing-library uses for getByRole -- because that is what
// actually exposes the pollution.
describe("reveal buttons and repeated rows do not pollute a control's accessible name", () => {
  it("SecretField: the masked input's name is the field label alone", () => {
    render(<SecretField label="MQTT Password" value="s3cret" onChange={() => {}} />);
    const input = document.querySelector('input[type="password"]') as HTMLInputElement;
    expect(computeAccessibleName(input)).toBe("MQTT Password");
  });

  it("SecretField: the revealed input is findable by role and the clean name", () => {
    render(<SecretField label="MQTT Password" value="s3cret" onChange={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Show MQTT Password" }));
    expect(screen.getByRole("textbox", { name: "MQTT Password" })).toHaveValue("s3cret");
  });

  it("StringListField: every row has its own accessible name, not just the first", () => {
    render(
      <StringListField
        label="Watchdog IPs"
        values={["10.0.0.2", "10.0.0.3", "10.0.0.4"]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole("textbox", { name: "Watchdog IPs row 1" })).toHaveValue("10.0.0.2");
    expect(screen.getByRole("textbox", { name: "Watchdog IPs row 2" })).toHaveValue("10.0.0.3");
    expect(screen.getByRole("textbox", { name: "Watchdog IPs row 3" })).toHaveValue("10.0.0.4");
  });
});
