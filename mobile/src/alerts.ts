import type { DashSocketPayload, ThermocoupleHealthView } from "@pifire/core/contracts/core";
import { projectProbeHealth } from "@pifire/core/dashboard/probeHealth";

export interface Alert {
  /** Stable across repeated payloads describing the same underlying event, so
   *  a reconnect that redelivers state already seen produces the same id
   *  rather than a fresh one -- see each alert's own id comment below for how
   *  it's built. */
  id: string;
  title: string;
  body: string;
}

function thermocoupleKey(health: ThermocoupleHealthView): string {
  return `${health.device}\u0000${health.port}\u0000${health.label}`;
}

function confirmedThermocoupleAlert(health: ThermocoupleHealthView): Alert {
  const projected = projectProbeHealth(health);
  const id = `thermocouple:${health.device}:${health.port}:${health.label}`;

  if (health.role === "Primary" && health.outcome === "notify_only") {
    return {
      id,
      title: "Control-probe fault detected",
      body: "Observe mode did not stop heating. Stop and inspect the pit probe now.",
    };
  }
  if (health.role === "Primary" && health.outcome === "stopped") {
    return {
      id,
      title: "Control probe unavailable",
      body: "PiFire stopped heating because the control temperature is unavailable.",
    };
  }
  if (health.role !== "Primary") {
    return {
      id,
      title: `${health.displayName} probe unavailable`,
      body: [projected.causeCopy, projected.impactCopy]
        .filter((part): part is string => part !== null)
        .join(" "),
    };
  }

  return {
    id,
    title: projected.headline ?? "Control-probe fault detected",
    body: [projected.impactCopy, projected.causeCopy]
      .filter((part): part is string => part !== null)
      .join(" "),
  };
}

/**
 * Compares two consecutive dash payloads and returns the alerts the
 * transition between them warrants. Pure and stateless: every notification
 * this app ever sends is decided here, not in the effect that delivers it
 * (see app/_layout.tsx), which is what makes the decision testable without a
 * device.
 *
 * `previous === null` means "no earlier payload to compare against" -- the
 * very first payload after launch, before this app has seen anything. That
 * always returns no alerts: a probe already sitting at its target, an error
 * that was already showing, a timer that had already cleared -- none of that
 * just happened, it's what was already true when the app opened, and an app
 * that greets a fresh launch with a burst of notifications for pre-existing
 * state is one people delete.
 */
export function alertsFor(previous: DashSocketPayload | null, next: DashSocketPayload): Alert[] {
  if (previous === null) {
    return [];
  }

  const alerts: Alert[] = [];

  // Health notification delivery stays edge-triggered here with every other
  // local alert: only entry into the authoritative confirmed state qualifies.
  // Healthy/suspected recovery therefore rearms the next genuine confirmation,
  // while identical and reconnect-replayed confirmed frames remain quiet.
  const previousHealthByProbe = new Map(
    (previous.thermocoupleHealth ?? []).map((health) => [thermocoupleKey(health), health]),
  );
  for (const health of next.thermocoupleHealth ?? []) {
    if (health.report.state !== "confirmed") {
      continue;
    }
    if (previousHealthByProbe.get(thermocoupleKey(health))?.report.state === "confirmed") {
      continue;
    }
    alerts.push(confirmedThermocoupleAlert(health));
  }

  // Grill errors: dash.errors is free text, not a fixed set of codes (see
  // web-react/src/components/shell/Banners.tsx, which renders each entry
  // verbatim) -- so the string itself is what identifies "the same error".
  // Using it as the id means an error still present on the next payload
  // (including one redelivered by a reconnect) never re-alerts, only one
  // that is newly present does.
  for (const err of next.errors) {
    if (!previous.errors.includes(err)) {
      alerts.push({ id: err, title: "Grill error", body: err });
    }
  }

  // Probe target reached: edge-triggered on CROSSING into "at or past
  // target", not on being there.
  //
  // This must be evaluated against the PREVIOUS payload's armed target, not
  // the next payload's. notify/notifications.py's check_notify (~lines
  // 105-115) fires the server's own "Probe_Temp_Achieved" notification and,
  // in the SAME pass, clears the target it just fired for: sets
  // control["notify_data"][index]["req"] = False and ["target"] = 0.
  // blueprints/mobile/socket_io.py (~lines 872-876) maps that straight into
  // the probe's targetReq/target fields. So the very first dash payload that
  // reports temp >= target already carries targetReq: false and target: 0 --
  // requiring the NEXT payload to still be armed skips exactly the payload
  // that should alert, and would never fire against a real grill. Requiring
  // only that the PREVIOUS
  // payload was armed and below target is what a probe sitting at its target
  // for an hour still alerts once for: once the server clears the flag,
  // every later payload's `before` is unarmed and the condition below is
  // false.
  const previousByLabel = new Map(previous.foodProbes.map((p) => [p.label, p]));
  for (const nextProbe of next.foodProbes) {
    const before = previousByLabel.get(nextProbe.label);
    if (before === undefined || !before.targetReq || before.target <= 0) {
      continue;
    }
    if (before.temp === null || nextProbe.temp === null) {
      // The contract types this nullable; there is no temperature to
      // compare against a target without one.
      continue;
    }
    if (before.temp < before.target && nextProbe.temp >= before.target) {
      alerts.push({
        // Keyed to the armed target value too: a probe re-armed for a
        // different temp after this fires is a genuinely new threshold to
        // cross, not a repeat of the same event.
        id: `probe-target:${before.label}:${before.target}`,
        title: `${nextProbe.title} reached target`,
        body: `${nextProbe.title} is at ${nextProbe.temp}°, its ${before.target}° target.`,
      });
    }
  }

  // Only an explicit controller expiry is completion. Manual stop, local zero,
  // interruption and a replacement timer must never produce this alert.
  if (next.timer.state === "expired" && previous.timer.state !== "expired"
      && next.timer.timerId !== null && next.timer.timerId === previous.timer.timerId) {
    alerts.push({
      id: `timer:${next.timer.timerId}`,
      title: "Timer done",
      body: "Your PiFire timer has finished.",
    });
  }

  return alerts;
}
