import { useState } from "react";
import { type TargetAction, type TargetEdit, targetRange } from "../../helpers/notify/notifyState";

interface Props {
  open: boolean;
  probeName: string;
  isPrimary: boolean;
  units: "F" | "C";
  /** Seeded from the live socket payload via readTargetEdit(probe). */
  initial: TargetEdit;
  saving: boolean;
  error: string | null;
  onSubmit(edit: TargetEdit): void;
  /** Closes. Writes NOTHING -- see the note on the Cancel button below. */
  onCancel(): void;
}

// One choice, not two checkboxes: the backend runs `if shutdown: ... elif
// keep_warm: ...` (notify/notifications.py:142-159), so a UI that lets you tick
// both is offering a state it will not honour.
const ACTIONS: { value: TargetAction; label: string }[] = [
  { value: "none", label: "No action" },
  { value: "shutdown", label: "Shutdown PiFire" },
  { value: "keepWarm", label: "Start Keep Warm" },
];

// Set a target temperature on one probe and choose what happens when it is
// reached. Port of the Flask notify modal's Target Temperature card
// (blueprints/dash/templates/default/_macro_dash_default.html:151-201).
export function ProbeNotifyModal({
  open,
  probeName,
  isPrimary,
  units,
  initial,
  saving,
  error,
  onSubmit,
  onCancel,
}: Props) {
  const [edit, setEdit] = useState<TargetEdit>(initial);
  const [invalid, setInvalid] = useState(false);
  // Re-seed from `initial` whenever the identity of what we're editing changes,
  // adjusted synchronously during render (React's recommended pattern for
  // deriving state from prop changes) rather than in an effect -- the React
  // Compiler lint forbids setState-in-effect here. Same shape as
  // SetpointEntry.tsx:14-23.
  const seedKey = `${open}|${probeName}|${initial.enabled}|${initial.target}|${initial.action}`;
  const [prevSeedKey, setPrevSeedKey] = useState(seedKey);
  if (seedKey !== prevSeedKey) {
    setPrevSeedKey(seedKey);
    if (open) {
      setEdit(initial);
      setInvalid(false);
    }
  }
  if (!open) return null;

  const { min, max } = targetRange(isPrimary, units);
  const setTarget = (raw: string) => {
    const n = Number(raw);
    const clamped = Math.min(max, Math.max(min, Math.round(Number.isFinite(n) ? n : 0)));
    setInvalid(false);
    setEdit((e) => ({ ...e, target: clamped }));
  };
  const submit = () => {
    // A target of 0 with the notification armed is not a no-op: the entry's
    // condition is "equal_above" (common/defaults.py:524), so it fires on the
    // next control pass -- and with Shutdown PiFire chosen, that is an
    // immediate shutdown. Flask's slider allows it; this deliberately does not.
    if (edit.enabled && edit.target <= 0) {
      setInvalid(true);
      return;
    }
    onSubmit(edit);
  };
  const alert =
    error ?? (invalid ? `Set a target above 0°${units} to arm the notification.` : null);

  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">{probeName} Notifications</div>

        <label className="pf-notify-switch">
          <input
            type="checkbox"
            checked={edit.enabled}
            onChange={(e) => {
              setInvalid(false);
              setEdit((v) => ({ ...v, enabled: e.target.checked }));
            }}
          />
          <span>Notify me at the target temperature</span>
        </label>

        <div className="pf-notify-row">
          <label className="pf-notify-num">
            <span>Target</span>
            <input
              type="number"
              min={min}
              max={max}
              value={edit.target}
              disabled={!edit.enabled}
              onChange={(e) => setTarget(e.target.value)}
            />
            <span className="pf-notify-unit">°{units}</span>
          </label>
        </div>
        <input
          className="pf-setpoint-slider"
          type="range"
          aria-label={`Target temperature (°${units})`}
          min={min}
          max={max}
          step={1}
          value={edit.target}
          disabled={!edit.enabled}
          onChange={(e) => setTarget(e.target.value)}
        />

        {/* Only a food probe gets these: _macro_dash_default.html:188-198 hides
            them for the Primary probe, whose "stop when hot enough" control is
            the setpoint. */}
        {!isPrimary && (
          <fieldset className="pf-notify-actions" disabled={!edit.enabled}>
            <legend>When it is reached</legend>
            {ACTIONS.map((a) => (
              <label key={a.value}>
                <input
                  type="radio"
                  name="pf-notify-action"
                  value={a.value}
                  checked={edit.action === a.value}
                  onChange={() => setEdit((v) => ({ ...v, action: a.value }))}
                />
                <span>{a.label}</span>
              </label>
            ))}
          </fieldset>
        )}

        {alert && (
          <div className="pf-notify-alert" role="alert">
            {alert}
          </div>
        )}

        <div className="pf-modal-actions">
          {/* Closes without writing. Flask's same-looking button POSTs a wipe of
              the target AND both limit alerts (dash_default.js:803-831); here,
              turning a notification off is the master switch. */}
          <button className="pf-modal-btn" onClick={onCancel}>
            Cancel
          </button>
          <button className="pf-modal-btn accent" onClick={submit} disabled={saving}>
            {saving ? "Saving…" : "Set"}
          </button>
        </div>
      </div>
    </div>
  );
}
