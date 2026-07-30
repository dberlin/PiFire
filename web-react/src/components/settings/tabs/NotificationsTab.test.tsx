import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { renderRoute } from "../../../test-utils";
import { NotificationsTab } from "./NotificationsTab";

const saveMock = rs.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
rs.mock("../../../helpers/settings/useSaveSettings", () => ({
  useSaveSettings: () => ({
    save: saveMock,
    saving: false,
    status: { kind: "idle" } as const,
    baseUrl: "",
  }),
}));

beforeEach(() => {
  saveMock.mockClear();
});

afterEach(cleanup);

// Real shape (abbreviated but every key the tab reads/writes present),
// mirroring common/defaults.py default_notify_services().
const NOTIFY_SERVICES = {
  apprise: {
    enabled: false,
    locations: ["mailto://user@example.com"],
  },
  ifttt: {
    enabled: false,
    APIKey: "",
  },
  pushbullet: {
    enabled: false,
    APIKey: "",
    PublicURL: "",
  },
  pushover: {
    enabled: false,
    APIKey: "",
    UserKeys: "",
    PublicURL: "",
  },
  onesignal: {
    enabled: false,
    uuid: "uuid-1234",
    app_id: "app-5678",
    devices: {
      dev1: { friendly_name: "Phone", device_name: "Pixel", app_version: "1.0" },
    },
  },
  influxdb: {
    enabled: false,
    url: "",
    token: "",
    org: "",
    bucket: "",
  },
  mqtt: {
    enabled: false,
    id: "PiFire",
    broker: "homeassistant.local",
    port: "1883",
    username: "",
    password: "",
    homeassistant_autodiscovery_topic: "homeassistant",
    update_sec: "30",
  },
  wled: {
    enabled: false,
    device_address: "wled.local",
    notify_duration: 120,
    use_profiles: true,
    use_suggested_presets: false,
    profile_numbers: { idle: 1, cooking: 2 },
    mode_presets: { Hold: 1, Prime: 2 },
    event_presets: { Grill_Error: 1, Timer_Expired: 2 },
    suggested_config: { cooking_color: "blue", idle_brightness: 20, night_mode: false },
  },
};

// player-id keyed devices, mirroring onesignal.devices as populated by the
// mobile app's self-registration flow (uuid/app_id are siblings, not rendered
// here).
const DEVICES = {
  "player-abc": { friendly_name: "Danny iPhone", device_name: "iPhone15", app_version: "1.2.0" },
  "player-xyz": { friendly_name: "Kitchen Tablet", device_name: "SM-T500", app_version: "1.1.0" },
};

const NOTIFY_SERVICES_WITH_DEVICES = {
  ...NOTIFY_SERVICES,
  onesignal: { ...NOTIFY_SERVICES.onesignal, devices: DEVICES },
};

const NOTIFY_SERVICES_NO_DEVICES = {
  ...NOTIFY_SERVICES,
  onesignal: { ...NOTIFY_SERVICES.onesignal, devices: {} },
};

function contextWithNotifyServices(notify_services: unknown, mode = "Stop") {
  return {
    settings: { notify_services },
    mode,
  };
}

// NumberField wraps its input in a <label> whose text also carries the suffix,
// so getByLabelText(field) does not match. Reach the input via the label span.
function inputFor(label: string): HTMLInputElement {
  const input = screen.getByText(label).closest("label")?.querySelector("input");
  if (!input) throw new Error(`no input for field "${label}"`);
  return input;
}

