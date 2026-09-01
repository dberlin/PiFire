import { useState } from "react";

import {
  type LimitAction,
  type LimitEdit,
  type NotifyEdit,
  type TargetAction,
  type TargetEdit,
  targetRange,
} from "../../helpers/notify/notifyState";
import { useDismissOnEscape } from "../../helpers/useDismissOnEscape";

interface Props {
  open: boolean;
  probeName: string;
  isPrimary: boolean;
  units: "F" | "C";
  /** Seeded from the live socket payload via readNotifyEdit(probe). */
  initial: NotifyEdit;
  saving: boolean;
  error: string | null;
  onSubmit(edit: NotifyEdit): void;
  /** Closes. Writes NOTHING -- see the note on the Cancel button below. */
  onCancel(): void;
}

interface Choice<A extends string> {
  value: A;
  label: string;
}

// One choice, not two checkboxes: the backend runs `if shutdown ... elif
// keep_warm ... elif reignite` (notify/notifications.py:157-174), so a UI that
// lets you tick both is offering a state it will not honour. Flask enforces the
// same exclusion with JavaScript that unchecks the other box
// (_macro_dash_default.html:294-308); a radio group cannot express it at all.
const TARGET_ACTIONS: Choice<TargetAction>[] = [
  { value: "none", label: "No action" },
  { value: "shutdown", label: "Shutdown PiFire" },
  { value: "keepWarm", label: "Start Keep Warm" },
];

// No re-ignite on the high limit: the socket payload publishes no
// highLimitReignite (blueprints/mobile/socket_io.py:781-787), so there is no
// way to show that state back, and re-igniting because the grill is too HOT is
// not a thing anyone wants.
const HIGH_ACTIONS: Choice<LimitAction>[] = [
  { value: "none", label: "No action" },
  { value: "shutdown", label: "Shutdown PiFire" },
];

const LOW_ACTIONS: Choice<LimitAction>[] = [
  ...HIGH_ACTIONS,
  { value: "reignite", label: "Attempt Re-ignite" },
];

type SectionKey = "target" | "high" | "low";

// Set a target temperature and high/low limit alerts on one probe, and choose
// what happens when each is reached. Port of the Flask notify modal's three
// accordion cards (_macro_dash_default.html:150-320).
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
  const [edit, setEdit] = useState<NotifyEdit>(initial);
  const [invalid, setInvalid] = useState<SectionKey | null>(null);
  // Re-seed from `initial` whenever the identity of what we're editing changes,
  // adjusted synchronously during render (React's recommended pattern for
  // deriving state from prop changes) rather than in an effect -- the React
  // Compiler lint forbids setState-in-effect here. Same shape as
  // SetpointEntry.tsx:14-23.
  const seedKey = `${open}|${probeName}|${JSON.stringify(initial)}`;
  const [prevSeedKey, setPrevSeedKey] = useState(seedKey);
  if (seedKey !== prevSeedKey) {
    setPrevSeedKey(seedKey);
    if (open) {
      setEdit(initial);
      setInvalid(null);
    }
  }
  useDismissOnEscape(open, onCancel);

  if (!open) return null;

  const { min, max } = targetRange(isPrimary, units);
  const clamp = (raw: string) => {
    const n = Number(raw);
    return Math.min(max, Math.max(min, Math.round(Number.isFinite(n) ? n : 0)));
  };
  const patchTarget = (over: Partial<TargetEdit>) => {
    setInvalid(null);
    setEdit((e) => ({ ...e, target: { ...e.target, ...over } }));
  };
  const patchHigh = (over: Partial<LimitEdit>) => {
    setInvalid(null);
    setEdit((e) => ({ ...e, high: { ...e.high, ...over } }));
  };
  const patchLow = (over: Partial<LimitEdit>) => {
    setInvalid(null);
    setEdit((e) => ({ ...e, low: { ...e.low, ...over } }));
  };

  const submit = () => {
    // A limit of 0 with the alert armed is not a no-op. The high entry's
    // condition is "equal_above" (common/defaults.py:542), so 0 fires on the
    // next control pass -- and with Shutdown PiFire chosen, that is an immediate
    // shutdown. The low entry's is "equal_below" (:548), so 0 can never fire at
    // all: an alert that silently does nothing. Flask's sliders allow both; this
    // deliberately does not.
    const bad = (
      [
        ["target", edit.target],
        ["high", edit.high],
        ["low", edit.low],
      ] as const
    ).find(([, section]) => section.enabled && section.target <= 0);
    if (bad !== undefined) {
      setInvalid(bad[0]);
      return;
    }
    onSubmit(edit);
  };

  const INVALID: Record<SectionKey, string> = {
    target: `Set a target above 0°${units} to arm the notification.`,
    high: `Set a high limit above 0°${units} to arm the alert.`,
    low: `Set a low limit above 0°${units} to arm the alert.`,
  };
  const alert = error ?? (invalid === null ? null : INVALID[invalid]);

  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">{probeName} Notifications</div>

        <NotifySection
          switchLabel="Notify me at the target temperature"
          name="Target"
          units={units}
          min={min}
          max={max}
          edit={edit.target}
          onEnabled={(enabled) => patchTarget({ enabled })}
          onTarget={(raw) => patchTarget({ target: clamp(raw) })}
          onAction={(action) => patchTarget({ action })}
          radioName="notify-target-action"
          legend="When it is reached"
          // Only a food probe gets these: _macro_dash_default.html:188-198 hides
          // them for the Primary probe, whose "stop when hot enough" control is
          // the setpoint.
          actions={isPrimary ? null : TARGET_ACTIONS}
        />

        {/* Both limit TEMPERATURES render for every probe, exactly as Flask does
            (_macro_dash_default.html:216-234): being told a food probe has run
            hot is useful anywhere. The ACTIONS below them are Primary-only,
            which is also Flask's shape (:238-244, :284-308) and is deliberate
            rather than inherited -- "Shutdown PiFire" on a high limit is a
            runaway-heat cutoff and "Attempt Re-ignite" on a low limit is a
            fire-out response. Both are statements about the FIRE, and only the
            primary probe measures the fire. A food probe reading low means cold
            meat, not a dead fire: arming a re-ignite there would fire the moment
            cold food went on the grate, and pre-arming `triggered` cannot help,
            because the temperature genuinely leaves the range and comes back. */}
        <NotifySection
          switchLabel="Notify me above the high limit"
          name="High limit"
          units={units}
          min={min}
          max={max}
          edit={edit.high}
          onEnabled={(enabled) => patchHigh({ enabled })}
          onTarget={(raw) => patchHigh({ target: clamp(raw) })}
          onAction={(action) => patchHigh({ action })}
          radioName="notify-high-action"
          legend="When it goes above the high limit"
          actions={isPrimary ? HIGH_ACTIONS : null}
        />

        <NotifySection
          switchLabel="Notify me below the low limit"
          name="Low limit"
          units={units}
          min={min}
          max={max}
          edit={edit.low}
          onEnabled={(enabled) => patchLow({ enabled })}
          onTarget={(raw) => patchLow({ target: clamp(raw) })}
          onAction={(action) => patchLow({ action })}
          radioName="notify-low-action"
          legend="When it goes below the low limit"
          actions={isPrimary ? LOW_ACTIONS : null}
        />

        {alert && (
          <div className="pf-notify-alert" role="alert">
            {alert}
          </div>
        )}

        <div className="pf-modal-actions">
          {/* Closes without writing. Flask's same-looking button POSTs a wipe of
              the target AND both limit alerts (dash_default.js:803-831); here,
              turning a notification off is its own master switch. */}
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

