import { useState } from "react";

// dash.errors NEVER clears itself. read_errors() (common/datastore_accessors.py:126-132)
// is a plain non-destructive blob read -- unlike `warnings` on the same payload,
// which drains (q.list(); q.flush()) and therefore self-heals frame to frame --
// and its only clearer, flush_errors(), is called from exactly one place in
// production: control.py:107-109, at boot. So once _check_control_status
// (blueprints/mobile/socket_io.py:1009-1019) writes the "control process did
// not respond" string, it is on every subsequent socket_dash_data frame until
// the control process restarts.
//
// It can also be written on a HEALTHY system: get_system_command_output
// (common/app.py:31-44) pops the shared queue_systemo and DISCARDS entries whose
// command does not match, so any of its seven consumers can eat the check_alive
// reply, the 1s timeout expires, and the sticky error lands anyway.
//
// The frontend cannot clear the blob -- there is no route, socket action or API
// command that does. What it CAN do is ask the same question directly and
// believe the answer. GET /api/sys/check_alive (blueprints/api/routes.py:299-311)
// runs exactly the same probe and answers {"result": "OK"} when control replies.

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
 * a live probe that just succeeded is better evidence than a blob written up to
 * 30 seconds ago that nothing in the system can clear.
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
