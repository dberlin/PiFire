import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { buildCommandUrl, createCommand } from "./command";

describe("buildCommandUrl", () => {
  it("joins base + /api + segments", () => {
    expect(buildCommandUrl("", ["set", "psp", 225])).toBe("/api/set/psp/225");
    expect(buildCommandUrl("http://pi:5000", ["set", "mode", "smoke"])).toBe("http://pi:5000/api/set/mode/smoke");
  });
});

describe("createCommand issues the right URLs", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ result: "OK", message: "", data: {} }) }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const url = () => fetchMock.mock.calls[0][0];
  const opts = () => fetchMock.mock.calls[0][1];

  it("setMode → lowercase mode", async () => {
    await createCommand("").setMode("smoke");
    expect(url()).toBe("/api/set/mode/smoke");
    expect(opts().method).toBe("POST");
  });
  it("hold → psp with integer temp", async () => {
    await createCommand("").hold(225);
    expect(url()).toBe("/api/set/psp/225");
  });
  it("setSmokePlus → true/false", async () => {
    await createCommand("").setSmokePlus(true);
    expect(url()).toBe("/api/set/splus/true");
  });
  it("timerStart → seconds", async () => {
    await createCommand("").timerStart(600);
    expect(url()).toBe("/api/set/timer/start/600");
  });
  it("prime → grams and next mode", async () => {
    await createCommand("").prime(20, "smoke");
    expect(url()).toBe("/api/set/mode/prime/20/smoke");
  });
  it("system → cmd grammar", async () => {
    await createCommand("").system("reboot");
    expect(url()).toBe("/api/cmd/reboot");
  });
  it("maps a non-OK envelope to ok:false", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ result: "ERROR", message: "bad", data: {} }) });
    const r = await createCommand("").setMode("stop");
    expect(r).toEqual({ ok: false, message: "bad", data: {} });
  });
});
