import { useState } from "react";
import type { WledDevice } from "../../../../helpers/contracts/control.gen";
import {
  discoverWled,
  pushWledProfiles,
  testWledProfile,
} from "../../../../helpers/notify/wledApi";
import { NumberField } from "../../fields/NumberField";
import { Section } from "../../fields/Section";
import { Select } from "../../fields/Select";
import { TextField } from "../../fields/TextField";
import { Toggle } from "../../fields/Toggle";

// The wled service is the same loosely-typed bag NotificationsTab holds. This
// card renders every WLED field Flask's settings/index.html exposes; the legacy
// mode_presets/event_presets subtrees (schema-carried, zero Flask UI) are NEVER
// rendered but ARE preserved because every edit spreads the existing bag.
type WledBag = Record<string, unknown>;

interface WledCardProps {
  wled: WledBag;
  onChange: (next: WledBag) => void;
}

// Ordered exactly as common/defaults.py default_notify_services()["wled"]
// ["profile_numbers"], with each key's default as a UI fallback for a bag that
// is missing the value (real settings always carry all twelve).
const PROFILE_STATES: [string, number][] = [
  ["idle", 200],
  ["booting", 201],
  ["preheat", 202],
  ["cooking", 203],
  ["cooldown", 204],
  ["target_reached", 205],
  ["overshoot_alarm", 206],
  ["probe_alarm", 207],
  ["low_pellets", 208],
  ["timer_done", 209],
  ["error_fault", 210],
  ["night_mode", 211],
];

const asBool = (v: unknown): boolean => !!v;
const asStr = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const asNum = (v: unknown, fallback: number): number => (typeof v === "number" ? v : fallback);

