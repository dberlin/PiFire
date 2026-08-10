import { useOutletContext } from "react-router";
import { readSelected } from "../../../helpers/settings/controllerSelection";
import { setPath } from "../../../helpers/settings/delta";
import { SettingsFieldErrorsProvider } from "../../../helpers/settings/fieldErrorContext";
import { hasDcFan } from "../../../helpers/settings/platform";
import type { ControllerCatalog } from "../../../helpers/settings/controllerTypes.gen";
import type { SettingsSchema } from "../../../helpers/settings/settingsTypes.gen";
import { SETTINGS_DEFAULTS } from "../../../helpers/settings/settingsDefaults.gen";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";
import { SaveBar } from "../SaveBar";

type WorkMode = {
  cycle_data: {
    SmokeOnCycleTime: number;
    SmokeOffCycleTime: number;
    PMode: number;
    u_max: number;
    LidOpenDetectEnabled: boolean;
    LidOpenThreshold: number;
    LidOpenPauseTime: number;
  };
  smoke_plus: {
    enabled: boolean;
    min_temp: number;
    max_temp: number;
    on_time: number;
    off_time: number;
    duty_cycle: number;
    fan_ramp: boolean;
  };
  keep_warm: {
    temp: number;
    s_plus: boolean;
  };
};

function readWorkMode(s: SettingsSchema): WorkMode {
  const cd = s.cycle_data ?? {};
  const sp = s.smoke_plus ?? {};
  const kw = s.keep_warm ?? {};
  return {
    cycle_data: {
      SmokeOnCycleTime: cd.SmokeOnCycleTime ?? SETTINGS_DEFAULTS.cycle_data.SmokeOnCycleTime,
      SmokeOffCycleTime: cd.SmokeOffCycleTime ?? SETTINGS_DEFAULTS.cycle_data.SmokeOffCycleTime,
      PMode: cd.PMode ?? SETTINGS_DEFAULTS.cycle_data.PMode,
      u_max: cd.u_max ?? SETTINGS_DEFAULTS.cycle_data.u_max,
      LidOpenDetectEnabled: !!cd.LidOpenDetectEnabled,
      LidOpenThreshold: cd.LidOpenThreshold ?? SETTINGS_DEFAULTS.cycle_data.LidOpenThreshold,
      LidOpenPauseTime: cd.LidOpenPauseTime ?? SETTINGS_DEFAULTS.cycle_data.LidOpenPauseTime,
    },
    smoke_plus: {
      enabled: !!sp.enabled,
      min_temp: sp.min_temp ?? SETTINGS_DEFAULTS.smoke_plus.min_temp,
      max_temp: sp.max_temp ?? SETTINGS_DEFAULTS.smoke_plus.max_temp,
      on_time: sp.on_time ?? SETTINGS_DEFAULTS.smoke_plus.on_time,
      off_time: sp.off_time ?? SETTINGS_DEFAULTS.smoke_plus.off_time,
      duty_cycle: sp.duty_cycle ?? SETTINGS_DEFAULTS.smoke_plus.duty_cycle,
      fan_ramp: !!sp.fan_ramp,
    },
    keep_warm: {
      temp: kw.temp ?? SETTINGS_DEFAULTS.keep_warm.temp,
      s_plus: !!kw.s_plus,
    },
  };
}

