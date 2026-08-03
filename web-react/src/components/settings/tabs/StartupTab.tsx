import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import { hasDcFan } from "../../../helpers/settings/platform";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { SETTINGS_DEFAULTS } from "../../../helpers/settings/settingsDefaults.gen";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import type {
  AfterStartupMode,
  SmartStartProfile,
} from "../../../helpers/settings/settingsTypes.gen";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Select } from "../fields/Select";
import { Toggle } from "../fields/Toggle";
import { type RangeProfileColumn, RangeProfileTable } from "../RangeProfileTable";
import { SaveBar } from "../SaveBar";

const SMARTSTART_COLUMNS: RangeProfileColumn[] = [
  { key: "startuptime", label: "Startup time", suffix: "s", min: 30, max: 1200 },
  { key: "augerontime", label: "Auger on", suffix: "s", min: 1, max: 1000 },
  { key: "p_mode", label: "P-Mode", min: 0, max: 9 },
];

const SHUTDOWN_DEFAULTS = SETTINGS_DEFAULTS.shutdown;
const STARTUP_DEFAULTS = SETTINGS_DEFAULTS.startup;

type Startup = {
  shutdown_duration: number;
  auto_power_off: boolean;
  duration: number;
  startup_exit_temp: number;
  prime_on_startup: number;
  // Remembered "last non-zero" values for the two 0-means-disabled numbers.
  // Seeded in readStartup, NOT derived in an effect (React Compiler rejects
  // setState-in-effect). Flask's settings.js:952-956 / :967-971 captures the
  // loaded value first and only substitutes its default when it was 0.
  exit_temp_default: number;
  prime_default: number;
  pwm_duty_cycle: number;
  smartstart_enabled: boolean;
  smartstart_exit_temp: number;
  smartstartTemps: number[];
  smartstartProfiles: Record<string, number>[];
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
    shutdown_duration: sh.shutdown_duration ?? SHUTDOWN_DEFAULTS.shutdown_duration,
    auto_power_off: !!sh.auto_power_off,
    duration: st.duration ?? STARTUP_DEFAULTS.duration,
    // Pre-fill for the box, not a schema default: the stored 0 means the
    // exit-temp check is off, and this is the value to offer when it is
    // turned on.
    startup_exit_temp: st.startup_exit_temp ?? 150,
    prime_on_startup: st.prime_on_startup ?? STARTUP_DEFAULTS.prime_on_startup,
    // Pre-fill for the box, not a schema default: offered only when the
    // stored exit temp is 0 ("disabled") and the user switches it on.
    exit_temp_default: (st.startup_exit_temp ?? 0) > 0 ? (st.startup_exit_temp as number) : 140,
    // Pre-fill for the box, not a schema default: offered only when the
    // stored prime amount is 0 ("disabled") and the user switches it on.
    prime_default: (st.prime_on_startup ?? 0) > 0 ? (st.prime_on_startup as number) : 10,
    pwm_duty_cycle: st.pwm_duty_cycle ?? STARTUP_DEFAULTS.pwm_duty_cycle,
    smartstart_enabled: !!ss.enabled,
    smartstart_exit_temp: ss.exit_temp ?? STARTUP_DEFAULTS.smartstart.exit_temp,
    // Cloned (never aliased into the widget's onChange arrays) — the widget
    // emits fresh arrays on edit, but our local state must not share
    // references with `settings`.
    smartstartTemps: structuredClone(
      (ss.temp_range_list ?? STARTUP_DEFAULTS.smartstart.temp_range_list) as number[],
    ),
    smartstartProfiles: structuredClone(
      (ss.profiles ?? STARTUP_DEFAULTS.smartstart.profiles) as unknown as Record<string, number>[],
    ),
    after_startup_mode: stm.after_startup_mode ?? STARTUP_DEFAULTS.start_to_mode.after_startup_mode,
    primary_setpoint: stm.primary_setpoint ?? STARTUP_DEFAULTS.start_to_mode.primary_setpoint,
    start_to_hold_prompt: !!stm.start_to_hold_prompt,
  };
}

