import { afterEach, describe, expect, it, rs } from "@rstest/core";
import type { ProbeData } from "../types";
import type { NotifyUpdate } from "./notifyApi";
import {
  readTargetEdit,
  saveTargetEdit,
  type TargetEdit,
  targetEditFields,
  targetRange,
} from "./notifyState";

const ON: TargetEdit = { enabled: true, target: 203, action: "keepWarm" };

describe("targetRange", () => {
  // Hard-coded in the Flask template
  // (blueprints/dash/templates/default/_macro_dash_default.html:174-186).
  // These are deliberately NOT probe.maxTemp from the dash payload -- that is
  // the gauge ceiling out of settings.dashboard.dashboards.Default.config, a
  // different number that happens to be 600 for the grill on many rigs.
  it("gives the primary probe 0-600 F / 0-300 C", () => {
    expect(targetRange(true, "F")).toEqual({ min: 0, max: 600 });
    expect(targetRange(true, "C")).toEqual({ min: 0, max: 300 });
  });
  it("gives a food probe 0-300 F / 0-225 C", () => {
    expect(targetRange(false, "F")).toEqual({ min: 0, max: 300 });
    expect(targetRange(false, "C")).toEqual({ min: 0, max: 225 });
  });
});

describe("targetEditFields", () => {
  it("arms the target", () => {
    expect(targetEditFields(ON)).toEqual({
      req: true,
      target: 203,
      shutdown: false,
      keep_warm: true,
    });
  });

  // THE test that makes Slice 2 (high/low limit alerts) purely additive, and
  // that keeps a concurrent writer's work alive: every field this object does
  // NOT name keeps whatever value the entry holds when the control loop drains
  // the queue. Naming a fifth field here would start writing state this modal
  // does not own.
  it("names ONLY the four fields the target modal owns", () => {
    expect(Object.keys(targetEditFields(ON)).sort()).toEqual([
      "keep_warm",
      "req",
      "shutdown",
      "target",
    ]);
    expect(
      Object.keys(targetEditFields({ enabled: false, target: 0, action: "none" })).sort(),
    ).toEqual(["keep_warm", "req", "shutdown", "target"]);
  });

  it("clears the target when disabled", () => {
    expect(targetEditFields({ enabled: false, target: 203, action: "none" })).toEqual({
      req: false,
      target: 0,
      shutdown: false,
      keep_warm: false,
    });
  });

  // The backend runs `if shutdown: ... elif keep_warm: ...`
  // (notify/notifications.py:142-159), so "both ticked" means shutdown and
  // silently drops keep-warm. TargetEdit carries one `action` so the UI cannot
  // express a state the backend will not honour.
  it("maps action to exactly one of shutdown / keep_warm", () => {
    expect(targetEditFields({ ...ON, action: "shutdown" })).toMatchObject({
      shutdown: true,
      keep_warm: false,
    });
    expect(targetEditFields({ ...ON, action: "keepWarm" })).toMatchObject({
      shutdown: false,
      keep_warm: true,
    });
    expect(targetEditFields({ ...ON, action: "none" })).toMatchObject({
      shutdown: false,
      keep_warm: false,
    });
  });

  it("rounds a fractional target to an integer", () => {
    expect(targetEditFields({ ...ON, target: 202.6 }).target).toBe(203);
  });
});

describe("readTargetEdit", () => {
  const probe = (over: Partial<ProbeData>): ProbeData =>
    ({
      title: "Probe-1",
      label: "Probe1",
      eta: null,
      temp: 120,
      setTemp: 0,
      maxTemp: 300,
      target: 0,
      lowLimitTemp: 0,
      highLimitTemp: 0,
      targetReq: false,
      hasNotifications: false,
      lowLimitReq: false,
      highLimitReq: false,
      highLimitShutdown: false,
      highLimitTriggered: false,
      lowLimitShutdown: false,
      lowLimitReignite: false,
      lowLimitTriggered: false,
      targetShutdown: false,
      targetKeepWarm: false,
      status: {},
      ...over,
    }) as ProbeData;

  it("maps targetReq/target onto the edit", () => {
    expect(readTargetEdit(probe({ targetReq: true, target: 203 }))).toEqual({
      enabled: true,
      target: 203,
      action: "none",
    });
  });

  it("rounds the incoming target", () => {
    expect(readTargetEdit(probe({ target: 202.4 })).target).toBe(202);
  });

  it("reads keepWarm and shutdown", () => {
    expect(readTargetEdit(probe({ targetKeepWarm: true })).action).toBe("keepWarm");
    expect(readTargetEdit(probe({ targetShutdown: true })).action).toBe("shutdown");
  });

  // Mirrors the backend's if/elif at notify/notifications.py:142-159: with both
  // flags set the grill shuts down and keep-warm never runs, so "shutdown" is
  // what the user is actually looking at.
  it("lets shutdown win when the payload somehow carries both", () => {
    expect(readTargetEdit(probe({ targetShutdown: true, targetKeepWarm: true })).action).toBe(
      "shutdown",
    );
  });
});

describe("saveTargetEdit", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  const stubOk = () => {
    const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async () => ({
      ok: true,
      status: 201,
      json: async () => ({ result: "success" }),
    }));
    rs.stubGlobal("fetch", fetchMock);
    return fetchMock;
  };
  const postedUpdates = (fetchMock: ReturnType<typeof rs.fn>) =>
    (
      JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body)) as {
        notify_updates: NotifyUpdate[];
      }
    ).notify_updates;

  // ONE round trip and NO read. Reading the whole array first and posting it
  // back would revert every entry the modal did not mean to touch -- most
  // visibly a timer armed from the shell while it was open -- because the
  // server can only apply a whole array as a replace. A per-field REST grammar
  // would instead be four POSTs for one user gesture, with a window in which
  // the target is set but its action is not.
  it("posts one addressed update, with no read first", async () => {
    const fetchMock = stubOk();
    await saveTargetEdit("", "Probe1", ON);
    expect(fetchMock.mock.calls).toHaveLength(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/control");
  });

  it("addresses the type:'probe' entry for that label and nothing else", async () => {
    const fetchMock = stubOk();
    await saveTargetEdit("", "Probe1", ON);
    // Up to three entries share one label (common/defaults.py:512-538), so the
    // type is what keeps a target edit off the two limit alerts.
    expect(postedUpdates(fetchMock)).toEqual([
      {
        label: "Probe1",
        type: "probe",
        fields: { req: true, target: 203, shutdown: false, keep_warm: true },
      },
    ]);
  });

  it("propagates a rejected write rather than reporting success", async () => {
    const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({}),
    }));
    rs.stubGlobal("fetch", fetchMock);
    await expect(saveTargetEdit("", "Probe1", ON)).rejects.toThrow(/HTTP 503/);
  });
});
