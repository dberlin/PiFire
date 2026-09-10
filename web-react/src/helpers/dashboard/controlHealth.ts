import type { ControlHealthResponse } from "@pifire/core/contracts/core";
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
    const body = (await res.json()) as ControlHealthResponse;
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
 * A manual check can supersede the poll verdict in the snapshot it checked,
 * not a later authoritative packet. Socket decoding supplies a new errors
 * array per packet; local display projections preserve that array's identity.
 * Capturing it at request start also prevents a late response from overriding
 * a newer packet (or another API target).
 */
export function useControlHealth(
  controlAlive: boolean,
  apiBase: string,
  snapshotErrors: readonly string[],
): ControlHealth {
  const [override, setOverride] = useState<{
    errors: readonly string[];
    apiBase: string;
  } | null>(null);
  const [rechecking, setRechecking] = useState(false);

  const recheck = async () => {
    setRechecking(true);
    try {
      const alive = await recheckControl(apiBase);
      setOverride(alive ? { errors: snapshotErrors, apiBase } : null);
    } finally {
      setRechecking(false);
    }
  };

  const overrideCurrent = override?.errors === snapshotErrors && override.apiBase === apiBase;
  return { alive: controlAlive || overrideCurrent, stale: !controlAlive, recheck, rechecking };
}
