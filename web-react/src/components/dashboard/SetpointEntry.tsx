import { useState } from "react";
import { SETPOINT_RANGE } from "../../helpers/dashboard/health";

interface Props {
  open: boolean;
  initial: number;
  units: "F" | "C";
  /** Headline. Defaults to the Hold-temperature wording this started life with. */
  title?: string;
  /** Submit-button label. Defaults to "Set Hold". */
  submitLabel?: string;
  /** Bounds override. Flask's startup hold prompt runs a WIDER range than the
   *  Hold setpoint does -- 125-600 °F / 50-260 °C
   *  (_macro_control_panel.html:236,238) against SETPOINT_RANGE's 150-500 /
   *  65-260 -- so the two callers cannot share one constant. */
  min?: number;
  max?: number;
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
  min,
  max,
  error = null,
  saving = false,
  onSubmit,
  onCancel,
}: Props) {
  const lo = min ?? SETPOINT_RANGE[units].min;
  const hi = max ?? SETPOINT_RANGE[units].max;
  const clamp = (t: number) => {
    const r = Math.round(t);
    return r < lo ? lo : r > hi ? hi : r;
  };

  const [temp, setTemp] = useState(() => (open ? clamp(initial) : initial));
  // Re-seed the slider from `initial` (clamped) whenever open/initial/units or
  // the bounds change, but only while open — adjusted synchronously during
  // render (React's recommended pattern for deriving state from prop changes)
  // rather than in an effect.
  const seedKey = `${open}|${initial}|${units}|${lo}|${hi}`;
  const [prevSeedKey, setPrevSeedKey] = useState(seedKey);
  if (seedKey !== prevSeedKey) {
    setPrevSeedKey(seedKey);
    if (open) setTemp(clamp(initial));
  }
  if (!open) return null;
  const step = units === "F" ? 5 : 3;
  const bump = (d: number) => setTemp((t) => clamp(t + d));

  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">{title}</div>
        <div className="pf-setpoint-row">
          <button className="pf-step" onClick={() => bump(-step)} aria-label="decrease">
            −
          </button>
          <div className="pf-setpoint-val">
            {temp}
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
          onChange={(e) => setTemp(clamp(Number(e.target.value)))}
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
