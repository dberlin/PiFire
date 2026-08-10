import { useState } from "react";
import { saveProfile, tunerErrorText } from "../../helpers/tuner/tunerApi";
import type { ProfileInput, SavedProfile } from "../../helpers/contracts/operations.gen";
import "./tuner.css";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/**
 * Name the tuned profile and save it, ported from index.html's
 * tunerAddProfile form (the "Save & Apply" / "Save Only" pair).
 *
 * The coefficients are shown read-only: they are computed, not typed, and an
 * editable field would invite someone to "fix" a number the maths produced.
 * Save & Apply attaches the profile to the probe that was being tuned; Save
 * Only stores it for later selection. A refusal (the probe vanished from the
 * map between tuning and saving) is rendered in place -- the parent is told
 * only about a real save.
 */
export function ProfileForm({
  coefficients,
  probeLabel,
  onSaved,
}: {
  coefficients: Pick<ProfileInput, "a" | "b" | "c">;
  probeLabel: string;
  onSaved: (saved: SavedProfile) => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const canSave = name.trim() !== "" && !saving;

  async function save(applyTo: string | null) {
    setSaving(true);
    setError(null);
    const result = await saveProfile(
      { name: name.trim(), ...coefficients, apply_to: applyTo },
      BASE_URL,
    );
    setSaving(false);
    if (result.ok && result.data) {
      onSaved(result.data);
    } else {
      setError(tunerErrorText(result));
    }
  }

  return (
    <div className="pf-tuner-profile">
      <label className="pf-tuner-field-label" htmlFor="tuner-profile-name">
        Name
      </label>
      <input
        id="tuner-profile-name"
        className="pf-tuner-input"
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />

      {(["A", "B", "C"] as const).map((key) => (
        <div className="pf-tuner-coeff" key={key}>
          <label className="pf-tuner-field-label" htmlFor={`tuner-coeff-${key}`}>
            {key}
          </label>
          <input
            id={`tuner-coeff-${key}`}
            className="pf-tuner-input"
            type="text"
            readOnly
            value={String(coefficients[key.toLowerCase() as "a" | "b" | "c"])}
          />
        </div>
      ))}

      {error && (
        <p className="pf-tuner-error" role="alert">
          {error}
        </p>
      )}

      <div className="pf-tuner-profile-actions">
        <button
          type="button"
          className="pf-tuner-btn"
          disabled={!canSave}
          onClick={() => void save(probeLabel)}
        >
          Save &amp; Apply
        </button>
        <button
          type="button"
          className="pf-tuner-btn"
          disabled={!canSave}
          onClick={() => void save(null)}
        >
          Save Only
        </button>
      </div>
    </div>
  );
}
