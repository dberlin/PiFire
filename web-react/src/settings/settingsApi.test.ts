import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applySettings, buildSettingsUrl } from "./settingsApi";

describe("buildSettingsUrl", () => {
  it("joins base + /api + path", () => {
    expect(buildSettingsUrl("", "settings")).toBe("/api/settings");
    expect(buildSettingsUrl("http://pi:5000", "settings_update")).toBe(
      "http://pi:5000/api/settings_update",
    );
  });
});

describe("applySettings", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ result: "success", message: "", data: {} }),
    }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("POSTs {settings, flags} to /api/settings_update and maps success", async () => {
    const r = await applySettings("", { globals: { grill_name: "X" } }, ["settings_update"]);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/settings_update");
    const init = fetchMock.mock.calls[0][1];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      settings: { globals: { grill_name: "X" } },
      flags: ["settings_update"],
    });
    expect(r.ok).toBe(true);
  });

  it("maps a non-success envelope to ok:false", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ result: "error", message: "bad" }),
    });
    expect(await applySettings("", {}, [])).toMatchObject({ ok: false, message: "bad" });
  });
});
