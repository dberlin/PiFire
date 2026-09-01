import type { NotifyUpdate } from "@pifire/core/contracts/control";
import type { ProbeDataPayload } from "@pifire/core/contracts/core";
import { afterEach, describe, expect, it, rs } from "@rstest/core";

import {
  type LimitEdit,
  limitEditFields,
  type NotifyEdit,
  notifyEditUpdates,
  readLimitEdit,
  readNotifyEdit,
  readTargetEdit,
  saveNotifyEdit,
  type TargetEdit,
  targetEditFields,
  targetRange,
} from "../../../../src/helpers/notify/notifyState";

const ON: TargetEdit = { enabled: true, target: 203, action: "keepWarm" };
const LIMIT_OFF: LimitEdit = { enabled: false, target: 0, action: "none" };

const probe = (over: Partial<ProbeDataPayload>): ProbeDataPayload =>
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
  }) as ProbeDataPayload;

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

  // THE test that keeps high/low limit alerts purely additive, and that keeps a
  // concurrent writer's work alive: every field this object does NOT name keeps
  // whatever value the entry holds when the control loop drains the queue.
  // Naming a fifth field here would start writing state this modal does not
  // own.
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
  it("readTargetEdit lets shutdown win when the payload somehow carries both", () => {
    expect(readTargetEdit(probe({ targetShutdown: true, targetKeepWarm: true })).action).toBe(
      "shutdown",
    );
  });
});

describe("limitEditFields", () => {
  const ARMED: LimitEdit = { enabled: true, target: 300, action: "none" };

  // THE reason the limit editor cannot use the /api/set/limit_high|limit_low
  // REST grammar: that grammar takes req/shutdown/keep_warm/reignite/target
  // (common/api_commands.py:544-551) and cannot set `triggered` at all. An
  // entry saved with triggered:false while the temperature is ALREADY past the
  // limit fires on the very next control pass (notify/notifications.py:112) --
  // instantly, before anything has gone wrong. Pre-arming says "this condition
  // is already true, stay quiet until the temperature leaves and comes back".
  it("pre-arms a high limit whose temperature is already above it", () => {
    expect(limitEditFields("high", ARMED, 310).triggered).toBe(true);
    expect(limitEditFields("high", ARMED, 250).triggered).toBe(false);
  });

  it("pre-arms a low limit whose temperature is already below it", () => {
    expect(limitEditFields("low", ARMED, 250).triggered).toBe(true);
    expect(limitEditFields("low", ARMED, 310).triggered).toBe(false);
  });

  // Deliberate divergence from dash_default.js:724/766, which pre-arms on a
  // STRICT `current > target` / `current < target`. The backend's conditions
  // are "equal_above" and "equal_below" (common/defaults.py:542,548) --
  // `>=` and `<=` (notify/notifications.py:745-748) -- so at exactly the limit
  // Flask writes triggered:false for a condition that is already true, and the
  // alarm sounds immediately. Pre-arm on the same comparison the backend fires
  // on, or pre-arming does not cover its own boundary.
  it("pre-arms at exactly the limit, where the backend's condition already holds", () => {
    expect(limitEditFields("high", ARMED, 300).triggered).toBe(true);
    expect(limitEditFields("low", ARMED, 300).triggered).toBe(true);
  });

  it("pre-arms against the rounded target that is actually written", () => {
    expect(limitEditFields("high", { ...ARMED, target: 299.6 }, 300).triggered).toBe(true);
    expect(limitEditFields("high", { ...ARMED, target: 299.6 }, 300).target).toBe(300);
  });

  // Every input to the backend's action tail (`if shutdown ... elif keep_warm
  // ... elif reignite`, notify/notifications.py:157-174), plus the three fields
  // that define the alert. The modal owns this entry outright, so it states all
  // of them: a `keep_warm` or `reignite` left armed by the mobile DTO or by
  // /api/set/limit_* would otherwise act on an alert whose UI shows no action
  // at all. `condition` is a per-type constant, not user state -- it is named
  // because notify.set APPENDS an entry that does not exist yet
  // (common/control_delta.py:263-270), and check_notify reads item["condition"]
  // unguarded, so an appended entry without it would raise on the next pass.
  it("names the fields that fully determine the entry", () => {
    for (const fields of [
      limitEditFields("high", ARMED, 250),
      limitEditFields("low", LIMIT_OFF, 250),
    ]) {
      expect(Object.keys(fields).sort()).toEqual([
        "condition",
        "keep_warm",
        "reignite",
        "req",
        "shutdown",
        "target",
        "triggered",
      ]);
    }
  });

  it("carries the condition each entry type fires on", () => {
    expect(limitEditFields("high", ARMED, 250).condition).toBe("equal_above");
    expect(limitEditFields("low", ARMED, 250).condition).toBe("equal_below");
  });

  // Same one-choice model as TargetAction, for the same reason: the backend
  // runs shutdown BEFORE reignite, so an entry carrying both silently drops the
  // re-ignite. Flask models this as two checkboxes that uncheck each other in
  // JavaScript (_macro_dash_default.html:294-308) -- the weaker form of the
  // same idea, and the one whose companion bug (reignite read off the shutdown
  // box) hid inside it.
  it("maps action to exactly one of shutdown / reignite, never both", () => {
    expect(limitEditFields("low", { ...ARMED, action: "shutdown" }, 250)).toMatchObject({
      shutdown: true,
      reignite: false,
      keep_warm: false,
    });
    expect(limitEditFields("low", { ...ARMED, action: "reignite" }, 250)).toMatchObject({
      shutdown: false,
      reignite: true,
      keep_warm: false,
    });
    expect(limitEditFields("low", ARMED, 250)).toMatchObject({
      shutdown: false,
      reignite: false,
      keep_warm: false,
    });
  });

  it("disarms everything when the alert is switched off", () => {
    expect(limitEditFields("high", LIMIT_OFF, 999)).toEqual({
      req: false,
      target: 0,
      triggered: false,
      shutdown: false,
      keep_warm: false,
      reignite: false,
      condition: "equal_above",
    });
  });
});

