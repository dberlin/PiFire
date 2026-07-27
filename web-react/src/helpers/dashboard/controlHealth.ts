import { useState } from "react";

// The "control process did not respond" entry in dash.errors is a POLL RESULT,
// not a durable error. blueprints/mobile/socket_io.py re-probes every 30s and
// composes common/app.py's CONTROL_DOWN_ERROR into each payload from that
// verdict alone (`_control_alive`), so the entry is gone from the first frame
// after control answers again.
//
// It used to be appended to the errors blob instead, which nothing reachable
// from the UI could clear -- one missed answer was permanent until the control
// process restarted. That is fixed on the backend; do not reintroduce a
// clearing endpoint here.
//
// What the fix does NOT remove is the 30s window: a payload can still say
// "down" for up to one poll interval after control has recovered. So an
// on-demand probe is still worth having. GET /api/sys/check_alive runs the same
// probe synchronously and answers {"result": "OK"} when control replies.

export async function recheckControl(baseUrl: string): Promise<boolean> {
  try {
    const res = await fetch(`${baseUrl}/api/sys/check_alive`);
    if (!res.ok) return false;
    const body = (await res.json()) as { result?: string };
    return body.result === "OK";
  } catch {
    return false;
  }
}

export interface ControlHealth {
  /** Whether the control process is believed reachable right now. */
  alive: boolean;
  /** Whether the PAYLOAD still carries the error, regardless of `alive`. */
  stale: boolean;
  recheck(): Promise<void>;
  rechecking: boolean;
}

/**
 * Control-process liveness with a manual override.
 *
 * `alive` is computed at render (`controlAlive || override`) rather than mirrored
 * into an effect, so there is no setState-in-useEffect for derived state.
 *
 * The override deliberately persists across later frames that still say false:
 * a live probe that just succeeded is better evidence than a poll result that
 * may be up to 30 seconds old. It is sticky in the other direction, though --
 * a control process that dies again after a successful recheck will not flip
 * `alive` back within this mount. That is acceptable because the underlying
 * signal now self-heals both ways, so the override exists only to close the
 * one-poll-interval gap, and `stale` still reports the raw payload verdict for
 * anything that wants it.
 */
export function useControlHealth(controlAlive: boolean, apiBase: string): ControlHealth {
  const [override, setOverride] = useState(false);
  const [rechecking, setRechecking] = useState(false);

  const recheck = async () => {
    setRechecking(true);
    try {
      if (await recheckControl(apiBase)) setOverride(true);
    } finally {
      setRechecking(false);
    }
  };

  return { alive: controlAlive || override, stale: !controlAlive, recheck, rechecking };
}
