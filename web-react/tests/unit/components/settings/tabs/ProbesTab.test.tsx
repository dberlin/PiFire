import type { ThermocoupleHealthView } from "@pifire/core/contracts/core";
import type { ProbeModuleCatalog } from "@pifire/core/contracts/wizard";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import { ProbesTab } from "../../../../../src/components/settings/tabs/ProbesTab";
import { testQueryClient } from "../../../test-utils";

// DevicesCard's add flow calls the wizard's bus-kind validator over HTTP
// (DevicesCard.tsx submit()). The tab does not own that call and must not be
// made to; stub it the way DevicesCard.test.tsx already does.
rs.mock("../../../../../src/helpers/wizard/wizardApi", () => ({
  validateBusKinds: rs.fn(async () => ({ ok: true })),
}));

// Only applyProbeMap is stubbed; readLiveProbeMap/readLiveProfiles are pure
// narrowing functions and this tab's seeding behaviour is exactly what they do,
// so mocking them would test the mock. rstest requires a SYNC factory, hence the
// `with { rstest: "importActual" }` import attribute rather than `await
// rs.importActual(...)`.
import * as realProbeMapApi from "../../../../../src/helpers/probes/probeMapApi" with {
  rstest: "importActual",
};

rs.mock("../../../../../src/helpers/probes/probeMapApi", () => ({
  ...realProbeMapApi,
  applyProbeMap: rs.fn(async () => ({ ok: true })),
}));

import * as realSettingsApi from "../../../../../src/helpers/settings/settingsApi" with {
  rstest: "importActual",
};

rs.mock("../../../../../src/helpers/settings/settingsApi", () => ({
  ...realSettingsApi,
  applySettings: rs.fn(async () => ({ ok: true, message: "", errors: [] })),
}));

import { applyProbeMap } from "../../../../../src/helpers/probes/probeMapApi";
import { applySettings } from "../../../../../src/helpers/settings/settingsApi";
import { useSettingsDraftStore } from "../../../../../src/helpers/settings/settingsDrafts";

const applyMock = applyProbeMap as ReturnType<typeof rs.fn>;
const applySettingsMock = rs.mocked(applySettings);

