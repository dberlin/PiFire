import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import {
  applySettings,
  buildSettingsUrl,
  getControllerMetadata,
  getMode,
  getSettings,
} from "../../../../src/helpers/settings/settingsApi";

describe("buildSettingsUrl", () => {
  it("joins base + /api + path", () => {
    expect(buildSettingsUrl("", "settings")).toBe("/api/settings");
    expect(buildSettingsUrl("http://pi:5000", "settings_update")).toBe(
      "http://pi:5000/api/settings_update",
    );
  });
});

describe("getSettings", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it("resolves the parsed settings object from a {settings:{...}} envelope", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({
        ok: true,
        json: async () => ({ settings: { globals: { grill_name: "Y" } } }),
      })),
    );
    await expect(getSettings("")).resolves.toEqual({ globals: { grill_name: "Y" } });
  });

  it("throws when the HTTP response is not ok", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
    );
    await expect(getSettings("")).rejects.toThrow("GET /api/settings failed: HTTP 500");
  });
});

describe("getMode", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it("returns the mode string from a {data:{mode}} envelope", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({ ok: true, json: async () => ({ data: { mode: "Hold" } }) })),
    );
    await expect(getMode("")).resolves.toBe("Hold");
  });

  it("returns empty string when the HTTP response is not ok", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({ ok: false, json: async () => ({}) })),
    );
    await expect(getMode("")).resolves.toBe("");
  });

  it("fails open to empty string when fetch rejects", async () => {
    rs.stubGlobal("fetch", rs.fn().mockRejectedValue(new Error("down")));
    await expect(getMode("")).resolves.toBe("");
  });
});

describe("getControllerMetadata", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it("resolves the parsed metadata envelope on a 201", async () => {
    const fixture = { metadata: { pid: { friendly_name: "PID Standard", config: [] } } };
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({ ok: true, status: 201, json: async () => fixture })),
    );
    await expect(getControllerMetadata("")).resolves.toEqual(fixture);
  });

  it("fails open to null when the HTTP response is not ok", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
    );
    await expect(getControllerMetadata("")).resolves.toBeNull();
  });

  it("fails open to null when fetch rejects", async () => {
    rs.stubGlobal("fetch", rs.fn().mockRejectedValue(new Error("down")));
    await expect(getControllerMetadata("")).resolves.toBeNull();
  });
});

describe("applySettings", () => {
  let fetchMock: ReturnType<typeof rs.fn>;
  beforeEach(() => {
    fetchMock = rs.fn(async () => ({
      ok: true,
      json: async () => ({ result: "success", message: "", data: {} }),
    }));
    rs.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    rs.unstubAllGlobals();
  });

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

  it("maps a non-ok HTTP response to ok:false with the status code", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 503 });
    expect(await applySettings("", {}, [])).toMatchObject({ ok: false, message: "HTTP 503" });
  });

  it("maps a rejected fetch to ok:false with the error message", async () => {
    fetchMock.mockRejectedValueOnce(new Error("network down"));
    expect(await applySettings("", {}, [])).toMatchObject({
      ok: false,
      message: "network down",
    });
  });
});
