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

  // The /api/set/... grammar answers "OK". Only POST /api/control answers
  // "success", and loosening this predicate to accept both would hide a real
  // surprise from every command that goes through the grammar.
  it('does NOT accept lowercase "success" from the /api/set grammar', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ result: "success", message: "", data: {} }),
    });
    const r = await createCommand("").setMode("stop");
    expect(r.ok).toBe(false);
  });
});

// blueprints/api/routes.py _api_post_control answers {"result": "success"} --
// lowercase, and a 201 -- where the command grammar answers {"result": "OK"}.
// A refactor that routed this write through the grammar's response check would
// report every successful timer start as a failure, silently.
describe("timerStartWithOptions envelope handling", () => {
  const CONTROL = {
    notify_data: [{ label: "Timer", type: "timer", req: false, shutdown: false, keep_warm: false }],
    timer: { start: 0, paused: 0, end: 0, shutdown: false },
  };

  function stubFetch(postBody: unknown, postOk = true) {
    const fetchMock = rs.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return { ok: postOk, status: 500, headers: new Headers(), json: async () => postBody };
      }
      return { ok: true, headers: new Headers(), json: async () => ({ control: CONTROL }) };
    });
    rs.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  const opts = { shutdown: true, keepWarm: false };

  it('treats lowercase "success" as a successful write', async () => {
    stubFetch({ control: "success", result: "success", message: "Settings updated successfully." });
    const r = await createCommand("").timerStartWithOptions(600, opts);
    expect(r).toEqual({ ok: true, message: "Settings updated successfully." });
  });

  it('treats "error" as a failed write', async () => {
    stubFetch({ control: "error", result: "error", message: "Settings update failed." });
    const r = await createCommand("").timerStartWithOptions(600, opts);
    expect(r).toEqual({ ok: false, message: "Settings update failed." });
  });

  it("reports an HTTP failure on the write", async () => {
    stubFetch({}, false);
    const r = await createCommand("").timerStartWithOptions(600, opts);
    expect(r).toEqual({ ok: false, message: "HTTP 500" });
  });

  it("reads control before writing, and writes exactly once", async () => {
    const fetchMock = stubFetch({ result: "success", message: "" });
    await createCommand("http://pi:5000").timerStartWithOptions(600, opts);
    const calls = fetchMock.mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0][0]).toBe("http://pi:5000/api/control");
    expect(calls[0][1]?.method).toBe("GET");
    expect(calls[1][0]).toBe("http://pi:5000/api/control");
    expect(calls[1][1]?.method).toBe("POST");
  });

  it("fails without writing when control has no timer notify entry", async () => {
    const fetchMock = rs.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") throw new Error("must not write");
      return {
        ok: true,
        headers: new Headers(),
        json: async () => ({ control: { notify_data: [{ label: "Grill", type: "probe" }] } }),
      };
    });
    rs.stubGlobal("fetch", fetchMock);
    const r = await createCommand("").timerStartWithOptions(600, opts);
    expect(r.ok).toBe(false);
    expect(r.message).toMatch(/no timer entry/);
    expect(fetchMock.mock.calls).toHaveLength(1);
  });

  it("fails without writing when the read fails", async () => {
    const fetchMock = rs.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") throw new Error("must not write");
      return { ok: false, status: 404, headers: new Headers(), json: async () => ({}) };
    });
    rs.stubGlobal("fetch", fetchMock);
    const r = await createCommand("").timerStartWithOptions(600, opts);
    expect(r.ok).toBe(false);
    expect(r.message).toMatch(/HTTP 404/);
    expect(fetchMock.mock.calls).toHaveLength(1);
  });

  it("falls back to the browser clock when the Date header is unreadable", async () => {
    const fetchMock = stubFetch({ result: "success", message: "" });
    const before = Math.floor(Date.now() / 1000);
    await createCommand("").timerStartWithOptions(600, opts);
    const body = JSON.parse(String(fetchMock.mock.calls[1][1]?.body)) as {
      timer: { start: number; end: number };
    };
    expect(body.timer.start).toBeGreaterThanOrEqual(before);
    expect(body.timer.end - body.timer.start).toBe(600);
  });
});
