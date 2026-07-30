import { useId, useState } from "react";

/**
 * A TextField for a value that should not be readable over someone's shoulder
 * or in a shared screenshot: an API key, a token, a password. Masked on mount,
 * revealed only while the user holds it open with the toggle.
 *
 * Storage and transport are unchanged -- these values are saved and sent in
 * clear exactly as before. This hides the value on the SCREEN, which is the
 * only threat a field component can address.
 *
 * Structured with an explicit htmlFor/id pair rather than TextField's wrapping
 * <label>, because the toggle has to live in the field's third grid column: a
 * <label> wrapping both the input and the button would fold "Show" into the
 * label's text content, and every getByLabelText("MQTT Password") in the suite
 * -- and every screen reader -- would then be looking for "MQTT PasswordShow".
 */
export function SecretField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  // Deliberately local, not a draft value: a settings tab remount (which is
  // what switching tabs does) re-masks the field.
  const [revealed, setRevealed] = useState(false);
  const id = useId();

  return (
    <div className="pf-field">
      <label className="pf-field-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="pf-input"
        type={revealed ? "text" : "password"}
        value={value}
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
    </div>
  );
}
