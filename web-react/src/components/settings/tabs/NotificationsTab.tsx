import { useState } from "react";
import { useOutletContext } from "react-router";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { StringListField } from "../fields/StringListField";
import { TextField } from "../fields/TextField";
import { Toggle } from "../fields/Toggle";

// Each notify service is a loosely-typed bag: the tab only reads/writes the
// scalar fields the legacy `_settings_notify` form submits (see the design
// spec), and rebuilds the WHOLE `notify_services` subtree on Save so every
// other key (WLED preset grids, onesignal uuid/app_id/devices, ...) survives
// byte-identical.
type NotifyService = Record<string, unknown>;
type NotifyServicesState = Record<string, NotifyService>;

function readNotify(s: Settings): { ns: NotifyServicesState } {
  return {
    ns: structuredClone(s.notify_services ?? {}) as unknown as NotifyServicesState,
  };
}

function str(o: NotifyService, key: string): string {
  const v = o[key];
  return typeof v === "string" ? v : "";
}
function bool(o: NotifyService, key: string): boolean {
  return !!o[key];
}
function num(o: NotifyService, key: string, fallback: number): number {
  const v = o[key];
  return typeof v === "number" ? v : fallback;
}

// OneSignal devices self-register from the mobile app; only friendly_name is
// editable here. uuid/app_id (siblings of `devices` on the onesignal service)
// are intentionally not rendered by this tab.
type OneSignalDevice = { friendly_name?: string; device_name?: string; app_version?: string };
function deviceStr(d: OneSignalDevice, key: keyof OneSignalDevice): string {
  const v = d[key];
  return typeof v === "string" ? v : "";
}
function devicesOf(o: NotifyService): Record<string, OneSignalDevice> {
  return (o.devices as Record<string, OneSignalDevice> | undefined) ?? {};
}

