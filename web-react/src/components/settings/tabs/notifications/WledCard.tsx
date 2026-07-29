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

  return (
    <Section title="WLED">
      <Toggle
        label="WLED Enabled"
        checked={asBool(wled.enabled)}
        onChange={(b) => setKey("enabled", b)}
      />
      <TextField
        label="WLED Device Address"
        value={asStr(wled.device_address)}
        onChange={(val) => setKey("device_address", val)}
      />
      <NumberField
        label="WLED Notify Duration"
        value={asNum(wled.notify_duration, 120)}
        onChange={(n) => setKey("notify_duration", n)}
        // index.html:2003. UI-only: the schema has ge=0 and no upper bound.
        min={0}
        max={3600}
      />

      <Toggle
        label="Use PiFire Suggested LED Behaviors"
        checked={useSuggested}
        onChange={(b) => setKey("use_suggested_presets", b)}
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
          />
          <NumberField
            label="Idle Brightness"
            value={asNum(suggested.idle_brightness, 20)}
            onChange={(n) => setSuggested("idle_brightness", n)}
            min={1}
            max={100}
            suffix="%"
          />
          <NumberField
            label="LED Count"
            value={asNum(suggested.led_count, 6)}
            onChange={(n) => setSuggested("led_count", n)}
            min={1}
            max={1000}
          />
          <Toggle
            label="Night Mode (dim amber glow)"
            checked={asBool(suggested.night_mode)}
            onChange={(b) => setSuggested("night_mode", b)}
          />
        </>
      )}

      <Toggle
        label="Use Profile-Based WLED Control"
        checked={useProfiles}
        onChange={(b) => setKey("use_profiles", b)}
      />
      {useProfiles && (
        <>
          <h3 className="pf-wled-subhead">Profile Numbers</h3>
          <div className="pf-wled-grid">
            {PROFILE_STATES.map(([state, def]) => (
              <NumberField
                key={state}
                label={state}
                value={asNum(profileNumbers[state], def)}
                onChange={(n) => setProfile(state, n)}
                min={1}
                max={250}
              />
            ))}
          </div>
        </>
      )}
    </Section>
  );
}
