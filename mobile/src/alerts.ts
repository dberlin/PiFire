import type { DashSocketPayload } from "@pifire/core/contracts/core";

export interface Alert {
  /** Stable across repeated payloads describing the same underlying event, so
   *  a reconnect that redelivers state already seen produces the same id
   *  rather than a fresh one -- see each alert's own id comment below for how
   *  it's built. */
  id: string;
  title: string;
  body: string;
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
  // target", not on being there. A probe sitting at its target for an hour
  // must alert once, not on every payload -- so this compares the previous
  // payload's own at-target state for the same probe, not just the current
  // one.
  const previousByLabel = new Map(previous.foodProbes.map((p) => [p.label, p]));
  for (const probe of next.foodProbes) {
    if (!probe.targetReq || probe.target <= 0) {
      continue;
    }
    if (probe.temp === null) {
      // The contract types this nullable; there is no temperature to compare
      // against a target without one.
      continue;
    }
    const before = previousByLabel.get(probe.label);
    const wasAtTarget =
      before !== undefined &&
      before.targetReq &&
      before.target > 0 &&
      before.temp !== null &&
      before.temp >= before.target;
    const isAtTarget = probe.temp >= probe.target;
    if (isAtTarget && !wasAtTarget) {
      alerts.push({
        // Keyed to the target value too: if the target changes after this
        // fires (armed again for a different temp), that is a genuinely new
        // threshold to cross, not a repeat of the same event.
        id: `probe-target:${probe.label}:${probe.target}`,
        title: `${probe.title} reached target`,
        body: `${probe.title} is at ${probe.temp}°, its ${probe.target}° target.`,
      });
    }
  }

  // Timer expiry. The control process decides a timer has expired by
  // comparing control.timer.end against its own clock (see
  // common/api_commands.py's docstring above _TIMER_EXPIRY_OPTIONS) and, once
  // it fires the timer's shutdown/keep_warm action, resets control["timer"]
  // back to the idle shape common/defaults.py seeds it with -- start, paused
  // and end all 0 (common/defaults.py:564). A transition from an active
  // countdown (end > 0) to that cleared state is the signal this side can
  // observe. The id carries the specific end time so a new timer that
  // happens to finish at the same instant on a later payload cannot be
  // mistaken for a repeat of this one.
  //
  // Known imprecision: @pifire/core's command.ts documents timerStop() as
  // ALSO clearing timer.end straight to 0 (verified against
  // common/api_commands.py's _cmd_set_timer). A user who manually stops a
  // running timer therefore produces the same end>0 -> end==0 transition as
  // one that actually finished, and gets the same "Timer done" alert. There
  // is nothing else in this payload that tells the two apart.
  if (previous.timer.end > 0 && next.timer.end === 0) {
    alerts.push({
      id: `timer:${previous.timer.end}`,
      title: "Timer done",
      body: "Your PiFire timer has finished.",
    });
  }

  return alerts;
}
