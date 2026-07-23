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

function contextWithNotifyServices(notify_services: unknown, mode = "Stop") {
  return {
    settings: { notify_services },
    mode,
  };
}

describe("NotificationsTab", () => {
  it("renders a Section for each of the six simple services + Apprise + OneSignal placeholder", () => {
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
});
