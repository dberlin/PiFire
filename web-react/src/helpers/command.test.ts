import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { buildCommandUrl, createCommand } from "./command";

describe("buildCommandUrl", () => {
  it("joins base + /api + segments", () => {
    expect(buildCommandUrl("", ["set", "psp", 225])).toBe("/api/set/psp/225");
    expect(buildCommandUrl("http://pi:5000", ["set", "mode", "smoke"])).toBe(
      "http://pi:5000/api/set/mode/smoke",
    );
  });
});

describe("createCommand issues the right URLs", () => {
  let fetchMock: ReturnType<typeof rs.fn>;
  beforeEach(() => {
    fetchMock = rs.fn(async () => ({
      ok: true,
      json: async () => ({ result: "OK", message: "", data: {} }),
    }));
    rs.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    rs.unstubAllGlobals();
  });

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
  it("timerShutdown → literal true/false segment", async () => {
    const c = createCommand("");
    await c.timerShutdown(true);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/set/timer/shutdown/true");
    await c.timerShutdown(false);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/set/timer/shutdown/false");
  });
  it("timerKeepWarm → literal true/false segment", async () => {
    const c = createCommand("");
    await c.timerKeepWarm(false);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/set/timer/keep_warm/false");
    await c.timerKeepWarm(true);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/set/timer/keep_warm/true");
  });
  it("timerPause and timerStop take no argument segment", async () => {
    const c = createCommand("");
    await c.timerPause();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/set/timer/pause");
    await c.timerStop();
    expect(fetchMock.mock.calls[1][0]).toBe("/api/set/timer/stop");
  });
  it("prime → grams and next mode", async () => {
    await createCommand("").prime(20, "smoke");
    expect(url()).toBe("/api/set/mode/prime/20/smoke");
  });
  it("system → cmd grammar", async () => {
    await createCommand("").system("reboot");
    expect(url()).toBe("/api/cmd/reboot");
  });
  it("setUnits → /api/set/units/{F|C}", async () => {
    await createCommand("").setUnits("C");
    expect(url()).toBe("/api/set/units/C");
  });
  it("manualOutput → toggle command for an output", async () => {
    await createCommand("").manualOutput("auger");
    expect(url()).toBe("/api/set/manual/auger/toggle");
  });
  it("manualOutput → accepts an explicit true/false action", async () => {
    await createCommand("").manualOutput("power", "false");
    expect(url()).toBe("/api/set/manual/power/false");
  });
  it("manualPwm → rounds and clamps the duty cycle", async () => {
    const c = createCommand("");
    await c.manualPwm(42.6);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/set/manual/pwm/43");
    await c.manualPwm(150);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/set/manual/pwm/100");
    await c.manualPwm(-5);
    expect(fetchMock.mock.calls[2][0]).toBe("/api/set/manual/pwm/0");
  });
  it("setMode → supports manual", async () => {
    await createCommand("").setMode("manual");
    expect(url()).toBe("/api/set/mode/manual");
  });
  it("maps a non-OK envelope to ok:false", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ result: "ERROR", message: "bad", data: {} }),
    });
    const r = await createCommand("").setMode("stop");
    expect(r).toEqual({ ok: false, message: "bad", data: {} });
  });
});