export function StartupTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving, status } = useSaveSettings();
  // Held on SettingsShell, so an unfinished edit survives a trip to another tab.
  const { value: v, setValue: setV, dirty, markSaved } = useSettingsDraft("startup", readStartup);

  const set = <K extends keyof Startup>(k: K, val: Startup[K]) => setV((s) => ({ ...s, [k]: val }));
  const units = settings.globals?.units === "C" ? "°C" : "°F";
  // Flask gates the DC-fan duty-cycle NOTE + input on platform.dc_fan
  // (settings/index.html:857-868). The clamp in onSave stays UNCONDITIONAL:
  // the value is still in the delta on an AC build, so it still has to satisfy
  // SettingsSchema._check_startup_pwm_duty_cycle.
  const dcFan = hasDcFan(settings);

  // The switches are DERIVED from the numbers, never stored alongside them:
  // two sources of truth for one value is exactly how "0 = disabled" got lost.
  // Off writes 0; on writes the remembered default.
  const exitTempOn = v.startup_exit_temp > 0;
  const primeOn = v.prime_on_startup > 0;
  const holdSelected = v.after_startup_mode === "Hold";

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
    const min_duty_cycle = pwm_bounds.min_duty_cycle ?? SETTINGS_DEFAULTS.pwm.min_duty_cycle;
    const max_duty_cycle = pwm_bounds.max_duty_cycle ?? SETTINGS_DEFAULTS.pwm.max_duty_cycle;
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
    // Table-driven arrays ride the same delta wholesale (plan ruling: single
    // Save per tab, existing ["settings_update"] flag kept).
    d = setPath(d, "startup.smartstart.temp_range_list", v.smartstartTemps);
    // RangeProfileTable is generic over Record<string, number>; every key it
    // writes back is one of SmartStartProfile's number fields.
    d = setPath(
      d,
      "startup.smartstart.profiles",
      v.smartstartProfiles as unknown as SmartStartProfile[],
    );

    // Build delta for start_to_mode fields
    // The Select only ever offers modeOptions's two values; the wider string
    // on Startup.after_startup_mode is the field's edit-box type, not its range.
    d = setPath(
      d,
      "startup.start_to_mode.after_startup_mode",
      v.after_startup_mode as AfterStartupMode,
    );
    d = setPath(d, "startup.start_to_mode.primary_setpoint", v.primary_setpoint);
    d = setPath(d, "startup.start_to_mode.start_to_hold_prompt", v.start_to_hold_prompt);

    if (await save(d, ["settings_update"])) markSaved();
  };

  const modeOptions = [
    { value: "Smoke", label: "Smoke" },
    { value: "Hold", label: "Hold" },
  ];

  return (
    <>
      <Section title="Shutdown">
        <NumberField
          integer
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
          integer
          label="Duration"
          value={v.duration}
          onChange={(n) => set("duration", n)}
          min={0}
          suffix="s"
        />
        <Toggle
          label="Exit Startup @ Temperature"
          checked={exitTempOn}
          onChange={(b) => set("startup_exit_temp", b ? v.exit_temp_default : 0)}
        />
        {exitTempOn && (
          <NumberField
            integer
            label="Startup Exit Temp"
            value={v.startup_exit_temp}
            onChange={(n) => set("startup_exit_temp", n)}
            min={0}
            suffix="°"
            hint="0 = disabled"
          />
        )}
        <Toggle
          label="Always Prime on Startup"
          checked={primeOn}
          onChange={(b) => set("prime_on_startup", b ? v.prime_default : 0)}
        />
        {primeOn && (
          <NumberField
            integer
            label="Prime on Startup"
            value={v.prime_on_startup}
            onChange={(n) => set("prime_on_startup", n)}
            min={0}
            max={200}
            hint="0 = disabled"
          />
        )}
        {dcFan && (
          <NumberField
            integer
            label="PWM Duty Cycle"
            value={v.pwm_duty_cycle}
            onChange={(n) => set("pwm_duty_cycle", n)}
            min={0}
            max={100}
            suffix="%"
          />
        )}
      </Section>

      <Section title="SmartStart">
        <Toggle
          label="Enabled"
          checked={v.smartstart_enabled}
          onChange={(b) => set("smartstart_enabled", b)}
        />
        <NumberField
          integer
          label="Exit Temp"
          value={v.smartstart_exit_temp}
          onChange={(n) => set("smartstart_exit_temp", n)}
          min={0}
          suffix="°"
        />
        <RangeProfileTable
          boundaries={v.smartstartTemps}
          profiles={v.smartstartProfiles}
          columns={SMARTSTART_COLUMNS}
          rangeHeader="Range"
          unit={units}
          // index.html:932, 978
          boundaryMin={0}
          boundaryMax={200}
          onChange={(boundaries, profiles) =>
            setV((s) => ({ ...s, smartstartTemps: boundaries, smartstartProfiles: profiles }))
          }
        />
      </Section>

      <Section title="Start to Mode">
        <Select
          label="After Startup Mode"
          value={v.after_startup_mode}
          options={modeOptions}
          onChange={(v) => set("after_startup_mode", v)}
        />
        {/* Flask hides the whole Hold block unless after_startup_mode is
            'Hold' (index.html:812-826, settings.js:943-950). Hiding is NOT
            clearing: both values stay in state and in the delta. */}
        {holdSelected && (
          <>
            <NumberField
              integer
              label="Primary Setpoint"
              value={v.primary_setpoint}
              onChange={(n) => set("primary_setpoint", n)}
              // index.html:819 — the bound is dynamic, read off the Safety tab.
              min={settings.safety?.maxstartuptemp ?? SETTINGS_DEFAULTS.safety.maxstartuptemp}
              max={settings.safety?.maxtemp ?? SETTINGS_DEFAULTS.safety.maxtemp}
              suffix="°"
            />
            <Toggle
              label="Start to Hold Prompt"
              checked={v.start_to_hold_prompt}
              onChange={(b) => set("start_to_hold_prompt", b)}
            />
          </>
        )}
        <SaveBar onSave={onSave} saving={saving} status={status} dirty={dirty} />
      </Section>
    </>
  );
}
