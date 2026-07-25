import { afterEach, describe, expect, it, rs } from "@rstest/core";
import type { ProbeData } from "../types";
import type { NotifyEntry } from "./notifyApi";
import {
  applyTargetEdit,
  readTargetEdit,
  saveTargetEdit,
  type TargetEdit,
  targetRange,
} from "./notifyState";

// A realistic slice of control["notify_data"] -- three entries per probe sharing
// one label (common/defaults.py:512-538), plus the singleton entries. Captured
// from a live GET /api/get/notify.
function fixture(): NotifyEntry[] {
  const probeTrio = (label: string): NotifyEntry[] => [
    {
      condition: "equal_above",
      eta: null,
      keep_warm: false,
      label,
      name: `${label}-name`,
      reignite: false,
      req: false,
      shutdown: false,
      target: 0,
      type: "probe",
    },
    {
      condition: "equal_above",
      eta: null,
      keep_warm: false,
      label,
      name: `${label}-name`,
      reignite: false,
      req: true,
      shutdown: true,
      target: 350,
      triggered: false,
      type: "probe_limit_high",
    },
    {
      condition: "equal_below",
      eta: null,
      keep_warm: false,
      label,
      name: `${label}-name`,
      reignite: true,
      req: true,
      shutdown: false,
      target: 100,
      triggered: false,
      type: "probe_limit_low",
    },
  ];
  return [
    ...probeTrio("Grill"),
    ...probeTrio("Probe1"),
    { label: "Timer", type: "timer", req: false, shutdown: false, keep_warm: false },
    {
      label: "Hopper",
      type: "hopper",
      req: true,
      shutdown: false,
      keep_warm: false,
      last_check: 0,
    },
    { label: "Test", type: "test", req: false, shutdown: false, keep_warm: false },
  ];
}

const ON: TargetEdit = { enabled: true, target: 203, action: "keepWarm" };
const entryFor = (entries: NotifyEntry[], label: string, type: string) =>
  entries.find((e) => e.label === label && e.type === type);

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