export function WorkModeTab() {
  // settingsLoader already fetches the controller catalog alongside settings and
  // SettingsShell puts it on the Outlet, so this is the same blob ControllerTab
  // reads -- a query here would be a second request for data already in hand,
  // and would let the two tabs disagree about it.
  const { settings, controllerMeta } = useOutletContext<{
    settings: SettingsSchema;
    mode: string;
    controllerMeta: ControllerCatalog | null;
  }>();
  const { save, saving, status, errors } = useSaveSettings();
  // Held on SettingsShell, so an unfinished edit survives a trip to another tab.
  const {
    value: v,
    setValue: setV,
    dirty,
    markSaved,
  } = useSettingsDraft("work-mode", readWorkMode);

  const setCycleData = <K extends keyof WorkMode["cycle_data"]>(
    k: K,
    val: WorkMode["cycle_data"][K],
  ) => setV((s) => ({ ...s, cycle_data: { ...s.cycle_data, [k]: val } }));
  const setSmokePlus = <K extends keyof WorkMode["smoke_plus"]>(
    k: K,
    val: WorkMode["smoke_plus"][K],
  ) => setV((s) => ({ ...s, smoke_plus: { ...s.smoke_plus, [k]: val } }));
  const setKeepWarm = <K extends keyof WorkMode["keep_warm"]>(
    k: K,
    val: WorkMode["keep_warm"][K],
  ) => setV((s) => ({ ...s, keep_warm: { ...s.keep_warm, [k]: val } }));

  // Flask gates the fan-ramp NOTE, the sp_fan_ramp switch and the sp_duty_cycle
  // input on platform.dc_fan (settings/index.html:405-423).
  const dcFan = hasDcFan(settings);

  // u_max is a duty-cycle CEILING the controller works under, so what a sensible
  // value is depends on which controller is running -- hence the catalog, not a
  // constant here. `undefined` (no catalog, or a controller that recommends
  // nothing) simply means no button.
  const recommendedUMax =
    controllerMeta?.metadata[readSelected(settings, controllerMeta)]?.recommendations?.cycle
      ?.cycle_ratio_max;

  const onSave = async () => {
    let d: object = {};
    // Object.entries widens its key to string, which erases exactly the fact
    // these loops depend on: every key of `v.<section>` is a field of the
    // matching settings section.
    type CycleDataKey = keyof NonNullable<SettingsSchema["cycle_data"]>;
    type SmokePlusKey = keyof NonNullable<SettingsSchema["smoke_plus"]>;
    type KeepWarmKey = keyof NonNullable<SettingsSchema["keep_warm"]>;
    for (const k of Object.keys(v.cycle_data) as CycleDataKey[]) {
      d = setPath(d, `cycle_data.${k}`, v.cycle_data[k]);
    }
    for (const k of Object.keys(v.smoke_plus) as SmokePlusKey[]) {
      d = setPath(d, `smoke_plus.${k}`, v.smoke_plus[k]);
    }
    for (const k of Object.keys(v.keep_warm) as KeepWarmKey[]) {
      d = setPath(d, `keep_warm.${k}`, v.keep_warm[k]);
    }
    if (await save(d, ["settings_update"])) markSaved();
  };

  return (
    <SettingsFieldErrorsProvider errors={errors}>
      <Section title="Cycle Data">
        <NumberField
          integer
          label="Smoke On Cycle Time"
          value={v.cycle_data.SmokeOnCycleTime}
          onChange={(n) => setCycleData("SmokeOnCycleTime", n)}
          min={1}
          suffix="s"
          path="cycle_data.SmokeOnCycleTime"
        />
        <NumberField
          integer
          label="Smoke Off Cycle Time"
          value={v.cycle_data.SmokeOffCycleTime}
          onChange={(n) => setCycleData("SmokeOffCycleTime", n)}
          min={1}
          suffix="s"
          path="cycle_data.SmokeOffCycleTime"
        />
        <NumberField
          integer
          label="PMode"
          value={v.cycle_data.PMode}
          onChange={(n) => setCycleData("PMode", n)}
          // index.html:343. No schema counterpart on cycle_data.PMode, but
          // SmartStartProfile.p_mode IS schema-bound ge=0/le=9
          // (settings_schema.py:324), so 0-9 is the house rule.
          min={0}
          max={9}
          hint="0–9"
          path="cycle_data.PMode"
        />
        <NumberField
          label="U Max"
          value={v.cycle_data.u_max}
          onChange={(n) => setCycleData("u_max", n)}
          step={0.1}
          path="cycle_data.u_max"
          // Flask's _macro_settings.html:136 labelled this button with the value
          // itself and staged it without saving; SaveBar going dirty is the
          // whole point. It rides in the field's `trailing` slot so it lands in
          // the row's third grid track instead of under the label.
          trailing={
            // `typeof === "number"`, not `!== undefined`: nothing validates
            // controllers.json against a schema, so a controller shipping
            // `cycle_ratio_max: null` would otherwise render an empty arrow and
            // stage null into a float setting.
            typeof recommendedUMax === "number" && (
              <button
                type="button"
                className="pf-recommend-btn"
                title="Click to Use Recommended Value."
                // The visible text is an arrow and a bare number, which names
                // no action; `title` is only an accessible-name FALLBACK, so
                // with text content present it stays a tooltip and a screen
                // reader would announce "leftwards arrow 0.9".
                aria-label={`Use recommended value ${recommendedUMax}`}
                onClick={() => setCycleData("u_max", recommendedUMax)}
              >
                ← {recommendedUMax}
              </button>
            )
          }
        />
        <Toggle
          label="Lid Open Detect Enabled"
          checked={v.cycle_data.LidOpenDetectEnabled}
          onChange={(b) => setCycleData("LidOpenDetectEnabled", b)}
          path="cycle_data.LidOpenDetectEnabled"
        />
        <NumberField
          integer
          label="Lid Open Threshold"
          value={v.cycle_data.LidOpenThreshold}
          onChange={(n) => setCycleData("LidOpenThreshold", n)}
          // index.html:502
          min={1}
          max={80}
          step={1}
          path="cycle_data.LidOpenThreshold"
        />
        <NumberField
          integer
          label="Lid Open Pause Time"
          value={v.cycle_data.LidOpenPauseTime}
          onChange={(n) => setCycleData("LidOpenPauseTime", n)}
          // index.html:511 — the audit missed this one
          min={10}
          max={1000}
          step={1}
          suffix="s"
          path="cycle_data.LidOpenPauseTime"
        />
      </Section>

      <Section title="Smoke Plus">
        <Toggle
          label="Enabled"
          checked={v.smoke_plus.enabled}
          onChange={(b) => setSmokePlus("enabled", b)}
          path="smoke_plus.enabled"
        />
        <NumberField
          integer
          label="Min Temp"
          value={v.smoke_plus.min_temp}
          onChange={(n) => setSmokePlus("min_temp", n)}
          min={1}
          suffix="°"
          path="smoke_plus.min_temp"
        />
        <NumberField
          integer
          label="Max Temp"
          value={v.smoke_plus.max_temp}
          onChange={(n) => setSmokePlus("max_temp", n)}
          min={1}
          suffix="°"
          path="smoke_plus.max_temp"
        />
        <NumberField
          integer
          label="On Time"
          value={v.smoke_plus.on_time}
          onChange={(n) => setSmokePlus("on_time", n)}
          min={1}
          suffix="s"
          path="smoke_plus.on_time"
        />
        <NumberField
          integer
          label="Off Time"
          value={v.smoke_plus.off_time}
          onChange={(n) => setSmokePlus("off_time", n)}
          min={1}
          suffix="s"
          path="smoke_plus.off_time"
        />
        {/* Hiding these does NOT drop their keys: onSave iterates
            Object.entries(v.smoke_plus), so the loaded values keep
            round-tripping on an AC build — which matches Flask, whose
            _settings_cycle leaves untouched keys alone. */}
        {dcFan && (
          <>
            <p className="pf-settings-hint">
              Fan ramping and duty cycle apply to a PWM-driven DC fan.
            </p>
            <NumberField
              integer
              label="Duty Cycle"
              value={v.smoke_plus.duty_cycle}
              onChange={(n) => setSmokePlus("duty_cycle", n)}
              min={20}
              max={100}
              suffix="%"
              path="smoke_plus.duty_cycle"
            />
            <Toggle
              label="Fan Ramp"
              checked={v.smoke_plus.fan_ramp}
              onChange={(b) => setSmokePlus("fan_ramp", b)}
              path="smoke_plus.fan_ramp"
            />
          </>
        )}
      </Section>

      <Section title="Keep Warm">
        <NumberField
          integer
          label="Temp"
          value={v.keep_warm.temp}
          onChange={(n) => setKeepWarm("temp", n)}
          min={1}
          suffix="°"
          path="keep_warm.temp"
        />
        <Toggle
          label="S Plus"
          checked={v.keep_warm.s_plus}
          onChange={(b) => setKeepWarm("s_plus", b)}
          path="keep_warm.s_plus"
        />
        <SaveBar onSave={onSave} saving={saving} status={status} dirty={dirty} />
      </Section>
    </SettingsFieldErrorsProvider>
  );
}
