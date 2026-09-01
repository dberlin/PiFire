import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { useState } from "react";

import { SettingsFieldErrorsProvider } from "../../../helpers/settings/fieldErrorContext";
import { settingsDelta } from "../../../helpers/settings/settingsDelta";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { ConfirmAction } from "../../dashboard/ConfirmAction";
import { SecretField } from "../fields/SecretField";
import { Section } from "../fields/Section";
import { StringListField } from "../fields/StringListField";
import { TextField } from "../fields/TextField";
import { Toggle } from "../fields/Toggle";
import { SaveBar } from "../SaveBar";
import { WledCard } from "./notifications/WledCard";

// Each notify service is a loosely-typed bag: the tab only reads/writes the
// scalar fields the legacy `_settings_notify` form submits, and rebuilds the
// WHOLE `notify_services` subtree on Save so every other key (WLED preset
// grids, onesignal uuid/app_id/devices, ...) survives byte-identical.
type NotifyService = Record<string, unknown>;
type NotifyServicesState = Record<string, NotifyService>;

// removedDeviceIds rides alongside ns in the SAME draft (not a plain
// useState) because each settings tab is a distinct route: switching away
// from Notifications and back unmounts and remounts this component, and only
// state held in the draft store survives that trip. A plain useState here
// would forget a queued delete exactly the way the bug this fixes forgot it.
function readNotify(s: SettingsSchema): { ns: NotifyServicesState; removedDeviceIds: string[] } {
  return {
    ns: structuredClone(s.notify_services ?? {}) as unknown as NotifyServicesState,
    removedDeviceIds: [],
  };
}