export function WledCard({ wled, onChange }: WledCardProps) {
  const useProfiles = asBool(wled.use_profiles);
  const useSuggested = asBool(wled.use_suggested_presets);
  const profileNumbers = (wled.profile_numbers as Record<string, unknown> | undefined) ?? {};
  const suggested = (wled.suggested_config as Record<string, unknown> | undefined) ?? {};

  const setKey = (key: string, val: unknown) => onChange({ ...wled, [key]: val });
  const setProfile = (state: string, val: number) =>
    onChange({ ...wled, profile_numbers: { ...profileNumbers, [state]: val } });
  const setSuggested = (key: string, val: unknown) =>
    onChange({ ...wled, suggested_config: { ...suggested, [key]: val } });

  const [busy, setBusy] = useState<null | "discover" | "push" | "test">(null);
  const [status, setStatus] = useState<{ kind: "info" | "success" | "error"; text: string } | null>(
    null,
  );
  const [devices, setDevices] = useState<WledDevice[] | null>(null);

  const address = asStr(wled.device_address).trim();
  // The live (unsaved) profile numbers the buttons push/test, resolved to real
  // integers with the same defaults the grid shows -- Flask's buttons read the
  // form fields, not the store, so nothing here needs a Save first.
  const profileNums = Object.fromEntries(
    PROFILE_STATES.map(([state, def]) => [state, asNum(profileNumbers[state], def)]),
  );

  const requireAddress = (): boolean => {
    if (address) return true;
    setStatus({ kind: "error", text: "Enter a WLED device address first." });
    return false;
  };

  const onDiscover = async () => {
    setBusy("discover");
    setStatus({ kind: "info", text: "Searching for WLED devices…" });
    const res = await discoverWled();
    setDevices(res.devices);
    setStatus({ kind: res.result === "success" ? "success" : "error", text: res.message });
    setBusy(null);
  };

  const onPush = async () => {
    if (!requireAddress()) return;
    setBusy("push");
    setStatus({ kind: "info", text: "Pushing profiles to WLED device…" });
    const res = await pushWledProfiles(address, profileNums);
    setStatus({
      kind: res.result === "success" ? "success" : "error",
      text:
        res.result === "success"
          ? `Pushed ${res.profiles_pushed ?? 0} profiles to WLED.`
          : res.message,
    });
    setBusy(null);
  };

  const onTest = async () => {
    if (!requireAddress()) return;
    setBusy("test");
    const res = await testWledProfile(address, profileNums.cooking);
    setStatus({ kind: res.result === "success" ? "success" : "error", text: res.message });
    setBusy(null);
  };

  const selectDevice = (dev: WledDevice) =>
    onChange({
      ...wled,
      device_address: dev.ip,
      suggested_config: { ...suggested, led_count: dev.led_count },
    });

  return (
    <Section title="WLED">
      <Toggle
        label="WLED Enabled"
        checked={asBool(wled.enabled)}
        onChange={(b) => setKey("enabled", b)}
        path="notify_services.wled.enabled"
      />
      <TextField
        label="WLED Device Address"
        value={asStr(wled.device_address)}
        onChange={(val) => setKey("device_address", val)}
        path="notify_services.wled.device_address"
      />
      <div className="pf-settings-actions">
        <button
          type="button"
          className="pf-modal-btn"
          disabled={busy !== null}
          onClick={() => void onDiscover()}
        >
          {busy === "discover" ? "Searching…" : "Find WLED Devices"}
        </button>
      </div>
      {devices && devices.length > 0 && (
        <ul className="pf-wled-results">
          {devices.map((dev) => (
            <li key={dev.ip} className="pf-wled-result-row">
              <span>
                {dev.name} — {dev.ip} ({dev.led_count} LEDs)
              </span>
              <button type="button" className="pf-modal-btn" onClick={() => selectDevice(dev)}>
                Use
              </button>
            </li>
          ))}
        </ul>
      )}
      {devices && devices.length === 0 && (
        <p className="pf-wled-subhead">No WLED devices found on your network.</p>
      )}
      <NumberField
        integer
        label="WLED Notify Duration"
        value={asNum(wled.notify_duration, 120)}
        onChange={(n) => setKey("notify_duration", n)}
        // index.html:2003. UI-only: the schema has ge=0 and no upper bound.
        min={0}
        max={3600}
        path="notify_services.wled.notify_duration"
      />

      <Toggle
        label="Use PiFire Suggested LED Behaviors"
        checked={useSuggested}
        onChange={(b) => setKey("use_suggested_presets", b)}
        path="notify_services.wled.use_suggested_presets"
      />
      {useSuggested && (
        <>
          <h3 className="pf-wled-subhead">Suggested Preset Configuration</h3>
          <Select
            label="Cooking Color"
            value={asStr(suggested.cooking_color, "blue")}
            options={[
              { value: "blue", label: "Blue" },
              { value: "green", label: "Green" },
            ]}
            onChange={(v) => setSuggested("cooking_color", v)}
            path="notify_services.wled.suggested_config.cooking_color"
          />
          <NumberField
            integer
            label="Idle Brightness"
            value={asNum(suggested.idle_brightness, 20)}
            onChange={(n) => setSuggested("idle_brightness", n)}
            min={1}
            max={100}
            suffix="%"
            path="notify_services.wled.suggested_config.idle_brightness"
          />
          <NumberField
            integer
            label="LED Count"
            value={asNum(suggested.led_count, 6)}
            onChange={(n) => setSuggested("led_count", n)}
            min={1}
            max={1000}
            path="notify_services.wled.suggested_config.led_count"
          />
          <Toggle
            label="Night Mode (dim amber glow)"
            checked={asBool(suggested.night_mode)}
            onChange={(b) => setSuggested("night_mode", b)}
            path="notify_services.wled.suggested_config.night_mode"
          />
        </>
      )}

      <Toggle
        label="Use Profile-Based WLED Control"
        checked={useProfiles}
        onChange={(b) => setKey("use_profiles", b)}
        path="notify_services.wled.use_profiles"
      />
      {useProfiles && (
        <>
          <div className="pf-settings-actions">
            <button
              type="button"
              className="pf-modal-btn accent"
              disabled={busy !== null}
              onClick={() => void onPush()}
            >
              {busy === "push" ? "Pushing…" : "Push Profiles to WLED"}
            </button>
            <button
              type="button"
              className="pf-modal-btn"
              disabled={busy !== null}
              onClick={() => void onTest()}
            >
              {busy === "test" ? "Testing…" : "Test Profile"}
            </button>
          </div>
          <h3 className="pf-wled-subhead">Profile Numbers</h3>
          <div className="pf-wled-grid">
            {PROFILE_STATES.map(([state, def]) => (
              <NumberField
                key={state}
                integer
                label={state}
                value={asNum(profileNumbers[state], def)}
                onChange={(n) => setProfile(state, n)}
                min={1}
                max={250}
                path={`notify_services.wled.profile_numbers.${state}`}
              />
            ))}
          </div>
        </>
      )}

      {status && <div className={`pf-wled-status ${status.kind}`}>{status.text}</div>}
    </Section>
  );
}