describe("readLimitEdit", () => {
  it("reads the high limit off the socket payload", () => {
    expect(
      readLimitEdit(
        probe({ highLimitReq: true, highLimitTemp: 555.4, highLimitShutdown: true }),
        "high",
      ),
    ).toEqual({ enabled: true, target: 555, action: "shutdown" });
  });

  it("reads the low limit, including re-ignite", () => {
    expect(
      readLimitEdit(probe({ lowLimitReq: true, lowLimitTemp: 120, lowLimitReignite: true }), "low"),
    ).toEqual({ enabled: true, target: 120, action: "reignite" });
  });

  // Mirrors the backend's if/elif: with both flags set the grill shuts down and
  // re-ignite never runs, so "shutdown" is what the user is actually looking at.
  it("readLimitEdit lets shutdown win when the payload somehow carries both", () => {
    expect(
      readLimitEdit(probe({ lowLimitShutdown: true, lowLimitReignite: true }), "low").action,
    ).toBe("shutdown");
  });

  // blueprints/mobile/socket_io.py:781-787 publishes no reignite flag for the
  // high-limit entry at all, which is why the high limit offers no re-ignite
  // choice: there would be no way to show the state back.
  it("never reports re-ignite for a high limit", () => {
    expect(readLimitEdit(probe({ lowLimitReignite: true }), "high").action).toBe("none");
  });
});

describe("readNotifyEdit", () => {
  it("seeds all three sections from one payload", () => {
    expect(
      readNotifyEdit(
        probe({
          targetReq: true,
          target: 203,
          targetKeepWarm: true,
          highLimitReq: true,
          highLimitTemp: 550,
          lowLimitReq: true,
          lowLimitTemp: 150,
          lowLimitReignite: true,
        }),
      ),
    ).toEqual({
      target: { enabled: true, target: 203, action: "keepWarm" },
      high: { enabled: true, target: 550, action: "none" },
      low: { enabled: true, target: 150, action: "reignite" },
    });
  });
});

describe("notifyEditUpdates", () => {
  const EDIT: NotifyEdit = {
    target: ON,
    high: { enabled: true, target: 550, action: "shutdown" },
    low: { enabled: false, target: 0, action: "none" },
  };

  // One POST, three addressed entries -- not three POSTs and not a whole-array
  // replace. Every entry NOT named here (the other probes, the timer, the
  // hopper) survives whatever another writer did in the same control cycle.
  it("addresses exactly this probe's three entries, by type", () => {
    expect(notifyEditUpdates("Probe1", EDIT, 250).map((u) => [u.label, u.type])).toEqual([
      ["Probe1", "probe"],
      ["Probe1", "probe_limit_high"],
      ["Probe1", "probe_limit_low"],
    ]);
  });

  it("keeps the target entry's field set unchanged -- it owns four fields, not seven", () => {
    expect(notifyEditUpdates("Probe1", EDIT, 250)[0].fields).toEqual(targetEditFields(ON));
  });

  it("pre-arms each limit against the temperature passed in", () => {
    expect(notifyEditUpdates("Probe1", EDIT, 560)[1].fields.triggered).toBe(true);
    expect(notifyEditUpdates("Probe1", EDIT, 250)[1].fields.triggered).toBe(false);
  });
});

describe("saveNotifyEdit", () => {
  const EDIT: NotifyEdit = { target: ON, high: LIMIT_OFF, low: LIMIT_OFF };

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

  // ONE round trip and NO read, for all three entries. Reading the whole array
  // first and posting it back would revert every entry the modal did not mean
  // to touch -- most visibly a timer armed from the shell while it was open --
  // because the server can only apply a whole array as a replace. A per-field
  // REST grammar would instead be a dozen POSTs for one user gesture, with a
  // window in which a limit is set but its action is not -- and could not write
  // `triggered` at all.
  it("posts one addressed update per entry, in a single request, with no read first", async () => {
    const fetchMock = stubOk();
    await saveNotifyEdit("", "Probe1", EDIT, 120);
    expect(fetchMock.mock.calls).toHaveLength(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/control");
    expect(postedUpdates(fetchMock)).toHaveLength(3);
  });

  it("addresses each of the label's three entries by type", async () => {
    const fetchMock = stubOk();
    await saveNotifyEdit("", "Probe1", EDIT, 120);
    // Up to three entries share one label (common/defaults.py:512-538), so the
    // type is what tells them apart -- and every OTHER label's entries, plus
    // the timer and the hopper, are not named here at all.
    expect(postedUpdates(fetchMock)[0]).toEqual({
      label: "Probe1",
      type: "probe",
      fields: { req: true, target: 203, shutdown: false, keep_warm: true },
    });
    expect(postedUpdates(fetchMock).map((u) => u.type)).toEqual([
      "probe",
      "probe_limit_high",
      "probe_limit_low",
    ]);
  });

  it("propagates a rejected write rather than reporting success", async () => {
    const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({}),
    }));
    rs.stubGlobal("fetch", fetchMock);
    await expect(saveNotifyEdit("", "Probe1", EDIT, 120)).rejects.toThrow(/HTTP 503/);
  });
});
