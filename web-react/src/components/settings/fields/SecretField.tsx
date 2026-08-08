import { useState } from "react";
import { Field } from "./Field";

/**
 * A TextField for a value that should not be readable over someone's shoulder
 * or in a shared screenshot: an API key, a token, a password. Masked on mount,
 * revealed only while the user holds it open with the toggle.
 *
 * Storage and transport are unchanged -- these values are saved and sent in
 * clear exactly as before. This hides the value on the SCREEN, which is the
 * only threat a field component can address.
 *
 * The reveal button renders alongside the input as a sibling, both outside
 * Field's <label> (Field associates the label to the input via htmlFor, not
 * by wrapping). A wrapping label would have folded the button's own text
 * into the input's accessible name -- "MQTT Password" becoming
 * "MQTT Password Show" -- which is exactly why Field never wraps.
 */
export function SecretField({
  label,
  value,
  onChange,
  hint,
  error = null,
  path,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  hint?: string;
  error?: string | null;
  path?: string;
}) {
  // Deliberately local, not a draft value: a settings tab remount (which is
  // what switching tabs does) re-masks the field.
  const [revealed, setRevealed] = useState(false);

  return (
    <Field label={label} hint={hint} error={error} path={path}>
      {({ id, describedBy, invalid }) => (
        <>
          <input
            id={id}
            className="pf-input"
            type={revealed ? "text" : "password"}
            value={value}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            onChange={(e) => onChange(e.target.value)}
            // A password manager filling the grill's InfluxDB token with the user's
            // website credentials is worse than no autofill at all.
            autoComplete="off"
            spellCheck={false}
          />
          <button
            type="button"
            className="pf-secret-toggle"
            // Named after the field so six of these on one page are distinguishable
            // to a screen reader; the visible text is a prefix of the name, which is
            // what WCAG 2.5.3 (Label in Name) asks for.
            aria-label={`${revealed ? "Hide" : "Show"} ${label}`}
            onClick={() => setRevealed((r) => !r)}
          >
            {revealed ? "Hide" : "Show"}
          </button>
        </>
      )}
    </Field>
  );
}
