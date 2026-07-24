import { useState } from "react";

interface Props {
  open: boolean;
  initial: number;
  onSubmit(duty: number): void;
  onCancel(): void;
}

const clampDuty = (n: number) => Math.min(100, Math.max(0, Math.round(n)));

// DC-fan duty entry for Manual mode. Modeled on SetpointEntry: the dashboard
// stage is a fixed 1280x720 box with no spare room for an inline slider, so
// duty is edited in an overlay.
export function PwmEntry({ open, initial, onSubmit, onCancel }: Props) {
  const [duty, setDuty] = useState(() => (open ? clampDuty(initial) : initial));
  // Re-seed the slider from `initial` (clamped) whenever open/initial change,
  // but only while open -- adjusted synchronously during render (React's
  // recommended pattern for deriving state from prop changes) rather than in
  // an effect (React Compiler rejects setState-in-effect; no suppressions).
  const seedKey = `${open}|${initial}`;
  const [prevSeedKey, setPrevSeedKey] = useState(seedKey);
  if (seedKey !== prevSeedKey) {
    setPrevSeedKey(seedKey);
    if (open) setDuty(clampDuty(initial));
  }
  if (!open) return null;

  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">Fan Duty Cycle</div>
        <div className="pf-setpoint-val">
          {duty}
          <span>%</span>
        </div>
        <input
          className="pf-setpoint-slider"
          type="range"
          min={0}
          max={100}
          step={1}
          value={duty}
          aria-label="Fan duty"
          onChange={(e) => setDuty(clampDuty(Number(e.target.value)))}
        />
        <div className="pf-modal-actions">
          <button className="pf-modal-btn" onClick={onCancel}>
            Cancel
          </button>
          <button className="pf-modal-btn accent" onClick={() => onSubmit(duty)}>
            Set
          </button>
        </div>
      </div>
    </div>
  );
}