// renderRoute (src/test-utils.tsx) supplies an outlet context but no loader
// data, and this tab reads BOTH. Same two-level router, plus a loader on the
// child so useLoaderData() resolves exactly as it does under App.tsx's
// /settings/probes route.
function renderTab(ui: ReactElement, context: unknown, catalog: ProbeModuleCatalog) {
  // Also stands in for SettingsShell's draft store, which is where the working
  // probe map now lives (helpers/settings/settingsDrafts.ts).
  function Host() {
    const store = useSettingsDraftStore((context as { settings?: unknown })?.settings);
    return <Outlet context={{ ...(context as object), ...store }} />;
  }
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <Host />,
        children: [
          { index: true, element: ui, loader: () => catalog, HydrateFallback: () => null },
        ],
      },
    ],
    { initialEntries: ["/"] },
  );
  // A fresh client, not the singleton: this tab now reaches its QueryClient
  // via useQueryClient(), and nothing in this file asserts against cache
  // state -- only that the save flow itself still behaves, so there is
  // nothing the singleton would buy here that a throwaway client does not.
  return render(
    <QueryClientProvider client={testQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const PROFILE = { id: "TWPS00", name: "Thermoworks", A: 1, B: 2, C: 3 };

const CATALOG: ProbeModuleCatalog = {
  modules: {
    virtual_average: {
      friendly_name: "Virtual Average",
      filename: "virtual_average",
      device_specific: { ports: ["VIRT0"], type: "virtual", config: [] },
    },
    ds18b20: {
      friendly_name: "DS18B20 1-Wire",
      filename: "ds18b20",
      device_specific: { ports: ["DS0"], type: "1wire", config: [] },
    },
  },
  requires_install: { virtual_average: false, ds18b20: true },
};

function liveSettings() {
  return {
    probe_settings: {
      probe_profiles: { TWPS00: PROFILE },
      probe_map: {
        probe_devices: [
          {
            device: "ADS1115",
            module: "virtual_average",
            module_filename: "virtual_average",
            ports: ["ADC0"],
            config: {},
          },
        ],
        probe_info: [
          {
            name: "Grill",
            label: "Grill",
            type: "Primary",
            enabled: true,
            device: "ADS1115",
            port: "ADC0",
            profile: PROFILE,
          },
        ],
      },
    },
    thermocouple_health: { inference_policy: "observe" as const },
  };
}

function ctx(mode = "Stop") {
  return { settings: liveSettings(), mode };
}

function health(
  role: ThermocoupleHealthView["role"],
  displayName: string,
  state: ThermocoupleHealthView["report"]["state"],
  outcome: ThermocoupleHealthView["outcome"],
  current = true,
): ThermocoupleHealthView {
  return {
    device: `${displayName} amplifier`,
    port: role === "Aux" ? "KTT2" : "KTT0",
    label: displayName,
    displayName,
    role,
    report: {
      state,
      faults: state === "confirmed" ? ["open", "short"] : [],
      evidence: state === "healthy" ? [] : ["hardware", "junction-collapse"],
      temperatureValid: outcome === "none" || outcome === "notify_only",
      detail: { junction_spread_c: 0.2, window_samples: 12 },
    },
    detector: {
      source: state === "confirmed" ? "mixed" : "software",
      policy: "observe",
    },
    outcome,
    freshness: { current, lastReportedAgeS: current ? 0 : 75 },
  };
}

// No `exact: true` on getByRole: ByRoleOptions has no such member (it is a
// ByText option) and typescript7 rejects it. A plain string `name` is already a
// full accessible-name match, so "Save" cannot pick up "Save probe
// configuration", nor the reverse.
const saveButton = () => screen.getByRole("button", { name: "Save probe configuration" });
const discardButton = () => screen.getByRole("button", { name: "Discard changes" });

async function renameGrillTo(next: string) {
  fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]);
  const dialog = await screen.findByRole("dialog", { name: "edit probe" });
  const input = dialog.querySelector("input[type='text']");
  if (!input) throw new Error("no probe-name input");
  fireEvent.change(input, { target: { value: next } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
}

beforeEach(() => {
  applyMock.mockClear();
  applyMock.mockResolvedValue({ ok: true });
  applySettingsMock.mockClear();
  applySettingsMock.mockResolvedValue({ ok: true, message: "", errors: [] });
});

afterEach(cleanup);

describe("ProbesTab", () => {
  it("seeds both cards from LIVE settings, not from the wizard draft", async () => {
    renderTab(<ProbesTab />, ctx(), CATALOG);
    const devices = await screen.findByRole("region", { name: "Probe devices" });
    expect(devices).toHaveTextContent("ADS1115");
    expect(screen.getByRole("region", { name: "Probe ports" })).toHaveTextContent("Grill");
  });

  it("disables Save until something actually changes", async () => {
    renderTab(<ProbesTab />, ctx(), CATALOG);
    await screen.findByRole("region", { name: "Probe devices" });
    expect(saveButton()).toBeDisabled();
    expect(discardButton()).toBeDisabled();
  });

  it("refuses to save while the grill is running, and says why", async () => {
    renderTab(<ProbesTab />, ctx("Smoke"), CATALOG);
    await screen.findByRole("region", { name: "Probe devices" });
    await renameGrillTo("Pit");
    expect(saveButton()).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/Stop it before changing probe/i);
  });

  it("blocks a module the running system cannot install, via the real add flow", async () => {
    renderTab(<ProbesTab />, ctx(), CATALOG);
    await screen.findByRole("region", { name: "Probe devices" });

    fireEvent.change(screen.getByLabelText("Add device module"), { target: { value: "ds18b20" } });
    const dialog = await screen.findByRole("dialog", { name: "add device" });
    fireEvent.change(dialog.querySelector("input[type='text']") as HTMLInputElement, {
      target: { value: "OneWire" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => {
      expect(screen.getByText(/need the setup wizard/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/need the setup wizard/i)).toHaveTextContent("ds18b20");
    expect(saveButton()).toBeDisabled();
  });

  it("posts the WORKING map, with the edit in it", async () => {
    renderTab(<ProbesTab />, ctx(), CATALOG);
    await screen.findByRole("region", { name: "Probe devices" });
    await renameGrillTo("Pit");

    await waitFor(() => expect(saveButton()).toBeEnabled());
    fireEvent.click(saveButton());

    await waitFor(() => expect(applyMock).toHaveBeenCalledTimes(1));
    const posted = applyMock.mock.calls[0][1] as { probe_info: { name: string }[] };
    expect(posted.probe_info.map((p) => p.name)).toEqual(["Pit"]);
  });

  it("surfaces a server rejection and KEEPS the user's edits", async () => {
    applyMock.mockResolvedValue({
      ok: false,
      message: "Stop the grill before changing probe configuration.",
    });
    renderTab(<ProbesTab />, ctx(), CATALOG);
    await screen.findByRole("region", { name: "Probe devices" });
    await renameGrillTo("Pit");

    await waitFor(() => expect(saveButton()).toBeEnabled());
    fireEvent.click(saveButton());

    await waitFor(() => {
      expect(
        screen.getByText("Stop the grill before changing probe configuration."),
      ).toBeInTheDocument();
    });
    // The store is untouched on every rejection path, so there is no drift to
    // correct -- and throwing the edits away exactly when the user needs to fix
    // them would be the worse bug.
    expect(screen.getByRole("region", { name: "Probe ports" })).toHaveTextContent("Pit");
    expect(saveButton()).toBeEnabled();
  });

  it("Discard puts the live map back", async () => {
    renderTab(<ProbesTab />, ctx(), CATALOG);
    await screen.findByRole("region", { name: "Probe devices" });
    await renameGrillTo("Pit");

    await waitFor(() => expect(discardButton()).toBeEnabled());
    fireEvent.click(discardButton());

    expect(screen.getByRole("region", { name: "Probe ports" })).toHaveTextContent("Grill");
    expect(saveButton()).toBeDisabled();
  });

  it("links to the probe tuner", async () => {
    //  The tuner is a top-level route, not a settings tab: it opens a live
    //  tuning session. Flask reaches it from the navbar; the React navbar has
    //  no Tuner entry, so this link is the only way in.
    renderTab(<ProbesTab />, ctx(), CATALOG);
    await screen.findByRole("region", { name: "Probe devices" });
    expect(screen.getByRole("link", { name: "Tune a probe" })).toHaveAttribute("href", "/tuner");
  });

  it("offers the exact software detection policies with truthful selected impact copy", async () => {
    renderTab(<ProbesTab />, ctx(), CATALOG);
    const selector = await screen.findByLabelText("Software thermocouple detection");

    expect(selector).toHaveValue("observe");
    expect(
      screen.getByText("Reports confirmed software-detected faults without stopping heating."),
    ).toBeInTheDocument();
    fireEvent.change(selector, { target: { value: "enforce" } });
    expect(
      screen.getByText("Stops heating when the control probe has a confirmed fault."),
    ).toBeInTheDocument();
  });

  it("saves a policy-only change without rewriting the probe map", async () => {
    renderTab(<ProbesTab />, ctx(), CATALOG);
    fireEvent.change(await screen.findByLabelText("Software thermocouple detection"), {
      target: { value: "enforce" },
    });

    expect(saveButton()).toBeEnabled();
    fireEvent.click(saveButton());

    await waitFor(() =>
      expect(applySettingsMock).toHaveBeenCalledWith(
        "",
        { thermocouple_health: { inference_policy: "enforce" } },
        [],
      ),
    );
    expect(applyMock).not.toHaveBeenCalled();
  });

  it("renders Primary, Food, and Aux health details including source, policy, causes, evidence, and stale age", async () => {
    renderTab(
      <ProbesTab />,
      {
        ...ctx(),
        thermocoupleHealth: [
          health("Primary", "Grill", "healthy", "none"),
          health("Food", "Brisket", "suspected", "none"),
          health("Aux", "Ambient", "confirmed", "unavailable", false),
        ],
      },
      CATALOG,
    );

    const region = await screen.findByRole("region", { name: "Thermocouple health" });
    expect(region).toHaveTextContent("Primary · Grill");
    expect(region).toHaveTextContent("Food · Brisket");
    expect(region).toHaveTextContent("Aux · Ambient");
    expect(region).toHaveTextContent("Last reported: PROBE UNAVAILABLE");
    expect(region).toHaveTextContent("Hardware + software");
    expect(region).toHaveTextContent("Observe");
    expect(region).toHaveTextContent("open, short");
    expect(region).toHaveTextContent("hardware, junction-collapse");
    expect(region).toHaveTextContent("junction spread c: 0.2");
    expect(region).toHaveTextContent("window samples: 12");
  });

  it("qualifies retained health and its frozen report age when transport is unreachable", async () => {
    renderTab(
      <ProbesTab />,
      {
        ...ctx(),
        phase: "unreachable",
        thermocoupleHealth: [health("Primary", "Grill", "confirmed", "unavailable", true)],
      },
      CATALOG,
    );

    const region = await screen.findByRole("region", { name: "Thermocouple health" });
    expect(region).toHaveTextContent("Last reported: PROBE UNAVAILABLE");
    expect(screen.getByText("Report age at last update").closest("div")).toHaveTextContent("0s");
  });
});
