import { useState } from "react";
import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";

type WorkMode = {
  cycle_data: {
    HoldCycleTime: number;
    SmokeOnCycleTime: number;
    SmokeOffCycleTime: number;
    PMode: number;
    u_min: number;
    u_max: number;
    LidOpenDetectEnabled: boolean;
    LidOpenThreshold: number;
    LidOpenPauseTime: number;
    FanPidEnabled: boolean;
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

function readWorkMode(s: Settings): WorkMode {
  const cd = s.cycle_data ?? {};
  const sp = s.smoke_plus ?? {};
  const kw = s.keep_warm ?? {};
  return {
    cycle_data: {
      HoldCycleTime: cd.HoldCycleTime ?? 10,
      SmokeOnCycleTime: cd.SmokeOnCycleTime ?? 5,
      SmokeOffCycleTime: cd.SmokeOffCycleTime ?? 5,
      PMode: cd.PMode ?? 0,
      u_min: cd.u_min ?? 0,
      u_max: cd.u_max ?? 100,
      LidOpenDetectEnabled: !!cd.LidOpenDetectEnabled,
      LidOpenThreshold: cd.LidOpenThreshold ?? 30,
      LidOpenPauseTime: cd.LidOpenPauseTime ?? 60,
      FanPidEnabled: !!cd.FanPidEnabled,
    },
    smoke_plus: {
      enabled: !!sp.enabled,
      min_temp: sp.min_temp ?? 150,
      max_temp: sp.max_temp ?? 275,
      on_time: sp.on_time ?? 5,
      off_time: sp.off_time ?? 5,
      duty_cycle: sp.duty_cycle ?? 50,
      fan_ramp: !!sp.fan_ramp,
    },
    keep_warm: {
      temp: kw.temp ?? 150,
      s_plus: !!kw.s_plus,
    },
  };
}

export function WorkModeTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving } = useSaveSettings();
  const [v, setV] = useState<WorkMode>(() => readWorkMode(settings));
  const [prev, setPrev] = useState(settings);
  const [saved, setSaved] = useState(false);
  if (settings !== prev) {
    setPrev(settings);
    setV(readWorkMode(settings));
  }

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

  const onSave = async () => {
    let d: object = {};
    for (const [k, val] of Object.entries(v.cycle_data)) d = setPath(d, `cycle_data.${k}`, val);
    for (const [k, val] of Object.entries(v.smoke_plus)) d = setPath(d, `smoke_plus.${k}`, val);
    for (const [k, val] of Object.entries(v.keep_warm)) d = setPath(d, `keep_warm.${k}`, val);
    setSaved(await save(d, ["settings_update"]));
  };

  return (
    <>
      <Section title="Cycle Data">
        <NumberField
          label="Hold Cycle Time"
          value={v.cycle_data.HoldCycleTime}
          onChange={(n) => setCycleData("HoldCycleTime", n)}
          min={0}
          suffix="s"
        />
        <NumberField
          label="Smoke On Cycle Time"
          value={v.cycle_data.SmokeOnCycleTime}
          onChange={(n) => setCycleData("SmokeOnCycleTime", n)}
          min={0}
          suffix="s"
        />
        <NumberField
          label="Smoke Off Cycle Time"
          value={v.cycle_data.SmokeOffCycleTime}
          onChange={(n) => setCycleData("SmokeOffCycleTime", n)}
          min={0}
          suffix="s"
        />
        <NumberField
          label="PMode"
          value={v.cycle_data.PMode}
          onChange={(n) => setCycleData("PMode", n)}
          min={0}
        />
        <NumberField
          label="U Min"
          value={v.cycle_data.u_min}
          onChange={(n) => setCycleData("u_min", n)}
          step={0.1}
        />
        <NumberField
          label="U Max"
          value={v.cycle_data.u_max}
          onChange={(n) => setCycleData("u_max", n)}
          step={0.1}
        />
        <Toggle
          label="Lid Open Detect Enabled"
          checked={v.cycle_data.LidOpenDetectEnabled}
          onChange={(b) => setCycleData("LidOpenDetectEnabled", b)}
        />
        <NumberField
          label="Lid Open Threshold"
          value={v.cycle_data.LidOpenThreshold}
          onChange={(n) => setCycleData("LidOpenThreshold", n)}
          min={0}
        />
        <NumberField
          label="Lid Open Pause Time"
          value={v.cycle_data.LidOpenPauseTime}
          onChange={(n) => setCycleData("LidOpenPauseTime", n)}
          min={0}
          suffix="s"
        />
        <Toggle
          label="Fan PID Enabled"
          checked={v.cycle_data.FanPidEnabled}
          onChange={(b) => setCycleData("FanPidEnabled", b)}
        />
      </Section>

      <Section title="Smoke Plus">
        <Toggle
          label="Enabled"
          checked={v.smoke_plus.enabled}
          onChange={(b) => setSmokePlus("enabled", b)}
        />
        <NumberField
          label="Min Temp"
          value={v.smoke_plus.min_temp}
          onChange={(n) => setSmokePlus("min_temp", n)}
          min={0}
          suffix="°"
        />
        <NumberField
          label="Max Temp"
          value={v.smoke_plus.max_temp}
          onChange={(n) => setSmokePlus("max_temp", n)}
          min={0}
          suffix="°"
        />
        <NumberField
          label="On Time"
          value={v.smoke_plus.on_time}
          onChange={(n) => setSmokePlus("on_time", n)}
          min={0}
          suffix="s"
        />
        <NumberField
          label="Off Time"
          value={v.smoke_plus.off_time}
          onChange={(n) => setSmokePlus("off_time", n)}
          min={0}
          suffix="s"
        />
        <NumberField
          label="Duty Cycle"
          value={v.smoke_plus.duty_cycle}
          onChange={(n) => setSmokePlus("duty_cycle", n)}
          min={20}
          max={100}
          suffix="%"
        />
        <Toggle
          label="Fan Ramp"
          checked={v.smoke_plus.fan_ramp}
          onChange={(b) => setSmokePlus("fan_ramp", b)}
        />
      </Section>

      <Section title="Keep Warm">
        <NumberField
          label="Temp"
          value={v.keep_warm.temp}
          onChange={(n) => setKeepWarm("temp", n)}
          min={0}
          suffix="°"
        />
        <Toggle
          label="S Plus"
          checked={v.keep_warm.s_plus}
          onChange={(b) => setKeepWarm("s_plus", b)}
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
