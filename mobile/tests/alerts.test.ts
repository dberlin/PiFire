import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { alertsFor } from "../src/alerts";

// A fixed target, independent of the temp being asserted each call, so two
// calls with the same probe name have something stable to cross (below it,
// then at/above it) rather than both landing "at target" the instant the
// probe is created.
const PROBE_TARGET = 204;

function withProbeTemp(dash: DashSocketPayload, name: string, temp: number): DashSocketPayload {
  const clone = structuredClone(dash);
  const existing = clone.foodProbes.find((p) => p.label === name);
  if (existing) {
    existing.temp = temp;
    return clone;
  }
  const probe = structuredClone(clone.foodProbes[0]);
  probe.label = name;
  probe.title = name;
  probe.target = PROBE_TARGET;
  probe.targetReq = true;
  probe.temp = temp;
  clone.foodProbes.push(probe);
  return clone;
}

function withError(dash: DashSocketPayload, code: string): DashSocketPayload {
  const clone = structuredClone(dash);
  clone.errors = [...clone.errors, code];
  return clone;
}

// Models what the server ACTUALLY sends on the payload that reports a probe
// crossing its target: notify/notifications.py's check_notify (~lines
// 105-115) fires the notification and, in the same pass, clears
// notify_data[index]["req"]/["target"] back to False/0; blueprints/mobile/
// socket_io.py (~lines 872-876) maps that straight into the probe's
// targetReq/target fields. So the crossing payload itself is already
// unarmed -- withProbeTemp's fixture (which leaves targetReq true forever)
// cannot produce this shape, and alertsFor must not require the NEXT
// payload to still be armed.
function withProbeCrossingAndCleared(dash: DashSocketPayload, name: string, temp: number): DashSocketPayload {
  const clone = withProbeTemp(dash, name, temp);
  const probe = clone.foodProbes.find((p) => p.label === name);
  if (!probe) throw new Error(`withProbeCrossingAndCleared: no probe named ${name}`);
  probe.targetReq = false;
  probe.target = 0;
  return clone;
}

it("alerts once when a probe reaches its target", () => {
  const before = withProbeTemp(FIXTURE_DASH, "Brisket", 200);
  const after = withProbeTemp(before, "Brisket", 204);
  const alerts = alertsFor(before, after);
  expect(alerts).toHaveLength(1);
  expect(alerts[0].title).toMatch(/Brisket/);
});

it("does not re-alert while the probe stays at target", () => {
  const at = withProbeTemp(FIXTURE_DASH, "Brisket", 204);
  expect(alertsFor(at, withProbeTemp(at, "Brisket", 205))).toEqual([]);
});

it("alerts on a grill error", () => {
  const alerts = alertsFor(FIXTURE_DASH, withError(FIXTURE_DASH, "GRILL_ERROR_01"));
  expect(alerts.map((a) => a.id)).toContain("GRILL_ERROR_01");
});

// The scenarios point 2 of the task brief calls out by name, beyond the three
// required above.

it("raises nothing on the very first payload after launch, even if it already carries an error, a timer that just cleared, and a probe already at target", () => {
  const busy = withError(withProbeTemp(FIXTURE_DASH, "Brisket", PROBE_TARGET), "GRILL_ERROR_01");
  expect(alertsFor(null, busy)).toEqual([]);
});

it("raises nothing on a reconnect that replays an identical payload", () => {
  const busy = withError(withProbeTemp(FIXTURE_DASH, "Brisket", PROBE_TARGET), "GRILL_ERROR_01");
  // Same object twice: exactly what a reconnect that redelivers state already
  // seen looks like from alertsFor's point of view.
  expect(alertsFor(busy, busy)).toEqual([]);
});

it("alerts on the real server transition: crossing payload already carries targetReq:false and target:0", () => {
  const before = withProbeTemp(FIXTURE_DASH, "Brisket", 200); // armed, below target
  const crossed = withProbeCrossingAndCleared(before, "Brisket", 204); // server already cleared it
  const alerts = alertsFor(before, crossed);
  expect(alerts).toHaveLength(1);
  expect(alerts[0].title).toMatch(/Brisket/);
});

it("does not re-alert on the payload after the server clears targetReq/target", () => {
  const before = withProbeTemp(FIXTURE_DASH, "Brisket", 200);
  const cleared = withProbeCrossingAndCleared(before, "Brisket", 204);
  // A further payload, still hot, with the clear already in effect: `before`
  // for this comparison is `cleared`, whose targetReq is false, so nothing
  // should fire even though the temperature is still at/above the old target.
  const further = withProbeTemp(cleared, "Brisket", 210);
  expect(alertsFor(cleared, further)).toEqual([]);
});

it("re-arms after a probe dips below target and reaches it again", () => {
  const at = withProbeTemp(FIXTURE_DASH, "Brisket", PROBE_TARGET);
  const dipped = withProbeTemp(at, "Brisket", PROBE_TARGET - 5);
  const backAtTarget = withProbeTemp(dipped, "Brisket", PROBE_TARGET);

  // The dip itself alerts nothing (crossing target downward is not an event).
  expect(alertsFor(at, dipped)).toEqual([]);
  // Reaching it again after the dip alerts once more.
  const alerts = alertsFor(dipped, backAtTarget);
  expect(alerts).toHaveLength(1);
  expect(alerts[0].title).toMatch(/Brisket/);
});
