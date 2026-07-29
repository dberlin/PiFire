import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { discoverWled, pushWledProfiles, testWledProfile } from "./wledApi";

describe("wledApi", () => {
  let fetchMock: ReturnType<typeof rs.fn>;
  const jsonRes = (body: unknown, ok = true, status = 200) => ({
    ok,
    status,
    json: async () => body,
  });

  beforeEach(() => {
    fetchMock = rs.fn();
    rs.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it("discoverWled GETs /api/wled_discover with the timeout and returns devices", async () => {
    fetchMock.mockResolvedValue(
      jsonRes({
        result: "success",
        message: "Found 1",
        devices: [{ ip: "10.0.0.5", led_count: 30, name: "WLED-A" }],
      }),
    );
    const res = await discoverWled(15);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/wled_discover?timeout=15");
    expect(res.result).toBe("success");
    expect(res.devices).toEqual([{ ip: "10.0.0.5", led_count: 30, name: "WLED-A" }]);
  });

  it("pushWledProfiles POSTs device_address + profile_numbers and returns profiles_pushed", async () => {
    fetchMock.mockResolvedValue(jsonRes({ result: "success", message: "ok", profiles_pushed: 12 }));
    const res = await pushWledProfiles("wled.local", { idle: 200, cooking: 203 });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/wled_push_profiles");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({
      device_address: "wled.local",
      profile_numbers: { idle: 200, cooking: 203 },
    });
    expect(res.profiles_pushed).toBe(12);
  });

  it("testWledProfile POSTs device_address + profile_number", async () => {
    fetchMock.mockResolvedValue(jsonRes({ result: "success", message: "ok" }));
    await testWledProfile("wled.local", 203);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/wled_test_profile");
    expect(JSON.parse(opts.body)).toEqual({ device_address: "wled.local", profile_number: 203 });
  });

  it("returns the error envelope without throwing on result:error", async () => {
    fetchMock.mockResolvedValue(
      jsonRes({ result: "error", message: "device unreachable" }, false, 500),
    );
    const res = await pushWledProfiles("bad", { idle: 200 });
    expect(res.result).toBe("error");
    expect(res.message).toBe("device unreachable");
  });

  it("synthesizes an error result when fetch rejects", async () => {
    fetchMock.mockRejectedValue(new Error("network down"));
    const res = await discoverWled();
    expect(res.result).toBe("error");
    expect(res.devices).toEqual([]);
  });
});
