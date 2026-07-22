import { useState } from "react";
import { clampSetpoint, SETPOINT_RANGE } from "../../helpers/dashboard/health";

interface Props {
  open: boolean;
  initial: number;
  units: "F" | "C";
  onSubmit(temp: number): void;
  onCancel(): void;
}

export function SetpointEntry({ open, initial, units, onSubmit, onCancel }: Props) {
  const [temp, setTemp] = useState(() => (open ? clampSetpoint(initial, units) : initial));
  // Re-seed the slider from `initial` (clamped) whenever open/initial/units
  // change, but only while open — adjusted synchronously during render
  // (React's recommended pattern for deriving state from prop changes)
  // rather than in an effect.
  const seedKey = `${open}|${initial}|${units}`;
  const [prevSeedKey, setPrevSeedKey] = useState(seedKey);
  if (seedKey !== prevSeedKey) {
    setPrevSeedKey(seedKey);
    if (open) setTemp(clampSetpoint(initial, units));
  }
  if (!open) return null;
  const step = units === "F" ? 5 : 3;
  const bump = (d: number) => setTemp((t) => clampSetpoint(t + d, units));
  const { min, max } = SETPOINT_RANGE[units];

  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">Set Hold Temperature</div>
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
          min={min}
          max={max}
          step={step}
          value={temp}
          onChange={(e) => setTemp(clampSetpoint(Number(e.target.value), units))}
        />
        <div className="pf-modal-actions">
          <button className="pf-modal-btn" onClick={onCancel}>
            Cancel
          </button>
          <button className="pf-modal-btn accent" onClick={() => onSubmit(temp)}>
            Set Hold
          </button>
        </div>
      </div>
    </div>
  );
}
