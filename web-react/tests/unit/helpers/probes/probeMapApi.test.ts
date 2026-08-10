import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import {
  applyProbeMap,
  getProbeModules,
  readLiveProbeMap,
  readLiveProfiles,
} from "../../../../src/helpers/probes/probeMapApi";

let fetchMock: ReturnType<typeof rs.fn>;
function reply(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body };
}
beforeEach(() => {
  fetchMock = rs.fn();
  rs.stubGlobal("fetch", fetchMock);
});

describe("getProbeModules", () => {
  it("GETs /api/probe_modules and unwraps the envelope", async () => {
    fetchMock.mockResolvedValue(
      reply(200, {
        data: { modules: { prototype: {} }, requires_install: { prototype: false } },
        result: "OK",
      }),
    );
    const catalog = await getProbeModules("");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/probe_modules");
    expect(catalog.requires_install.prototype).toBe(false);
  });

  it("throws on a non-ok response so the route's errorElement renders", async () => {
    fetchMock.mockResolvedValue(reply(500, {}));
    await expect(getProbeModules("")).rejects.toThrow("GET /api/probe_modules failed: HTTP 500");
  });
});

describe("applyProbeMap", () => {
  const MAP = { probe_devices: [], probe_info: [] };

  it("POSTs the map under a probe_map key", async () => {
    fetchMock.mockResolvedValue(reply(200, { result: "success" }));
    expect(await applyProbeMap("", MAP)).toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/probe_map");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ probe_map: MAP });
  });

  it("translates system_active into a sentence about the grill", async () => {
    fetchMock.mockResolvedValue(reply(409, { result: "error", message: "system_active" }));
    const r = await applyProbeMap("", MAP);
    expect(r).toEqual({
      ok: false,
      message: "Stop the grill before changing probe configuration.",
    });
  });

  it("names the offending modules on modules_require_install", async () => {
    fetchMock.mockResolvedValue(
      reply(422, {
        result: "error",
        message: "modules_require_install",
        modules: ["ds18b20", "bt_ibbq"],
      }),
    );
    const r = await applyProbeMap("", MAP);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.message).toContain("ds18b20");
      expect(r.message).toContain("bt_ibbq");
      expect(r.message).toContain("wizard");
    }
  });

  it("surfaces the bus-conflict detail verbatim", async () => {
    fetchMock.mockResolvedValue(
      reply(422, {
        result: "error",
        message: "bus_conflict",
        detail: "'basic' I2C can't share a process",
      }),
    );
    const r = await applyProbeMap("", MAP);
    expect(r).toEqual({ ok: false, message: "'basic' I2C can't share a process" });
  });

  it("falls back to the status code for an unrecognised rejection", async () => {
    fetchMock.mockResolvedValue(reply(418, { result: "error", message: "brewing_tea" }));
    const r = await applyProbeMap("", MAP);
    expect(r).toEqual({ ok: false, message: "Probe configuration was not saved (HTTP 418)." });
  });

  it("explains bad_probe_map without leaking the wire code", async () => {
    fetchMock.mockResolvedValue(reply(400, { result: "error", message: "bad_probe_map" }));
    const r = await applyProbeMap("", MAP);
    expect(r).toEqual({
      ok: false,
      message: "The probe configuration is malformed and was not saved.",
    });
  });

  it("does not throw on a network failure", async () => {
    fetchMock.mockRejectedValue(new Error("boom"));
    const r = await applyProbeMap("", MAP);
    expect(r.ok).toBe(false);
  });
});

describe("readLiveProbeMap / readLiveProfiles", () => {
  it("narrows the generated settings type and defaults both arrays", () => {
    expect(readLiveProbeMap({} as never)).toEqual({ probe_devices: [], probe_info: [] });
  });

  it("defaults each half independently when only one is present", () => {
    const device = {
      device: "A",
      module: "prototype",
      module_filename: "prototype",
      ports: [],
      config: {},
    };
    const settings = { probe_settings: { probe_map: { probe_devices: [device] } } };
    expect(readLiveProbeMap(settings as never)).toEqual({
      probe_devices: [device],
      probe_info: [],
    });
  });

  it("flattens probe_profiles from an id-keyed object to a list", () => {
    const settings = {
      probe_settings: {
        probe_profiles: { TWPS00: { id: "TWPS00", name: "TW", A: 1, B: 2, C: 3 } },
      },
    };
    expect(readLiveProfiles(settings as never)).toEqual([
      { id: "TWPS00", name: "TW", A: 1, B: 2, C: 3 },
    ]);
  });

  it("returns an empty profile list rather than undefined", () => {
    expect(readLiveProfiles({} as never)).toEqual([]);
  });
});