function str(o: NotifyService, key: string): string {
  const v = o[key];
  return typeof v === "string" ? v : "";
}
function bool(o: NotifyService, key: string): boolean {
  return !!o[key];
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
/** How Flask names a device in the delete modal (index.html:1659-1670): the
    friendly name when set, the hardware device name otherwise. */
function deviceLabel(d: OneSignalDevice | undefined): string {
  if (!d) return "";
  return deviceStr(d, "friendly_name") || deviceStr(d, "device_name");
}

export function NotificationsTab() {
  const { save, saving, status, errors } = useSaveSettings();
  // Held on SettingsShell, so an unfinished edit survives a trip to another tab.
  const {
    value: v,
    setValue: setV,
    dirty,
    markSaved,
  } = useSettingsDraft("notifications", readNotify);

  const { ns } = v;
  const svc = (name: string): NotifyService => ns[name] ?? {};

  const setField = (name: string, key: string, val: unknown) =>
    setV((s) => ({
      ...s,
      ns: { ...s.ns, [name]: { ...s.ns[name], [key]: val } },
    }));

  const onSave = async () => {
    // notify_services is saved as a full-subtree merge (see the comment at
    // the top of this file), so a device dropped from v.ns is silence, not a
    // removal instruction -- apply_settings_delta deep-merges and the device
    // reappears on the next load. removedDeviceIds names every device
    // deleted since the last save explicitly, as delete paths.
    const ok = await save(
      settingsDelta(
        { notify_services: v.ns },
        v.removedDeviceIds.map((id) => ["notify_services", "onesignal", "devices", id]),
      ),
      ["settings_update"],
    );
    if (ok) {
      // Clear first, then markSaved: setV always marks the draft dirty
      // (saved: false), so markSaved has to be the last word to leave the
      // post-save draft clean.
      setV((s) => ({ ...s, removedDeviceIds: [] }));
      markSaved();
    }
  };

  const setDeviceField = (deviceId: string, key: keyof OneSignalDevice, val: string) =>
    setV((s) => {
      const onesignalSvc = (s.ns.onesignal ?? {}) as NotifyService;
      const devices = devicesOf(onesignalSvc);
      return {
        ...s,
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
        ...s,
        ns: { ...s.ns, onesignal: { ...onesignalSvc, devices } },
        // This tab has no add-device action -- OneSignal devices only ever
        // arrive via mobile-app self-registration -- so every deviceId that
        // reaches here (a delete button only renders for a row already in
        // devicesOf(...)) was necessarily part of the persisted tree, never
        // one added and removed again within this same draft. Nothing here
        // needs to distinguish that case; there is no path that creates it.
        removedDeviceIds: [...s.removedDeviceIds, deviceId],
      };
    });

  // The OneSignal device id awaiting confirmation; null when closed. Flask puts
  // this delete behind a modal naming the device (index.html:1651-1678). One
  // <ConfirmAction> for the whole table, not one per row.
  const [pendingDevice, setPendingDevice] = useState<string | null>(null);

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
    <SettingsFieldErrorsProvider errors={errors}>
      <Section title="Apprise">
        <Toggle
          label="Apprise Enabled"
          checked={bool(apprise, "enabled")}
          onChange={(b) => setField("apprise", "enabled", b)}
          path="notify_services.apprise.enabled"
        />
        <StringListField
          label="Apprise Locations"
          values={(apprise.locations as string[] | undefined) ?? []}
          onChange={(next) => setField("apprise", "locations", next)}
          path="notify_services.apprise.locations"
        />
      </Section>

      <Section title="IFTTT">
        <Toggle
          label="IFTTT Enabled"
          checked={bool(ifttt, "enabled")}
          onChange={(b) => setField("ifttt", "enabled", b)}
          path="notify_services.ifttt.enabled"
        />
        <SecretField
          label="IFTTT API Key"
          value={str(ifttt, "APIKey")}
          onChange={(val) => setField("ifttt", "APIKey", val)}
          path="notify_services.ifttt.APIKey"
        />
      </Section>

      <Section title="Pushbullet">
        <Toggle
          label="Pushbullet Enabled"
          checked={bool(pushbullet, "enabled")}
          onChange={(b) => setField("pushbullet", "enabled", b)}
          path="notify_services.pushbullet.enabled"
        />
        <SecretField
          label="Pushbullet API Key"
          value={str(pushbullet, "APIKey")}
          onChange={(val) => setField("pushbullet", "APIKey", val)}
          path="notify_services.pushbullet.APIKey"
        />
        <TextField
          label="Pushbullet Public URL"
          value={str(pushbullet, "PublicURL")}
          onChange={(val) => setField("pushbullet", "PublicURL", val)}
          path="notify_services.pushbullet.PublicURL"
        />
      </Section>

      <Section title="Pushover">
        <Toggle
          label="Pushover Enabled"
          checked={bool(pushover, "enabled")}
          onChange={(b) => setField("pushover", "enabled", b)}
          path="notify_services.pushover.enabled"
        />
        <SecretField
          label="Pushover API Key"
          value={str(pushover, "APIKey")}
          onChange={(val) => setField("pushover", "APIKey", val)}
          path="notify_services.pushover.APIKey"
        />
        <SecretField
          label="Pushover User Keys"
          value={str(pushover, "UserKeys")}
          onChange={(val) => setField("pushover", "UserKeys", val)}
          path="notify_services.pushover.UserKeys"
        />
        <TextField
          label="Pushover Public URL"
          value={str(pushover, "PublicURL")}
          onChange={(val) => setField("pushover", "PublicURL", val)}
          path="notify_services.pushover.PublicURL"
        />
      </Section>

      <Section title="OneSignal">
        <Toggle
          label="OneSignal Enabled"
          checked={bool(onesignal, "enabled")}
          onChange={(b) => setField("onesignal", "enabled", b)}
          path="notify_services.onesignal.enabled"
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
                      path={`notify_services.onesignal.devices.${deviceId}.friendly_name`}
                    />
                  </td>
                  <td className="pf-device-meta">{deviceStr(device, "device_name")}</td>
                  <td className="pf-device-meta">{deviceStr(device, "app_version")}</td>
                  <td className="pf-devices-delete-col">
                    <button
                      type="button"
                      aria-label={`Delete ${deviceId}`}
                      onClick={() => setPendingDevice(deviceId)}
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
          path="notify_services.influxdb.enabled"
        />
        <TextField
          label="InfluxDB URL"
          value={str(influxdb, "url")}
          onChange={(val) => setField("influxdb", "url", val)}
          path="notify_services.influxdb.url"
        />
        <SecretField
          label="InfluxDB Token"
          value={str(influxdb, "token")}
          onChange={(val) => setField("influxdb", "token", val)}
          path="notify_services.influxdb.token"
        />
        <TextField
          label="InfluxDB Org"
          value={str(influxdb, "org")}
          onChange={(val) => setField("influxdb", "org", val)}
          path="notify_services.influxdb.org"
        />
        <TextField
          label="InfluxDB Bucket"
          value={str(influxdb, "bucket")}
          onChange={(val) => setField("influxdb", "bucket", val)}
          path="notify_services.influxdb.bucket"
        />
      </Section>

      <Section title="MQTT">
        <Toggle
          label="MQTT Enabled"
          checked={bool(mqtt, "enabled")}
          onChange={(b) => setField("mqtt", "enabled", b)}
          path="notify_services.mqtt.enabled"
        />
        <TextField
          label="MQTT Client ID"
          value={str(mqtt, "id")}
          onChange={(val) => setField("mqtt", "id", val)}
          path="notify_services.mqtt.id"
        />
        <TextField
          label="MQTT Broker"
          value={str(mqtt, "broker")}
          onChange={(val) => setField("mqtt", "broker", val)}
          path="notify_services.mqtt.broker"
        />
        {/* schema types port as str — TextField, not NumberField */}
        <TextField
          label="MQTT Port"
          value={str(mqtt, "port")}
          onChange={(val) => setField("mqtt", "port", val)}
          path="notify_services.mqtt.port"
        />
        <TextField
          label="MQTT Username"
          value={str(mqtt, "username")}
          onChange={(val) => setField("mqtt", "username", val)}
          path="notify_services.mqtt.username"
        />
        <SecretField
          label="MQTT Password"
          value={str(mqtt, "password")}
          onChange={(val) => setField("mqtt", "password", val)}
          path="notify_services.mqtt.password"
        />
        <TextField
          label="MQTT Home Assistant Autodiscovery Topic"
          value={str(mqtt, "homeassistant_autodiscovery_topic")}
          onChange={(val) => setField("mqtt", "homeassistant_autodiscovery_topic", val)}
          path="notify_services.mqtt.homeassistant_autodiscovery_topic"
        />
        {/* schema types update_sec as str — TextField, not NumberField */}
        <TextField
          label="MQTT Update Interval"
          value={str(mqtt, "update_sec")}
          onChange={(val) => setField("mqtt", "update_sec", val)}
          path="notify_services.mqtt.update_sec"
        />
      </Section>

      <WledCard
        wled={wled}
        onChange={(next) => setV((s) => ({ ...s, ns: { ...s.ns, wled: next } }))}
      />

      <ConfirmAction
        open={pendingDevice !== null}
        // Flask branches on friendly_name vs device_name at :1659-1670.
        title={`Delete Device ${
          pendingDevice === null ? "" : deviceLabel(devicesOf(onesignal)[pendingDevice])
        }`}
        message="This device will stop receiving notifications. It can only be re-added by registering again from the PiFire Android app."
        onConfirm={() => {
          if (pendingDevice !== null) deleteDevice(pendingDevice);
          setPendingDevice(null);
        }}
        onCancel={() => setPendingDevice(null)}
      />
      <SaveBar onSave={onSave} saving={saving} status={status} dirty={dirty} />
    </SettingsFieldErrorsProvider>
  );
}
