import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import type { ProbeModuleCatalog } from "../../../helpers/probes/probeMapTypes";
import { ProbesTab } from "./ProbesTab";

// DevicesCard's add flow calls the wizard's bus-kind validator over HTTP
// (DevicesCard.tsx submit()). The tab does not own that call and must not be
// made to; stub it the way DevicesCard.test.tsx already does.
rs.mock("../../../helpers/wizard/wizardApi", () => ({
  validateBusKinds: rs.fn(async () => ({ ok: true })),
}));

// Only applyProbeMap is stubbed; readLiveProbeMap/readLiveProfiles are pure
// narrowing functions and this tab's seeding behaviour is exactly what they do,
// so mocking them would test the mock. rstest requires a SYNC factory, hence the
// `with { rstest: "importActual" }` import attribute rather than `await
// rs.importActual(...)`.
import * as realProbeMapApi from "../../../helpers/probes/probeMapApi" with {
  rstest: "importActual",
};

rs.mock("../../../helpers/probes/probeMapApi", () => ({
  ...realProbeMapApi,
  applyProbeMap: rs.fn(async () => ({ ok: true })),
}));

import { applyProbeMap } from "../../../helpers/probes/probeMapApi";
import { useSettingsDraftStore } from "../../../helpers/settings/settingsDrafts";

const applyMock = applyProbeMap as ReturnType<typeof rs.fn>;

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
  return render(<RouterProvider router={router} />);
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
  };
}

function ctx(mode = "Stop") {
  return { settings: liveSettings(), mode };
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
});