describe("NotificationsTab", () => {
  it("renders a Section for each of the six simple services + Apprise + OneSignal", () => {
    renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES));

    expect(screen.getByText("Apprise")).toBeInTheDocument();
    expect(screen.getByText("IFTTT")).toBeInTheDocument();
    expect(screen.getByText("Pushbullet")).toBeInTheDocument();
    expect(screen.getByText("Pushover")).toBeInTheDocument();
    expect(screen.getByText("OneSignal")).toBeInTheDocument();
    expect(screen.getByText("InfluxDB")).toBeInTheDocument();
    expect(screen.getByText("MQTT")).toBeInTheDocument();
    expect(screen.getByText("WLED")).toBeInTheDocument();
  });

  // Which fields are secret is a decision about THIS tab, not about SecretField,
  // so it is pinned here: swapping any one of the six back to a plain TextField
  // -- or masking a field that is not a credential -- fails.
  it("masks exactly the six credential fields", () => {
    renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES));

    const secret = [
      "IFTTT API Key",
      "Pushbullet API Key",
      "Pushover API Key",
      "Pushover User Keys",
      "InfluxDB Token",
      "MQTT Password",
    ];
    for (const label of secret) {
      expect(screen.getByLabelText(label), label).toHaveAttribute("type", "password");
    }
    expect(screen.getAllByRole("button", { name: /^Show / })).toHaveLength(secret.length);

    // The non-secret neighbours in the same sections stay readable -- a URL or a
    // bucket name is not a credential, and masking one only costs the user the
    // ability to check what they typed.
    for (const label of [
      "Pushbullet Public URL",
      "Pushover Public URL",
      "InfluxDB URL",
      "InfluxDB Org",
      "InfluxDB Bucket",
      "MQTT Client ID",
      "MQTT Broker",
      "MQTT Username",
      "WLED Device Address",
    ]) {
      expect(screen.getByLabelText(label), label).toHaveAttribute("type", "text");
    }
  });

  it("renders loaded values for each service", () => {
    renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES));

    expect(screen.getByDisplayValue("homeassistant.local")).toBeInTheDocument();
    expect(screen.getByDisplayValue("1883")).toBeInTheDocument();
    expect(screen.getByDisplayValue("wled.local")).toBeInTheDocument();
    expect(screen.getByDisplayValue("120")).toBeInTheDocument();
  });

  it("toggling ifttt.enabled + setting APIKey and saving produces the exact delta with untouched services preserved and flags settings_update", async () => {
    renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES));

    fireEvent.click(screen.getByRole("button", { name: "IFTTT Enabled" }));
    fireEvent.change(screen.getByLabelText("IFTTT API Key"), {
      target: { value: "my-ifttt-key" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(saveMock).toHaveBeenCalledWith(
      {
        notify_services: {
          ...NOTIFY_SERVICES,
          ifttt: { enabled: true, APIKey: "my-ifttt-key" },
        },
      },
      ["settings_update"],
    );

    // Explicitly confirm the untouched mqtt subtree is byte-identical.
    const calledDelta = saveMock.mock.calls[0][0] as {
      notify_services: { mqtt: unknown };
    };
    expect(calledDelta.notify_services.mqtt).toEqual(NOTIFY_SERVICES.mqtt);
  });

  it("editing MQTT port keeps it a string in the saved delta", async () => {
    renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES));

    const portInput = screen.getByLabelText("MQTT Port");
    fireEvent.change(portInput, { target: { value: "8883" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    const calledDelta = saveMock.mock.calls[0][0] as {
      notify_services: { mqtt: { port: unknown } };
    };
    expect(calledDelta.notify_services.mqtt.port).toBe("8883");
    expect(typeof calledDelta.notify_services.mqtt.port).toBe("string");
  });

  it("adding an Apprise location via StringListField grows the saved locations array", async () => {
    renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES));

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    const calledDelta = saveMock.mock.calls[0][0] as {
      notify_services: { apprise: { locations: string[] } };
    };
    expect(calledDelta.notify_services.apprise.locations).toEqual([
      "mailto://user@example.com",
      "",
    ]);
  });

  it("WLED notify_duration is a NumberField saved as a number", async () => {
    renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES));

    const durationInput = screen.getByLabelText("WLED Notify Duration");
    fireEvent.change(durationInput, { target: { value: "45" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    const calledDelta = saveMock.mock.calls[0][0] as {
      notify_services: { wled: { notify_duration: unknown } };
    };
    expect(calledDelta.notify_services.wled.notify_duration).toBe(45);
    expect(typeof calledDelta.notify_services.wled.notify_duration).toBe("number");
  });

  it("editing every field across all six simple services + toggles produces the exact full delta", async () => {
    renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES));

    fireEvent.click(screen.getByRole("button", { name: "Apprise Enabled" }));
    fireEvent.click(screen.getByRole("button", { name: "Pushbullet Enabled" }));
    fireEvent.change(screen.getByLabelText("Pushbullet API Key"), {
      target: { value: "pb-key" },
    });
    fireEvent.change(screen.getByLabelText("Pushbullet Public URL"), {
      target: { value: "https://pb.example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Pushover Enabled" }));
    fireEvent.change(screen.getByLabelText("Pushover API Key"), {
      target: { value: "po-key" },
    });
    fireEvent.change(screen.getByLabelText("Pushover User Keys"), {
      target: { value: "user-1,user-2" },
    });
    fireEvent.change(screen.getByLabelText("Pushover Public URL"), {
      target: { value: "https://po.example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "OneSignal Enabled" }));
    fireEvent.click(screen.getByRole("button", { name: "InfluxDB Enabled" }));
    fireEvent.change(screen.getByLabelText("InfluxDB URL"), {
      target: { value: "https://influx.example.com" },
    });
    fireEvent.change(screen.getByLabelText("InfluxDB Token"), {
      target: { value: "tok" },
    });
    fireEvent.change(screen.getByLabelText("InfluxDB Org"), {
      target: { value: "org1" },
    });
    fireEvent.change(screen.getByLabelText("InfluxDB Bucket"), {
      target: { value: "bucket1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "MQTT Enabled" }));
    fireEvent.change(screen.getByLabelText("MQTT Client ID"), {
      target: { value: "Grill1" },
    });
    fireEvent.change(screen.getByLabelText("MQTT Broker"), {
      target: { value: "mqtt.example.com" },
    });
    fireEvent.change(screen.getByLabelText("MQTT Username"), {
      target: { value: "user" },
    });
    fireEvent.change(screen.getByLabelText("MQTT Password"), {
      target: { value: "pass" },
    });
    fireEvent.change(screen.getByLabelText("MQTT Home Assistant Autodiscovery Topic"), {
      target: { value: "ha" },
    });
    fireEvent.change(screen.getByLabelText("MQTT Update Interval"), {
      target: { value: "60" },
    });
    fireEvent.click(screen.getByRole("button", { name: "WLED Enabled" }));
    fireEvent.change(screen.getByLabelText("WLED Device Address"), {
      target: { value: "wled2.local" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(saveMock).toHaveBeenCalledWith(
      {
        notify_services: {
          ...NOTIFY_SERVICES,
          apprise: { ...NOTIFY_SERVICES.apprise, enabled: true },
          pushbullet: { enabled: true, APIKey: "pb-key", PublicURL: "https://pb.example.com" },
          pushover: {
            enabled: true,
            APIKey: "po-key",
            UserKeys: "user-1,user-2",
            PublicURL: "https://po.example.com",
          },
          onesignal: { ...NOTIFY_SERVICES.onesignal, enabled: true },
          influxdb: {
            enabled: true,
            url: "https://influx.example.com",
            token: "tok",
            org: "org1",
            bucket: "bucket1",
          },
          mqtt: {
            enabled: true,
            id: "Grill1",
            broker: "mqtt.example.com",
            port: "1883",
            username: "user",
            password: "pass",
            homeassistant_autodiscovery_topic: "ha",
            update_sec: "60",
          },
          wled: { ...NOTIFY_SERVICES.wled, enabled: true, device_address: "wled2.local" },
        },
      },
      ["settings_update"],
    );
  });

  it("handles a missing notify_services subtree without crashing", () => {
    renderRoute(<NotificationsTab />, { settings: {}, mode: "Stop" });

    expect(screen.getByText("Apprise")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  describe("OneSignal devices manager", () => {
    it("renders a real table with one row per device showing friendly_name + device_name + app_version", () => {
      renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES_WITH_DEVICES));

      const table = screen.getByRole("table");
      expect(table).toBeInTheDocument();
      expect(screen.getByRole("columnheader", { name: "Friendly Name" })).toBeInTheDocument();
      expect(screen.getByRole("columnheader", { name: "Device" })).toBeInTheDocument();
      expect(screen.getByRole("columnheader", { name: "App Version" })).toBeInTheDocument();
      // Two device rows, each a <tr> in <tbody>.
      expect(screen.getAllByRole("row")).toHaveLength(3); // header + 2 devices

      expect(screen.getByDisplayValue("Danny iPhone")).toBeInTheDocument();
      expect(screen.getByDisplayValue("Kitchen Tablet")).toBeInTheDocument();
      expect(screen.getByText("iPhone15")).toBeInTheDocument();
      expect(screen.getByText("SM-T500")).toBeInTheDocument();
      expect(screen.getByText("1.2.0")).toBeInTheDocument();
      expect(screen.getByText("1.1.0")).toBeInTheDocument();
    });

    it("does not render onesignal.uuid or app_id anywhere in the DOM (siblings of devices, intentionally hidden)", () => {
      renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES_WITH_DEVICES));

      expect(screen.queryByText(NOTIFY_SERVICES.onesignal.uuid)).not.toBeInTheDocument();
      expect(screen.queryByText(NOTIFY_SERVICES.onesignal.app_id)).not.toBeInTheDocument();
      expect(screen.queryByDisplayValue(NOTIFY_SERVICES.onesignal.uuid)).not.toBeInTheDocument();
      expect(screen.queryByDisplayValue(NOTIFY_SERVICES.onesignal.app_id)).not.toBeInTheDocument();
    });

    it("uses the row's player-id (not device_name) for friendly-name-field and delete-button a11y identity, so two devices sharing a device_name can't collide", () => {
      const SAME_MODEL_DEVICES = {
        "player-1": { friendly_name: "", device_name: "iPhone15", app_version: "1.0" },
        "player-2": { friendly_name: "", device_name: "iPhone15", app_version: "1.0" },
      };
      renderRoute(
        <NotificationsTab />,
        contextWithNotifyServices({
          ...NOTIFY_SERVICES,
          onesignal: { ...NOTIFY_SERVICES.onesignal, devices: SAME_MODEL_DEVICES },
        }),
      );

      // Both rows resolve to distinct, stable labels/aria-labels despite an
      // identical device_name -- no "multiple elements found" collision.
      expect(screen.getByLabelText("Friendly Name (player-1)")).toBeInTheDocument();
      expect(screen.getByLabelText("Friendly Name (player-2)")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Delete player-1" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Delete player-2" })).toBeInTheDocument();
    });

    it("editing a device's friendly_name and saving updates only that device, leaving device_name/app_version and the other device untouched", async () => {
      renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES_WITH_DEVICES));

      fireEvent.change(screen.getByLabelText("Friendly Name (player-abc)"), {
        target: { value: "Danny's Phone" },
      });

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));

      const calledDelta = saveMock.mock.calls[0][0] as {
        notify_services: {
          onesignal: { devices: Record<string, Record<string, unknown>> };
        };
      };
      const savedDevices = calledDelta.notify_services.onesignal.devices;
      expect(savedDevices["player-abc"]).toEqual({
        friendly_name: "Danny's Phone",
        device_name: "iPhone15",
        app_version: "1.2.0",
      });
      expect(savedDevices["player-xyz"]).toEqual(DEVICES["player-xyz"]);
    });

    it("deleting a device removes it from the saved devices map", async () => {
      renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES_WITH_DEVICES));

      fireEvent.click(screen.getByRole("button", { name: "Delete player-xyz" }));
      // The delete is now behind a confirm (Flask index.html:1651-1678).
      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));

      const calledDelta = saveMock.mock.calls[0][0] as {
        notify_services: {
          onesignal: { devices: Record<string, Record<string, unknown>> };
        };
      };
      const savedDevices = calledDelta.notify_services.onesignal.devices;
      expect(savedDevices["player-xyz"]).toBeUndefined();
      expect(savedDevices["player-abc"]).toEqual(DEVICES["player-abc"]);
    });

    it("renders the empty-state hint and no rows when there are no devices, without crashing", () => {
      renderRoute(<NotificationsTab />, contextWithNotifyServices(NOTIFY_SERVICES_NO_DEVICES));

      expect(
        screen.getByText(
          "No devices registered. Devices register automatically when you sign in on the PiFire mobile app.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(screen.queryByLabelText(/Friendly Name/)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
    });
  });
  // index.html:2003 — Flask's 3600 is UI-only (the schema has ge=0 and no
  // upper), but it is the sensible ceiling: a notification lasting longer than
  // an hour is a stuck notification.
  it("caps the WLED notify duration at 3600 seconds", () => {
    renderRoute(<NotificationsTab />, { settings: {}, mode: "Stop" });
    const el = inputFor("WLED Notify Duration");
    expect(el).toHaveAttribute("min", "0");
    expect(el).toHaveAttribute("max", "3600");

    fireEvent.change(el, { target: { value: "99999" } });
    fireEvent.blur(el);
    expect(inputFor("WLED Notify Duration")).toHaveValue(3600);
  });
  // Flask puts the OneSignal delete behind a Bootstrap modal naming the device
  // (index.html:1651-1678), branching on friendly_name vs device_name at
  // :1659-1670. React deleted on the first click.
  describe("OneSignal device delete confirmation (M15)", () => {
    const deviceFixture = () => ({
      settings: {
        notify_services: {
          onesignal: {
            enabled: true,
            devices: {
              "player-1": {
                friendly_name: "Kitchen Tablet",
                device_name: "Pixel 7",
                app_version: "1.2",
              },
              "player-2": { friendly_name: "", device_name: "iPhone 14", app_version: "1.3" },
            },
          },
        },
      },
      mode: "Stop",
    });

    it("asks before deleting, naming the device by its friendly name", () => {
      renderRoute(<NotificationsTab />, deviceFixture());

      fireEvent.click(screen.getByRole("button", { name: "Delete player-1" }));
      expect(document.querySelector(".pf-modal-title")).toHaveTextContent("Kitchen Tablet");
      // The row is still there while the dialog is open.
      expect(screen.getByRole("button", { name: "Delete player-1" })).toBeInTheDocument();
    });

    it("falls back to device_name when friendly_name is empty (index.html:1659-1670)", () => {
      renderRoute(<NotificationsTab />, deviceFixture());

      fireEvent.click(screen.getByRole("button", { name: "Delete player-2" }));
      // The device name also appears in its table row, so scope to the dialog.
      expect(document.querySelector(".pf-modal-title")).toHaveTextContent("iPhone 14");
    });

    it("keeps the row on Cancel", () => {
      renderRoute(<NotificationsTab />, deviceFixture());

      fireEvent.click(screen.getByRole("button", { name: "Delete player-1" }));
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

      expect(screen.getByRole("button", { name: "Delete player-1" })).toBeInTheDocument();
    });

    it("removes the row on Confirm and drops it from the next save delta", async () => {
      renderRoute(<NotificationsTab />, deviceFixture());

      fireEvent.click(screen.getByRole("button", { name: "Delete player-1" }));
      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

      expect(screen.queryByRole("button", { name: "Delete player-1" })).toBeNull();
      expect(screen.getByRole("button", { name: "Delete player-2" })).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));

      const [delta] = saveMock.mock.calls[0];
      expect(delta.notify_services.onesignal.devices["player-1"]).toBeUndefined();
      expect(delta.notify_services.onesignal.devices["player-2"]).toBeDefined();
    });
  });
});
