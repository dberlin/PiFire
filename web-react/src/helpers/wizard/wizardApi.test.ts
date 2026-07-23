import { afterEach, describe, expect, rs, test } from "@rstest/core";
import type { WizardState } from "./wizardTypes";

afterEach(() => {
  rs.resetAllMocks();
});

describe("wizardApi", () => {
  test("getWizardState fetches /api/wizard/state and returns parsed JSON", async () => {
    const fake: Partial<WizardState> = { has_draft: false, control_mode: "Stop" };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { getWizardState } = await import("./wizardApi");
    const state = await getWizardState("");
    expect(state.control_mode).toBe("Stop");
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]).toContain(
      "/api/wizard/state",
    );
  });

  test("saveDraft posts /api/wizard/draft and returns ok status", async () => {
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => ({}) }) as never;
    const { saveDraft } = await import("./wizardApi");
    const ok = await saveDraft("", {
      selections: { grillplatform: "", display: "", distance: "", probes: "" },
      settings_dep_values: { grillplatform: {}, display: {}, distance: {}, probes: {} },
      display_config: {},
    });
    expect(ok).toBe(true);
    const call = (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/wizard/draft");
    expect((call[1] as RequestInit).method).toBe("POST");
  });

  test("scan posts /api/wizard/scan and returns parsed JSON", async () => {
    const fake = {
      groups: [{ title: "By Bus", items: [{ value: "1", label: "i2c-1" }] }],
      error: null,
    };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { scan } = await import("./wizardApi");
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
    const { finishWizard } = await import("./wizardApi");
    const r = await finishWizard("", {
      selections: {},
      settings_dep_values: {},
      display_config: {},
    } as never);
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
    const { finishWizard } = await import("./wizardApi");
    const r = await finishWizard("", {
      selections: {},
      settings_dep_values: {},
      display_config: {},
    } as never);
    expect(r.ok).toBe(true);
    expect(r.status).toBe(200);
    expect(r.message).toBeUndefined();
  });

  test("getInstallStatus fetches /api/wizard/installstatus and returns parsed JSON", async () => {
    const fake = { percent: 42, status: "Installing...", output: "" };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { getInstallStatus } = await import("./wizardApi");
    const status = await getInstallStatus("");
    expect(status.percent).toBe(42);
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]).toContain(
      "/api/wizard/installstatus",
    );
  });
});