/** One accordion card's worth of controls: a master switch, a temperature bound
 *  to a number box and a slider, and -- where the probe is allowed one -- the
 *  single action to take when the condition is met. `edit` is structurally a
 *  LimitEdit; TargetEdit has the same three fields with a wider action union. */
function NotifySection<A extends string>({
  switchLabel,
  name,
  units,
  min,
  max,
  edit,
  onEnabled,
  onTarget,
  onAction,
  radioName,
  legend,
  actions,
}: {
  switchLabel: string;
  name: string;
  units: "F" | "C";
  min: number;
  max: number;
  edit: { enabled: boolean; target: number; action: A };
  onEnabled(enabled: boolean): void;
  onTarget(raw: string): void;
  onAction(action: A): void;
  radioName: string;
  legend: string;
  actions: Choice<A>[] | null;
}) {
  return (
    <div className="pf-notify-section">
      <label className="pf-notify-switch">
        <input
          type="checkbox"
          checked={edit.enabled}
          onChange={(e) => onEnabled(e.target.checked)}
        />
        <span>{switchLabel}</span>
      </label>

      <div className="pf-notify-row">
        <label className="pf-notify-num">
          <span>{name}</span>
          <input
            type="number"
            min={min}
            max={max}
            value={edit.target}
            disabled={!edit.enabled}
            onChange={(e) => onTarget(e.target.value)}
          />
          <span className="pf-notify-unit">°{units}</span>
        </label>
      </div>
      <input
        className="pf-setpoint-slider"
        type="range"
        aria-label={`${name} temperature (°${units})`}
        min={min}
        max={max}
        step={1}
        value={edit.target}
        disabled={!edit.enabled}
        onChange={(e) => onTarget(e.target.value)}
      />

      {actions !== null && (
        <fieldset className="pf-notify-actions" disabled={!edit.enabled}>
          <legend>{legend}</legend>
          {actions.map((a) => (
            <label key={a.value}>
              <input
                type="radio"
                name={radioName}
                value={a.value}
                checked={edit.action === a.value}
                onChange={() => onAction(a.value)}
              />
              <span>{a.label}</span>
            </label>
          ))}
        </fieldset>
      )}
    </div>
  );
}
