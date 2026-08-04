import { useState } from "react";
import { setpointRange } from "../../helpers/dashboard/health";

interface Props {
  open: boolean;
  initial: number;
  units: "F" | "C";
  /** Headline. Defaults to the Hold-temperature wording this started life with. */
  title?: string;
  /** Submit-button label. Defaults to "Set Hold". */
  submitLabel?: string;
  /** The grill's shutdown limit (LiveState.safetyMaxTemp), in `units`. It is
   *  the ceiling for the value entered here; omitting it falls back to the
   *  fixed one setpointRange() keeps for a backend too old to send it. */
  safetyMaxTemp?: number;
  /** Floor override, for a caller whose lower bound is not the Hold floor. The
   *  ceiling is never overridable -- no caller may offer a temperature the
   *  grill would shut down at. */
  min?: number;
  /** Failure text from the caller's write, shown WITHOUT closing the modal. */
  error?: string | null;
  /** Blocks a second submit while the caller's write is in flight. */
  saving?: boolean;
  onSubmit(temp: number): void;
  onCancel(): void;
}

export function SetpointEntry({
  open,
  initial,
  units,
  title = "Set Hold Temperature",
  submitLabel = "Set Hold",
  safetyMaxTemp,
  min,
  error = null,
  saving = false,
  onSubmit,
  onCancel,
}: Props) {
  const range = setpointRange(units, safetyMaxTemp);
  const lo = min ?? range.min;
  const hi = range.max;
  const clamp = (t: number) => {
    const r = Math.round(t);
    return r < lo ? lo : r > hi ? hi : r;
  };

  const [temp, setTemp] = useState(() => (open ? clamp(initial) : initial));
  // What the text box shows while it is being typed in. `null` means "show
  // `temp`" -- an intermediate "1" on the way to "180" must survive on screen
  // even though `temp` has already clamped it to the floor, or the field is
  // untypeable (the reason NumberField clamps on blur rather than on change).
  const [draft, setDraft] = useState<string | null>(null);
  // Re-seed from `initial` when the dialog opens or its units/bounds change.
  // `initial` alone can change on every live probe frame; once open, the modal
  // owns the edit and must not replace it with those readings.
  const seedKey = `${open}|${units}|${lo}|${hi}`;
  const [prevSeedKey, setPrevSeedKey] = useState(seedKey);
  if (seedKey !== prevSeedKey) {
    setPrevSeedKey(seedKey);
    if (open) {
      setTemp(clamp(initial));
      setDraft(null);
    }
  }
  if (!open) return null;
  const step = units === "F" ? 5 : 3;
  const set = (t: number) => {
    setTemp(clamp(t));
    setDraft(null); // the slider and the steppers own the value again
  };
  const bump = (d: number) => set(temp + d);

  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">{title}</div>
        <div className="pf-setpoint-row">
          <button className="pf-step" onClick={() => bump(-step)} aria-label="decrease">
            −
          </button>
          <div className="pf-setpoint-val">
            <input
              className="pf-setpoint-input"
              type="number"
              inputMode="numeric"
              aria-label={title}
              min={lo}
              max={hi}
              value={draft ?? String(temp)}
              onChange={(e) => {
                setDraft(e.target.value);
                const typed = Number(e.target.value);
                // A half-typed or emptied box leaves the last good value
                // standing, so submitting mid-edit can never send a NaN.
                if (e.target.value.trim() !== "" && Number.isFinite(typed)) setTemp(clamp(typed));
              }}
              onBlur={() => setDraft(null)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  setDraft(null);
                  onSubmit(temp);
                }
              }}
            />
            <span>°{units}</span>
          </div>
          <button className="pf-step" onClick={() => bump(step)} aria-label="increase">
            +
          </button>
        </div>
        <input
          className="pf-setpoint-slider"
          type="range"
          min={lo}
          max={hi}
          step={step}
          value={temp}
          onChange={(e) => set(Number(e.target.value))}
        />
        {error !== null && <div className="pf-notify-alert">{error}</div>}
        <div className="pf-modal-actions">
          <button className="pf-modal-btn" onClick={onCancel}>
            Cancel
          </button>
          <button className="pf-modal-btn accent" disabled={saving} onClick={() => onSubmit(temp)}>
            {submitLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
