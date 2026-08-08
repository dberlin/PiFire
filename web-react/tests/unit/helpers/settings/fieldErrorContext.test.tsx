import { describe, expect, it } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { Field } from "../../../../src/components/settings/fields/Field";
import { SaveBar } from "../../../../src/components/settings/SaveBar";
import { SettingsFieldErrorsProvider } from "../../../../src/helpers/settings/fieldErrorContext";

const ERRORS = [
  { path: "startup.duration", message: "Input should be a valid integer" },
  { path: "startup.hidden_one", message: "Input should be greater than 0" },
];

function Harness({ showHidden }: { showHidden: boolean }) {
  return (
    <SettingsFieldErrorsProvider errors={ERRORS}>
      <Field label="Duration" path="startup.duration">
        {({ describedBy }) => <input aria-label="Duration" aria-describedby={describedBy} />}
      </Field>
      {showHidden && (
        <Field label="Hidden" path="startup.hidden_one">
          {({ describedBy }) => <input aria-label="Hidden" aria-describedby={describedBy} />}
        </Field>
      )}
      <SaveBar onSave={() => {}} saving={false} status={{ kind: "idle" }} />
    </SettingsFieldErrorsProvider>
  );
}

describe("SettingsFieldErrorsProvider", () => {
  it("renders a mounted field's error inline, not in the save bar", () => {
    render(<Harness showHidden={true} />);
    // Both fields are on screen, so both errors are claimed and the bar adds none.
    expect(screen.getAllByRole("alert").map((n) => n.textContent)).toEqual([
      "Input should be a valid integer",
      "Input should be greater than 0",
    ]);
  });

  it("surfaces an unmounted field's error in the save bar instead", () => {
    render(<Harness showHidden={false} />);
    const alerts = screen.getAllByRole("alert").map((n) => n.textContent);
    expect(alerts).toContain("Input should be a valid integer");
    // Nothing on screen can display the hidden path, so the bar names it.
    expect(alerts).toContain("startup.hidden_one: Input should be greater than 0");
  });

  it("releases a claim when the field unmounts", () => {
    function Toggler() {
      const [show, setShow] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setShow(false)}>
            hide
          </button>
          <Harness showHidden={show} />
        </>
      );
    }
    render(<Toggler />);
    expect(
      screen.getAllByRole("alert").some((n) => n.textContent?.startsWith("startup.hidden_one:")),
    ).toBe(false);
    // fireEvent.click, not the DOM's raw .click(): the release runs in a
    // passive-effect cleanup, and only fireEvent's act() wrapping flushes
    // that before the assertion below reads the DOM.
    fireEvent.click(screen.getByText("hide"));
    expect(
      screen.getAllByRole("alert").some((n) => n.textContent?.startsWith("startup.hidden_one:")),
    ).toBe(true);
  });
});
