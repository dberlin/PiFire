import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { buildCommandUrl, createCommand } from "../../../src/helpers/command";
import type { CommandResponse } from "../../../src/helpers/contracts/core.gen";
import type { TimerOptionsPayload } from "../../../src/helpers/contracts/control.gen";

const OK_COMMAND_RESPONSE = {
  result: "OK",
  message: "",
  data: {},
} satisfies CommandResponse;

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
      json: async () => OK_COMMAND_RESPONSE,
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
  it("setPMode → the bare number", async () => {
    // common/api_commands.py::_cmd_set_pmode reads arglist[1] and gates on
    // .isdigit() before the 0-9 range check, so anything but a bare integer
    // segment is refused server-side with no client-visible error.
    await createCommand("").setPMode(7);
    expect(url()).toBe("/api/set/pmode/7");
  });
  it("setPMode → 0 is a real value, not an omitted one", async () => {
    await createCommand("").setPMode(0);
    expect(url()).toBe("/api/set/pmode/0");
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
  it("prime → grams and each backend-recognized next mode", async () => {
    const command = createCommand("");
    await command.prime(20, "startup");
    await command.prime(20, "monitor");
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/set/mode/prime/20/startup",
      "/api/set/mode/prime/20/monitor",
    ]);
  });
  it("prime omits next mode to select the backend default Stop", async () => {
    await createCommand("").prime(20);
    expect(url()).toBe("/api/set/mode/prime/20");
  });
  it("prime rejects modes the backend silently maps to Stop", () => {
    if (false) {
      // @ts-expect-error smoke is not a recognized post-prime mode.
      createCommand("").prime(20, "smoke");
      // @ts-expect-error manual is not a recognized post-prime mode.
      createCommand("").prime(20, "manual");
    }
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

// timerStartWithOptions is a single command in the /api/set grammar:
//
//   /api/set/timer/start/{seconds}/{expiry options}
//
// The DURATION is what travels; the server (common/api_commands.py
// _cmd_set_timer) computes control.timer.end from its OWN time.time(). The
// control process judges expiry against that same clock, so a client clock
// running behind the Pi's must not be able to arm an already-expired timer --
// and an expired timer with "Shutdown Grill" ticked shuts the grill down
// mid-cook. No timestamp is ever sent, so there is nothing to skew.
describe("timerStartWithOptions", () => {
  let fetchMock: ReturnType<typeof rs.fn>;
  beforeEach(() => {
    fetchMock = rs.fn(async () => ({
      ok: true,
      json: async () => ({ result: "OK", message: "Command was accepted successfully." }),
    }));
    rs.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  const opts = { shutdown: true, keepWarm: false } satisfies TimerOptionsPayload;

  it("issues exactly ONE request, carrying the duration and both flags", async () => {
    const r = await createCommand("http://pi:5000").timerStartWithOptions(600, {
      shutdown: true,
      keepWarm: true,
    });
    // One request: the server computes the end from its own clock and validates
    // the duration, neither of which a client-side read-modify-write does.
    expect(fetchMock.mock.calls).toHaveLength(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://pi:5000/api/set/timer/start/600/shutdown,keep_warm",
    );
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    // A duration is the whole payload -- no client-computed end time.
    expect(fetchMock.mock.calls[0][1]?.body).toBeUndefined();
    expect(r).toEqual({ ok: true, message: "Command was accepted successfully.", data: undefined });
  });

  it("encodes every flag combination as a named option segment", async () => {
    const c = createCommand("");
    await c.timerStartWithOptions(600, { shutdown: false, keepWarm: false });
    await c.timerStartWithOptions(600, { shutdown: true, keepWarm: false });
    await c.timerStartWithOptions(600, { shutdown: false, keepWarm: true });
    await c.timerStartWithOptions(600, { shutdown: true, keepWarm: true });
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/set/timer/start/600/none",
      "/api/set/timer/start/600/shutdown",
      "/api/set/timer/start/600/keep_warm",
      "/api/set/timer/start/600/shutdown,keep_warm",
    ]);
  });

  it("sends a whole number of seconds", async () => {
    await createCommand("").timerStartWithOptions(600.6, opts);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/set/timer/start/601/shutdown");
  });

  it("maps the grammar's ERROR envelope to ok:false", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        result: "ERROR",
        message: "Timer is paused. Resume or stop it before starting a new timer.",
      }),
    });
    const r = await createCommand("").timerStartWithOptions(600, opts);
    expect(r.ok).toBe(false);
    expect(r.message).toMatch(/paused/);
  });

  it("reports an HTTP failure", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) });
    const r = await createCommand("").timerStartWithOptions(600, opts);
    expect(r).toEqual({ ok: false, message: "HTTP 500" });
  });

  // The modal closes on submit either way, so a start that never reached the Pi
  // must at least come back as a failure rather than as a resolved promise.
  it("reports a network error instead of throwing", async () => {
    fetchMock.mockImplementationOnce(async () => {
      throw new Error("Failed to fetch");
    });
    const r = await createCommand("").timerStartWithOptions(600, opts);
    expect(r).toEqual({ ok: false, message: "Failed to fetch" });
  });

  // POST /api/control answers {"result": "success"}; this command goes through
  // the /api/set grammar, which answers {"result": "OK"}. Accepting "success"
  // here would mean the write silently went somewhere else.
  it('does NOT accept lowercase "success"', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ result: "success", message: "" }),
    });
    const r = await createCommand("").timerStartWithOptions(600, opts);
    expect(r.ok).toBe(false);
  });
});

