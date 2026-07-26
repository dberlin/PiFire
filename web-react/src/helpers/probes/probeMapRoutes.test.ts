import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { BASE_URL, probeModulesLoader } from "./probeMapRoutes";

let fetchMock: ReturnType<typeof rs.fn>;
function reply(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body };
}
beforeEach(() => {
  fetchMock = rs.fn();
  rs.stubGlobal("fetch", fetchMock);
});

describe("probeModulesLoader", () => {
  it("resolves the catalog for the /settings/probes route", async () => {
    fetchMock.mockResolvedValue(
      reply(200, {
        data: { modules: { virtual_average: {} }, requires_install: { virtual_average: false } },
        result: "OK",
      }),
    );
    const catalog = await probeModulesLoader();
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE_URL}/api/probe_modules`);
    expect(catalog.requires_install.virtual_average).toBe(false);
  });

  it("rejects when the endpoint fails, so SettingsError renders", async () => {
    fetchMock.mockResolvedValue(reply(500, {}));
    await expect(probeModulesLoader()).rejects.toThrow("GET /api/probe_modules failed: HTTP 500");
  });

  it("uses a same-origin base so Flask never has to send CORS headers", () => {
    // PUBLIC_PIFIRE_URL is unset in tests and in the shipped bundle; the shell
    // context's absolute targetUrl must NOT be used as a fetch base -- the
    // notify slice already shipped that bug once.
    expect(BASE_URL).toBe("");
  });
});
