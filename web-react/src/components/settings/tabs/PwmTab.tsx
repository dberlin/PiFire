import { useState } from "react";
import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";
import { type RangeProfileColumn, RangeProfileTable } from "../RangeProfileTable";

const DEFAULT_PWM_TEMPS = [3, 7, 10, 15];
const DEFAULT_PWM_PROFILES: Record<string, number>[] = [
  { duty_cycle: 20 },
  { duty_cycle: 35 },
  { duty_cycle: 50 },
  { duty_cycle: 75 },
  { duty_cycle: 100 },
];

type Pwm = {
  pwm_control: boolean;
  update_time: number;
  min_duty_cycle: number;
  max_duty_cycle: number;
  frequency: number;
  temp_range_list: number[];
  profiles: Record<string, number>[];
};

function readPwm(settings: Settings): Pwm {
  const p = settings.pwm ?? {};
  return {
    pwm_control: !!p.pwm_control,
    update_time: p.update_time ?? 10,
    min_duty_cycle: p.min_duty_cycle ?? 20,
    max_duty_cycle: p.max_duty_cycle ?? 100,
    frequency: p.frequency ?? 100,
    // Cloned (never aliased into the widget's onChange arrays) — the widget
    // emits fresh arrays on edit, but our local state must not share
    // references with `settings`.
    temp_range_list: structuredClone((p.temp_range_list ?? DEFAULT_PWM_TEMPS) as number[]),
    profiles: structuredClone((p.profiles ?? DEFAULT_PWM_PROFILES) as Record<string, number>[]),
  };
}

export function PwmTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving } = useSaveSettings();
  const [pwm, setPwm] = useState<Pwm>(() => readPwm(settings));
  const [saved, setSaved] = useState(false);

  // Re-sync from the loader on revalidation via render-phase adjustment (the
  // repo's house style — NOT a useEffect; the React Compiler lint rule
  // `react-hooks/set-state-in-effect` rejects setState-in-effect, and Task 1's
  // Dashboard cook-timer established this `prev`-compare pattern. Do NOT suppress.)
  const [prevSettings, setPrevSettings] = useState(settings);
  if (settings !== prevSettings) {
    setPrevSettings(settings);
    setPwm(readPwm(settings));
  }

  const set = <K extends keyof Pwm>(k: K, v: Pwm[K]) => setPwm((s) => ({ ...s, [k]: v }));

  // Column min/max come from the tab's CURRENT local values so a duty-cycle
  // edit clamps against whatever is on screen (including an un-saved
  // min/max edit), not the last-saved settings.
  const DUTY_COLUMNS: RangeProfileColumn[] = [
    {
      key: "duty_cycle",
      label: "Duty cycle",
      suffix: "%",
      min: pwm.min_duty_cycle,
      max: pwm.max_duty_cycle,
    },
  ];
  const units = settings.globals?.units === "C" ? "°C" : "°F";

  const onSave = async () => {
    let d: object = {};
    // pwm.temp_range_list and pwm.profiles ride the same delta wholesale
    // (plan ruling: single Save per tab, existing ["settings_update"] flag
    // kept) — they're already keys of `pwm`, so this loop covers them too.
    for (const [k, v] of Object.entries(pwm)) d = setPath(d, `pwm.${k}`, v);
    setSaved(await save(d, ["settings_update"])); // control loop must re-read pwm
  };

  return (
    <Section title="PWM Fan">
      <Toggle
        label="PWM Control"
        checked={pwm.pwm_control}
        onChange={(v) => set("pwm_control", v)}
      />
      <NumberField
        label="Update Time"
        value={pwm.update_time}
        onChange={(v) => set("update_time", v)}
        min={1}
        suffix="s"
      />
      <NumberField
        label="Min Duty Cycle"
        value={pwm.min_duty_cycle}
        onChange={(v) => set("min_duty_cycle", v)}
        min={0}
        max={100}
        suffix="%"
      />
      <NumberField
        label="Max Duty Cycle"
        value={pwm.max_duty_cycle}
        onChange={(v) => set("max_duty_cycle", v)}
        min={0}
        max={100}
        suffix="%"
      />
      <NumberField
        label="Frequency"
        value={pwm.frequency}
        onChange={(v) => set("frequency", v)}
        min={1}
        suffix="Hz"
      />
      <RangeProfileTable
        boundaries={pwm.temp_range_list}
        profiles={pwm.profiles}
        columns={DUTY_COLUMNS}
        rangeHeader="ΔT range"
        unit={units}
        onChange={(temp_range_list, profiles) =>
          setPwm((s) => ({ ...s, temp_range_list, profiles }))
        }
      />
      <div className="pf-settings-actions">
        <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>
          {saving ? "Saving…" : "Save"}
        </button>
        {saved && <span className="pf-settings-saved">Saved ✓</span>}
      </div>
    </Section>
  );
}