export function NotificationsTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving } = useSaveSettings();
  const [v, setV] = useState(() => readNotify(settings));
  const [prev, setPrev] = useState(settings);
  const [saved, setSaved] = useState(false);
  if (settings !== prev) {
    setPrev(settings);
    setV(readNotify(settings));
  }

  const { ns } = v;
  const svc = (name: string): NotifyService => ns[name] ?? {};

  const setField = (name: string, key: string, val: unknown) =>
    setV((s) => ({
      ns: { ...s.ns, [name]: { ...(s.ns[name] ?? {}), [key]: val } },
    }));

  const onSave = async () => {
    setSaved(await save({ notify_services: v.ns }, ["settings_update"]));
  };

  const setDeviceField = (deviceId: string, key: keyof OneSignalDevice, val: string) =>
    setV((s) => {
      const onesignalSvc = (s.ns.onesignal ?? {}) as NotifyService;
      const devices = devicesOf(onesignalSvc);
      return {
        ns: {
          ...s.ns,
          onesignal: {
            ...onesignalSvc,
            devices: {
              ...devices,
              [deviceId]: { ...devices[deviceId], [key]: val },
            },
          },
        },
      };
    });

  const deleteDevice = (deviceId: string) =>
    setV((s) => {
      const onesignalSvc = (s.ns.onesignal ?? {}) as NotifyService;
      const devices = { ...devicesOf(onesignalSvc) };
      delete devices[deviceId];
      return {
        ns: { ...s.ns, onesignal: { ...onesignalSvc, devices } },
      };
    });

  const apprise = svc("apprise");
  const ifttt = svc("ifttt");
  const pushbullet = svc("pushbullet");
  const pushover = svc("pushover");
  const onesignal = svc("onesignal");
  const onesignalDevices = Object.entries(devicesOf(onesignal));
  const influxdb = svc("influxdb");
  const mqtt = svc("mqtt");
  const wled = svc("wled");

  return (
    <>
      <Section title="Apprise">
        <Toggle
          label="Apprise Enabled"
          checked={bool(apprise, "enabled")}
          onChange={(b) => setField("apprise", "enabled", b)}
        />
        <StringListField
          label="Apprise Locations"
          values={(apprise.locations as string[] | undefined) ?? []}
          onChange={(next) => setField("apprise", "locations", next)}
        />
      </Section>

      <Section title="IFTTT">
        <Toggle
          label="IFTTT Enabled"
          checked={bool(ifttt, "enabled")}
          onChange={(b) => setField("ifttt", "enabled", b)}
        />
        <TextField
          label="IFTTT API Key"
          value={str(ifttt, "APIKey")}
          onChange={(val) => setField("ifttt", "APIKey", val)}
        />
      </Section>

      <Section title="Pushbullet">
        <Toggle
          label="Pushbullet Enabled"
          checked={bool(pushbullet, "enabled")}
          onChange={(b) => setField("pushbullet", "enabled", b)}
        />
        <TextField
          label="Pushbullet API Key"
          value={str(pushbullet, "APIKey")}
          onChange={(val) => setField("pushbullet", "APIKey", val)}
        />
        <TextField
          label="Pushbullet Public URL"
          value={str(pushbullet, "PublicURL")}
          onChange={(val) => setField("pushbullet", "PublicURL", val)}
        />
      </Section>

      <Section title="Pushover">
        <Toggle
          label="Pushover Enabled"
          checked={bool(pushover, "enabled")}
          onChange={(b) => setField("pushover", "enabled", b)}
        />
        <TextField
          label="Pushover API Key"
          value={str(pushover, "APIKey")}
          onChange={(val) => setField("pushover", "APIKey", val)}
        />
        <TextField
          label="Pushover User Keys"
          value={str(pushover, "UserKeys")}
          onChange={(val) => setField("pushover", "UserKeys", val)}
        />
        <TextField
          label="Pushover Public URL"
          value={str(pushover, "PublicURL")}
          onChange={(val) => setField("pushover", "PublicURL", val)}
        />
      </Section>

      <Section title="OneSignal">
        <Toggle
          label="OneSignal Enabled"
          checked={bool(onesignal, "enabled")}
          onChange={(b) => setField("onesignal", "enabled", b)}
        />
        {onesignalDevices.length === 0 ? (
          <p className="pf-settings-hint">
            No devices registered. Devices register automatically when you sign in on the PiFire
            mobile app.
          </p>
        ) : (
          <table className="pf-devices-table">
            <thead>
              <tr>
                <th>Friendly Name</th>
                <th>Device</th>
                <th>App Version</th>
                <th className="pf-devices-delete-col" />
              </tr>
            </thead>
            <tbody>
              {onesignalDevices.map(([deviceId, device]) => (
                // deviceId (the OneSignal player-id) is the row's stable, guaranteed-unique
                // identity -- unlike device_name, which two physical devices of the same
                // model can share, so it's safe to use for a11y label uniqueness.
                <tr key={deviceId}>
                  <td>
                    <TextField
                      label={`Friendly Name (${deviceId})`}
                      value={deviceStr(device, "friendly_name")}
                      onChange={(val) => setDeviceField(deviceId, "friendly_name", val)}
                    />
                  </td>
                  <td className="pf-device-meta">{deviceStr(device, "device_name")}</td>
                  <td className="pf-device-meta">{deviceStr(device, "app_version")}</td>
                  <td className="pf-devices-delete-col">
                    <button
                      type="button"
                      aria-label={`Delete ${deviceId}`}
                      onClick={() => deleteDevice(deviceId)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="InfluxDB">
        <Toggle
          label="InfluxDB Enabled"
          checked={bool(influxdb, "enabled")}
          onChange={(b) => setField("influxdb", "enabled", b)}
        />
        <TextField
          label="InfluxDB URL"
          value={str(influxdb, "url")}
          onChange={(val) => setField("influxdb", "url", val)}
        />
        <TextField
          label="InfluxDB Token"
          value={str(influxdb, "token")}
          onChange={(val) => setField("influxdb", "token", val)}
        />
        <TextField
          label="InfluxDB Org"
          value={str(influxdb, "org")}
          onChange={(val) => setField("influxdb", "org", val)}
        />
        <TextField
          label="InfluxDB Bucket"
          value={str(influxdb, "bucket")}
          onChange={(val) => setField("influxdb", "bucket", val)}
        />
      </Section>

      <Section title="MQTT">
        <Toggle
          label="MQTT Enabled"
          checked={bool(mqtt, "enabled")}
          onChange={(b) => setField("mqtt", "enabled", b)}
        />
        <TextField
          label="MQTT Client ID"
          value={str(mqtt, "id")}
          onChange={(val) => setField("mqtt", "id", val)}
        />
        <TextField
          label="MQTT Broker"
          value={str(mqtt, "broker")}
          onChange={(val) => setField("mqtt", "broker", val)}
        />
        {/* schema types port as str — TextField, not NumberField */}
        <TextField
          label="MQTT Port"
          value={str(mqtt, "port")}
          onChange={(val) => setField("mqtt", "port", val)}
        />
        <TextField
          label="MQTT Username"
          value={str(mqtt, "username")}
          onChange={(val) => setField("mqtt", "username", val)}
        />
        <TextField
          label="MQTT Password"
          value={str(mqtt, "password")}
          onChange={(val) => setField("mqtt", "password", val)}
        />
        <TextField
          label="MQTT Home Assistant Autodiscovery Topic"
          value={str(mqtt, "homeassistant_autodiscovery_topic")}
          onChange={(val) => setField("mqtt", "homeassistant_autodiscovery_topic", val)}
        />
        {/* schema types update_sec as str — TextField, not NumberField */}
        <TextField
          label="MQTT Update Interval"
          value={str(mqtt, "update_sec")}
          onChange={(val) => setField("mqtt", "update_sec", val)}
        />
      </Section>

      <Section title="WLED">
        <Toggle
          label="WLED Enabled"
          checked={bool(wled, "enabled")}
          onChange={(b) => setField("wled", "enabled", b)}
        />
        <TextField
          label="WLED Device Address"
          value={str(wled, "device_address")}
          onChange={(val) => setField("wled", "device_address", val)}
        />
        <NumberField
          label="WLED Notify Duration"
          value={num(wled, "notify_duration", 120)}
          onChange={(n) => setField("wled", "notify_duration", n)}
          min={0}
        />
      </Section>

      <div className="pf-settings-actions">
        <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>
          {saving ? "Saving…" : "Save"}
        </button>
        {saved && <span className="pf-settings-saved">Saved ✓</span>}
      </div>
    </>
  );
}
