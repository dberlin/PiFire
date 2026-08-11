import { afterEach, describe, expect, rs, test } from "@rstest/core";
import type { WizardState } from "../../../../src/helpers/contracts/wizard.gen";

afterEach(() => {
  rs.resetAllMocks();
});

describe("wizardApi", () => {
  test("getWizardState fetches /api/wizard/state and returns parsed JSON", async () => {
    const fake: Partial<WizardState> = { has_draft: false, control_mode: "Stop" };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { getWizardState } = await import("../../../../src/helpers/wizard/wizardApi");
    const state = await getWizardState("");
    expect(state.control_mode).toBe("Stop");
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]).toContain(
      "/api/wizard/state",
    );
  });

  test("saveDraft posts /api/wizard/draft and returns ok status", async () => {
    globalThis.fetch = rs
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ result: "success" }) }) as never;
    const { saveDraft } = await import("../../../../src/helpers/wizard/wizardApi");
    const ok = await saveDraft("", {
      selections: { grillplatform: "", display: "", distance: "", probes: "" },
      settings_dep_values: { grillplatform: {}, display: {}, distance: {}, probes: {} },
      display_config: {},
      probe_map: { probe_devices: [], probe_info: [] },
      probes_units: "F",
    });
    expect(ok).toBe(true);
    const call = (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/wizard/draft");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({
      selections: { grillplatform: "", display: "", distance: "", probes: "" },
      settings_dep_values: { grillplatform: {}, display: {}, distance: {}, probes: {} },
      display_config: {},
      probe_map: { probe_devices: [], probe_info: [] },
      probes_units: "F",
    });
  });

  test("saveDraft rejects an error envelope even if the transport status is 200", async () => {
    globalThis.fetch = rs.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ result: "error", message: "invalid_request" }),
    }) as never;
    const { saveDraft } = await import("../../../../src/helpers/wizard/wizardApi");

    const ok = await saveDraft("", {
      selections: {},
      settings_dep_values: {},
      display_config: {},
      probe_map: { probe_devices: [], probe_info: [] },
      probes_units: "F",
    });

    expect(ok).toBe(false);
  });

  test("cancelWizard posts /api/wizard/cancel and returns ok status", async () => {
    globalThis.fetch = rs.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ result: "success" }),
    }) as never;
    const { cancelWizard } = await import("../../../../src/helpers/wizard/wizardApi");
    const ok = await cancelWizard("");
    expect(ok).toBe(true);
    const call = (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/wizard/cancel");
    expect((call[1] as RequestInit).method).toBe("POST");
  });

  test("cancelWizard reports a failed cancel as false rather than throwing", async () => {
    // The caller uses this to decide whether it is safe to navigate away: a
    // false must keep the user in the wizard, because globals.first_time_setup
    // is still set and the dashboard would bounce them straight back.
    globalThis.fetch = rs
      .fn()
      .mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }) as never;
    const { cancelWizard } = await import("../../../../src/helpers/wizard/wizardApi");
    expect(await cancelWizard("")).toBe(false);
  });

  test("scan posts /api/wizard/scan and returns parsed JSON", async () => {
    const fake = {
      groups: [{ title: "By Bus", items: [{ value: "1", label: "i2c-1" }] }],
      error: null,
    };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { scan } = await import("../../../../src/helpers/wizard/wizardApi");
    const result = await scan("", { kind: "extended" });
    expect(result.groups[0].title).toBe("By Bus");
    const call = (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/wizard/scan");
  });

  test("finishWizard surfaces 409 as ok:false with status", async () => {
    globalThis.fetch = rs.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ result: "error", message: "system_active" }),
    }) as never;
    const { finishWizard } = await import("../../../../src/helpers/wizard/wizardApi");
    const r = await finishWizard("", {
      selections: {},
      settings_dep_values: {},
      display_config: {},
      probe_map: { probe_devices: [], probe_info: [] },
      probes_units: "F",
    });
    expect(r.ok).toBe(false);
    expect(r.status).toBe(409);
    expect(r.message).toBe("system_active");
  });

  test("finishWizard surfaces 200 success as ok:true", async () => {
    globalThis.fetch = rs.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ result: "success" }),
    }) as never;
    const { finishWizard } = await import("../../../../src/helpers/wizard/wizardApi");
    const r = await finishWizard("", {
      selections: {},
      settings_dep_values: {},
      display_config: {},
      probe_map: { probe_devices: [], probe_info: [] },
      probes_units: "F",
    });
    const call = (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0];
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({
      selections: {},
      settings_dep_values: {},
      display_config: {},
      probe_map: { probe_devices: [], probe_info: [] },
      probes_units: "F",
    });
    expect(r.ok).toBe(true);
    expect(r.status).toBe(200);
    expect(r.message).toBeUndefined();
  });

  test("getInstallStatus fetches /api/wizard/installstatus and returns parsed JSON", async () => {
    const fake = { percent: 42, status: "Installing...", output: "" };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { getInstallStatus } = await import("../../../../src/helpers/wizard/wizardApi");
    const status = await getInstallStatus("");
    expect(status.percent).toBe(42);
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]).toContain(
      "/api/wizard/installstatus",
    );
  });

  test("scanBluetooth posts to /scan/bluetooth and returns rows", async () => {
    const fake = { rows: [{ name: "iBBQ", hw_id: "AA", info: "" }], error: null };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { scanBluetooth } = await import("../../../../src/helpers/wizard/wizardApi");
    const r = await scanBluetooth("");
    const call = (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/wizard/scan/bluetooth");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(r.rows[0].hw_id).toBe("AA");
  });

  test("scanThermoworks posts email/password to /scan/thermoworks and returns rows", async () => {
    const fake = {
      rows: [{ label: "TW", type: "smoke", serial: "SN1", num_channels: 4 }],
      error: null,
    };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { scanThermoworks } = await import("../../../../src/helpers/wizard/wizardApi");
    const r = await scanThermoworks("", "a@b.com", "pw");
    const call = (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/wizard/scan/thermoworks");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({
      email: "a@b.com",
      password: "pw",
    });
    expect(r.rows[0].serial).toBe("SN1");
  });

  test("validateBusKinds posts probe_devices and returns ok", async () => {
    globalThis.fetch = rs
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ ok: true }) }) as never;
    const { validateBusKinds } = await import("../../../../src/helpers/wizard/wizardApi");
    const r = await validateBusKinds("", [
      { device: "D1", module: "m", module_filename: "m", ports: [], config: {} },
    ]);
    const call = (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/wizard/probes/validate-bus-kinds");
    expect(r.ok).toBe(true);
  });

  test("fetchModuleValues posts section/module to /module-values and returns parsed JSON", async () => {
    const fake = { settings: { system_type: "raspberry_pi_all" }, config: {} };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { fetchModuleValues } = await import("../../../../src/helpers/wizard/wizardApi");
    const r = await fetchModuleValues("", "grillplatform", "pcb_4.x.x");
    const call = (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/wizard/module-values");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({
      section: "grillplatform",
      module: "pcb_4.x.x",
    });
    expect(r.settings.system_type).toBe("raspberry_pi_all");
  });

  test("fetchModuleValues throws on a non-ok response", async () => {
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: false, status: 400 }) as never;
    const { fetchModuleValues } = await import("../../../../src/helpers/wizard/wizardApi");
    await expect(fetchModuleValues("", "grillplatform", "nope")).rejects.toThrow();
  });
});