describe("applyTargetEdit", () => {
  it("arms the type:'probe' entry for that label", () => {
    const out = applyTargetEdit(fixture(), "Probe1", ON);
    expect(entryFor(out, "Probe1", "probe")).toMatchObject({
      req: true,
      target: 203,
      shutdown: false,
      keep_warm: true,
    });
  });

  // THE test that makes Slice 2 (high/low limit alerts) purely additive: a
  // target edit must never touch the two limit entries that share the label.
  // Up to three entries carry the same label, so any "find the entry for this
  // probe" that filters on label alone silently edits the limits too.
  it("leaves the probe_limit_high/low entries for the SAME label deep-equal", () => {
    const before = fixture();
    const out = applyTargetEdit(before, "Probe1", ON);
    expect(entryFor(out, "Probe1", "probe_limit_high")).toEqual(
      entryFor(before, "Probe1", "probe_limit_high"),
    );
    expect(entryFor(out, "Probe1", "probe_limit_low")).toEqual(
      entryFor(before, "Probe1", "probe_limit_low"),
    );
  });

  it("leaves other labels and the timer/hopper/test entries deep-equal", () => {
    const before = fixture();
    const out = applyTargetEdit(before, "Probe1", ON);
    for (const type of ["probe", "probe_limit_high", "probe_limit_low"]) {
      expect(entryFor(out, "Grill", type)).toEqual(entryFor(before, "Grill", type));
    }
    for (const label of ["Timer", "Hopper", "Test"]) {
      expect(out.find((e) => e.label === label)).toEqual(before.find((e) => e.label === label));
    }
  });

  it("does not mutate the input array and returns a new one", () => {
    const before = fixture();
    const clone = structuredClone(before);
    const out = applyTargetEdit(before, "Probe1", ON);
    expect(before).toEqual(clone);
    expect(out).not.toBe(before);
    expect(out).toHaveLength(before.length);
  });

  it("preserves unknown keys on the edited entry", () => {
    const entries: NotifyEntry[] = [
      { label: "P", type: "probe", req: false, shutdown: false, condition: "equal_above", foo: 1 },
    ];
    const out = applyTargetEdit(entries, "P", ON);
    expect(out[0].foo).toBe(1);
    expect(out[0].condition).toBe("equal_above");
  });

  it("clears the target entry when disabled, still without touching the limits", () => {
    const before = fixture();
    const out = applyTargetEdit(before, "Probe1", { enabled: false, target: 203, action: "none" });
    expect(entryFor(out, "Probe1", "probe")).toMatchObject({
      req: false,
      target: 0,
      shutdown: false,
      keep_warm: false,
    });
    // Deliberately divergent from Flask's cancelNotify (dash_default.js:807-813),
    // which clears every entry matching the label -- wiping the limit alerts as
    // a side effect of turning a target off.
    expect(entryFor(out, "Probe1", "probe_limit_high")).toEqual(
      entryFor(before, "Probe1", "probe_limit_high"),
    );
    expect(entryFor(out, "Probe1", "probe_limit_low")).toEqual(
      entryFor(before, "Probe1", "probe_limit_low"),
    );
  });

  // The backend runs `if shutdown: ... elif keep_warm: ...`
  // (notify/notifications.py:142-159), so "both ticked" means shutdown and
  // silently drops keep-warm. TargetEdit carries one `action` so the UI cannot
  // express a state the backend will not honour.
  it("maps action to exactly one of shutdown / keep_warm", () => {
    const shut = applyTargetEdit(fixture(), "Probe1", { ...ON, action: "shutdown" });
    expect(entryFor(shut, "Probe1", "probe")).toMatchObject({ shutdown: true, keep_warm: false });
    const warm = applyTargetEdit(fixture(), "Probe1", { ...ON, action: "keepWarm" });
    expect(entryFor(warm, "Probe1", "probe")).toMatchObject({ shutdown: false, keep_warm: true });
    const none = applyTargetEdit(fixture(), "Probe1", { ...ON, action: "none" });
    expect(entryFor(none, "Probe1", "probe")).toMatchObject({ shutdown: false, keep_warm: false });
  });

  it("rounds a fractional target to an integer", () => {
    const out = applyTargetEdit(fixture(), "Probe1", { ...ON, target: 202.6 });
    expect(entryFor(out, "Probe1", "probe")?.target).toBe(203);
  });

  it("returns the array unchanged for a label with no type:'probe' entry", () => {
    const before = fixture();
    const out = applyTargetEdit(before, "NoSuchProbe", ON);
    expect(out).toEqual(before);
    expect(out).toHaveLength(before.length);
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

  it("reads the whole array, edits one entry, and posts it back in ONE request", async () => {
    const before = fixture();
    const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async (url: string) =>
      String(url).endsWith("/api/get/notify")
        ? { ok: true, status: 200, json: async () => ({ result: "OK", data: before }) }
        : { ok: true, status: 201, json: async () => ({ result: "success" }) },
    );
    rs.stubGlobal("fetch", fetchMock);

    await saveTargetEdit("", "Probe1", ON);

    // Exactly two round trips: one GET, one POST. A per-field REST grammar would
    // be four POSTs for one user gesture, with a window in which the target is
    // set but its action is not. (Those four would no longer eat each other --
    // the drain merges notify_data element-wise -- but they are still four.)
    expect(fetchMock.mock.calls).toHaveLength(2);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/get/notify");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/control");
    const posted = JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body)) as {
      notify_data: NotifyEntry[];
    };
    expect(entryFor(posted.notify_data, "Probe1", "probe")).toMatchObject({
      req: true,
      target: 203,
      keep_warm: true,
    });
    expect(posted.notify_data).toHaveLength(before.length);
  });

  it("never posts when the read fails -- an empty array would wipe every alert", async () => {
    const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({}),
    }));
    rs.stubGlobal("fetch", fetchMock);
    await expect(saveTargetEdit("", "Probe1", ON)).rejects.toThrow(/HTTP 503/);
    expect(fetchMock.mock.calls).toHaveLength(1);
  });
});
