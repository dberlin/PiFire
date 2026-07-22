import { useState } from "react";
import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Select } from "../fields/Select";
import { Toggle } from "../fields/Toggle";

type Startup = {
  shutdown_duration: number;
  auto_power_off: boolean;
  duration: number;
  startup_exit_temp: number;
  prime_on_startup: number;
  pwm_duty_cycle: number;
  smartstart_enabled: boolean;
  smartstart_exit_temp: number;
  after_startup_mode: string;
  primary_setpoint: number;
  start_to_hold_prompt: boolean;
};

function readStartup(s: Settings): Startup {
  const sh = s.shutdown ?? {};
  const st = s.startup ?? {};
  const ss = st.smartstart ?? {};
  const stm = st.start_to_mode ?? {};
  return {
    shutdown_duration: sh.shutdown_duration ?? 60,
    auto_power_off: !!sh.auto_power_off,
    duration: st.duration ?? 60,
    startup_exit_temp: st.startup_exit_temp ?? 150,
    prime_on_startup: st.prime_on_startup ?? 0,
    pwm_duty_cycle: st.pwm_duty_cycle ?? 50,
    smartstart_enabled: !!ss.enabled,
    smartstart_exit_temp: ss.exit_temp ?? 150,
    after_startup_mode: stm.after_startup_mode ?? "Smoke",
    primary_setpoint: stm.primary_setpoint ?? 225,
    start_to_hold_prompt: !!stm.start_to_hold_prompt,
  };
}

export function StartupTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving } = useSaveSettings();
  const [v, setV] = useState<Startup>(() => readStartup(settings));
  const [prev, setPrev] = useState(settings);
  const [saved, setSaved] = useState(false);
  if (settings !== prev) {
    setPrev(settings);
    setV(readStartup(settings));
  }

  const set = <K extends keyof Startup>(k: K, val: Startup[K]) => setV((s) => ({ ...s, [k]: val }));

  const onSave = async () => {
    let d: object = {};

    // Build delta for shutdown fields
    d = setPath(d, "shutdown.shutdown_duration", v.shutdown_duration);
    d = setPath(d, "shutdown.auto_power_off", v.auto_power_off);

    // Apply coercions to prime_on_startup: clamp to [0, 200], else 0
    let prime_on_startup = v.prime_on_startup;
    if (prime_on_startup < 0 || prime_on_startup > 200) prime_on_startup = 0;

    // Apply coercions to pwm_duty_cycle: clamp to [settings.pwm.min_duty_cycle, settings.pwm.max_duty_cycle]
    let pwm_duty_cycle = v.pwm_duty_cycle;
    const pwm_bounds = settings.pwm ?? {};
    const min_duty_cycle = pwm_bounds.min_duty_cycle ?? 20;
    const max_duty_cycle = pwm_bounds.max_duty_cycle ?? 100;
    if (pwm_duty_cycle < min_duty_cycle) pwm_duty_cycle = min_duty_cycle;
    if (pwm_duty_cycle > max_duty_cycle) pwm_duty_cycle = max_duty_cycle;

    // Build delta for startup fields
    d = setPath(d, "startup.duration", v.duration);
    d = setPath(d, "startup.startup_exit_temp", v.startup_exit_temp);
    d = setPath(d, "startup.prime_on_startup", prime_on_startup);
    d = setPath(d, "startup.pwm_duty_cycle", pwm_duty_cycle);

    // Build delta for smartstart fields
    d = setPath(d, "startup.smartstart.enabled", v.smartstart_enabled);
    d = setPath(d, "startup.smartstart.exit_temp", v.smartstart_exit_temp);

    // Build delta for start_to_mode fields
    d = setPath(d, "startup.start_to_mode.after_startup_mode", v.after_startup_mode);
    d = setPath(d, "startup.start_to_mode.primary_setpoint", v.primary_setpoint);
    d = setPath(d, "startup.start_to_mode.start_to_hold_prompt", v.start_to_hold_prompt);

    setSaved(await save(d, ["settings_update"]));
  };

  const modeOptions = [
    { value: "Smoke", label: "Smoke" },
    { value: "Hold", label: "Hold" },
  ];

  return (
    <>
      <Section title="Shutdown">
        <NumberField
          label="Shutdown Duration"
          value={v.shutdown_duration}
          onChange={(n) => set("shutdown_duration", n)}
          min={0}
          suffix="s"
        />
        <Toggle
          label="Auto Power Off"
          checked={v.auto_power_off}
          onChange={(b) => set("auto_power_off", b)}
        />
      </Section>

      <Section title="Startup">
        <NumberField
          label="Duration"
          value={v.duration}
          onChange={(n) => set("duration", n)}
          min={0}
          suffix="s"
        />
        <NumberField
          label="Startup Exit Temp"
          value={v.startup_exit_temp}
          onChange={(n) => set("startup_exit_temp", n)}
          min={0}
          suffix="°"
        />
        <NumberField
          label="Prime on Startup"
          value={v.prime_on_startup}
          onChange={(n) => set("prime_on_startup", n)}
          min={0}
        />
        <NumberField
          label="PWM Duty Cycle"
          value={v.pwm_duty_cycle}
          onChange={(n) => set("pwm_duty_cycle", n)}
          min={0}
          max={100}
          suffix="%"
        />
      </Section>

      <Section title="SmartStart">
        <Toggle
          label="Enabled"
          checked={v.smartstart_enabled}
          onChange={(b) => set("smartstart_enabled", b)}
        />
        <NumberField
          label="Exit Temp"
          value={v.smartstart_exit_temp}
          onChange={(n) => set("smartstart_exit_temp", n)}
          min={0}
          suffix="°"
        />
      </Section>

      <Section title="Start to Mode">
        <Select
          label="After Startup Mode"
          value={v.after_startup_mode}
          options={modeOptions}
          onChange={(v) => set("after_startup_mode", v)}
        />
        <NumberField
          label="Primary Setpoint"
          value={v.primary_setpoint}
          onChange={(n) => set("primary_setpoint", n)}
          min={0}
          suffix="°"
        />
        <Toggle
          label="Start to Hold Prompt"
          checked={v.start_to_hold_prompt}
          onChange={(b) => set("start_to_hold_prompt", b)}
        />
        <div className="pf-settings-actions">
          <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>
            {saving ? "Saving…" : "Save"}
          </button>
          {saved && <span className="pf-settings-saved">Saved ✓</span>}
        </div>
      </Section>
    </>
  );
}
