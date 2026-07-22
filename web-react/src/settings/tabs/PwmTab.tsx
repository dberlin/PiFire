import { useState } from "react";
import { useOutletContext } from "react-router";
import type { Settings } from "../settingsApi";
import { useSaveSettings } from "../useSaveSettings";
import { setPath } from "../delta";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";
import { NumberField } from "../fields/NumberField";

type Pwm = { pwm_control: boolean; update_time: number; min_duty_cycle: number; max_duty_cycle: number; frequency: number };

function readPwm(settings: Settings): Pwm {
  const p = settings.pwm ?? {};
  return {
    pwm_control: !!p.pwm_control, update_time: p.update_time ?? 10,
    min_duty_cycle: p.min_duty_cycle ?? 20, max_duty_cycle: p.max_duty_cycle ?? 100, frequency: p.frequency ?? 100,
  };
}

export function PwmTab() {
  const { settings } = useOutletContext<{ settings: Settings }>();
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

  const onSave = async () => {
    let d: object = {};
    for (const [k, v] of Object.entries(pwm)) d = setPath(d, `pwm.${k}`, v);
    setSaved(await save(d, ["settings_update"])); // control loop must re-read pwm
  };

  return (
    <Section title="PWM Fan">
      <Toggle label="PWM Control" checked={pwm.pwm_control} onChange={(v) => set("pwm_control", v)} />
      <NumberField label="Update Time" value={pwm.update_time} onChange={(v) => set("update_time", v)} min={1} suffix="s" />
      <NumberField label="Min Duty Cycle" value={pwm.min_duty_cycle} onChange={(v) => set("min_duty_cycle", v)} min={0} max={100} suffix="%" />
      <NumberField label="Max Duty Cycle" value={pwm.max_duty_cycle} onChange={(v) => set("max_duty_cycle", v)} min={0} max={100} suffix="%" />
      <NumberField label="Frequency" value={pwm.frequency} onChange={(v) => set("frequency", v)} min={1} suffix="Hz" />
      <div className="pf-settings-actions">
        <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>{saving ? "Saving…" : "Save"}</button>
        {saved && <span className="pf-settings-saved">Saved ✓</span>}
      </div>
    </Section>
  );
}