// recipeNextStep has no /api/set/... grammar behind it: Flask sends a bare
// control patch (control_panel.js:530). That path answers lowercase "success",
// not the "OK" that post() tests for, so it goes through notifyApi's postControl
// rather than through post() -- routing it through post() would report every
// successful advance as a failure.
describe("createCommand.recipeNextStep", () => {
  let fetchMock: ReturnType<typeof rs.fn>;
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it('POSTs /api/control with exactly {"updated": true}', async () => {
    fetchMock = rs.fn(async () => ({
      ok: true,
      status: 201,
      json: async () => ({ result: "success" }),
    }));
    rs.stubGlobal("fetch", fetchMock);

    await expect(createCommand("").recipeNextStep()).resolves.toEqual({ ok: true, message: "" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/control");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ updated: true });
  });

  it("treats lowercase success as ok and anything else as a failure", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({
        ok: true,
        status: 201,
        json: async () => ({ result: "error", message: "nope" }),
      })),
    );
    const res = await createCommand("").recipeNextStep();
    expect(res.ok).toBe(false);
    expect(res.message).toBe("nope");
  });

  it("reports a network failure rather than throwing", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => {
        throw new Error("network down");
      }),
    );
    const res = await createCommand("").recipeNextStep();
    expect(res).toEqual({ ok: false, message: "network down" });
  });
});

// recipeUnpause resumes a recipe stopped at a step whose `pause` flag is set.
// Flask's single "Next Step" button branches: paused -> cpRecipeUnpause (posts
// the step_data with pause:false, control_panel.js:382-392); otherwise the bare
// {updated:true} advance. Unlike Flask, which reposts the WHOLE step_data (and
// so can revert a `triggered`/`notify` the control loop set in the same cycle),
// this sends only the scalar leaf -- RFC-7396 merges it in place. The controller
// advances the step once `pause` is false (base.py::_handle_recipe_end).
describe("createCommand.recipeUnpause", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it('POSTs /api/control with exactly {"recipe": {"step_data": {"pause": false}}}', async () => {
    const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async () => ({
      ok: true,
      status: 201,
      json: async () => ({ result: "success" }),
    }));
    rs.stubGlobal("fetch", fetchMock);

    await expect(createCommand("").recipeUnpause()).resolves.toEqual({ ok: true, message: "" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/control");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ recipe: { step_data: { pause: false } } });
  });
});

// createCommand no longer exposes hopperCheck. Its only caller was the hopper
// card's Refresh Status button, removed once the control loop started
// refreshing the level on a timer (distance/intervals.py) -- an exported
// command with no caller is just an untested API surface. The backend
// hopper_check flag is untouched: the attached display (_base_flex.py:1462) and
// the Flask pellet pages still raise it, and the control loop still services it.
describe("createCommand has no hopperCheck", () => {
  it("does not expose one", () => {
    expect("hopperCheck" in createCommand("")).toBe(false);
  });
});
