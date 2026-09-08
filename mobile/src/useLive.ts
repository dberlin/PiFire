import { type CommandClient, createCommand } from "@pifire/core/command";
import type { PelletSocketPayload } from "@pifire/core/contracts/control";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { deriveControlAlive } from "@pifire/core/dashboard/health";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import {
  type ConnectionPhase,
  type LiveConnection,
  createLiveConnection,
  monotonicNowMs,
  projectLiveDurations,
} from "@pifire/core/liveConnection";
import { useEffect, useMemo, useRef, useState } from "react";
import { AppState } from "react-native";

export type { ConnectionPhase };

export const LIVE_STALE_AFTER_MS = 30_000;

export interface LiveResult {
  live: DashSocketPayload;
  phase: ConnectionPhase;
  controlAlive: boolean;
  pellets: PelletSocketPayload["pellets"] | null;
  command: CommandClient;
  /** Process-local monotonic ms of the newest dash payload; null before a real
   *  payload or after background/resume invalidates the receipt. */
  lastPayloadMonotonicMs: number | null;
  /** The connected grill's API base (same value `command` was built from).
   *  REST reads that are not part of the socket payload -- history.tsx's
   *  `/api/history/chart` fetch -- need this directly; `command` only
   *  exposes control writes, not a base URL. */
  host: string;
}

/** Shared by retained health and the transport header; never consult wall time. */
export function receiptFreshness(result: LiveResult, now: number) {
  const payloadAgeMs = result.lastPayloadMonotonicMs === null
    ? null
    : now - result.lastPayloadMonotonicMs;
  const validAge = payloadAgeMs !== null && Number.isFinite(payloadAgeMs) && payloadAgeMs >= 0;
  return {
    payloadAgeMs: validAge ? payloadAgeMs : null,
    retained: result.phase !== "live" || !validAge || payloadAgeMs > LIVE_STALE_AFTER_MS,
  };
}

export function qualifyRetainedHealth(result: LiveResult, now: number): LiveResult {
  const { payloadAgeMs, retained } = receiptFreshness(result, now);
  const projected = projectLiveDurations(
    result.live, result.lastPayloadMonotonicMs, now, result.phase === "live",
  );
  return {
    ...result,
    live: {
      ...projected,
      thermocoupleHealth: result.live.thermocoupleHealth?.map((health) => ({
        ...health,
        freshness: {
          ...health.freshness,
          current: health.freshness.current && health.freshness.lastReportedAgeS !== null && !retained,
          lastReportedAgeS:
            health.freshness.lastReportedAgeS === null || payloadAgeMs === null
              ? null
              : health.freshness.lastReportedAgeS + payloadAgeMs / 1000,
          reason: payloadAgeMs === null || health.freshness.lastReportedAgeS === null
            ? "unknown-clock"
            : retained ? "retained" : health.freshness.reason,
        },
      })),
    },
  };
}

// A backgrounded iOS app suspends its socket without reliably surfacing a
// "disconnect" -- see LiveConnection.reconnect's doc comment in
// @pifire/core/liveConnection. Foregrounding therefore forces a reconnect
// rather than trusting the transport to notice on its own.
//
// reconnect() closes the old socket before opening a new one, and
// socket.io can fire that old socket's "disconnect" synchronously inside
// close() -- the shared connection surfaces that as an "unreachable" phase
// an instant before it reports "connecting" for the new socket. That is
// correct (the old socket really did just die) but undebounced it would
// flash a false failure at the user every time they unlock their phone.
// Delaying "unreachable" just long enough for a same-tick "connecting" (or
// "live") to preempt it keeps that transition invisible without touching
// core's phase semantics.
const UNREACHABLE_DEBOUNCE_MS = 400;

export function useLive(host: string): LiveResult {
  const [live, setLive] = useState<DashSocketPayload>(FIXTURE_DASH);
  const [phase, setPhase] = useState<ConnectionPhase>("connecting");
  const [pellets, setPellets] = useState<PelletSocketPayload["pellets"] | null>(null);
  const [lastPayloadMonotonicMs, setLastPayloadMonotonicMs] = useState<number | null>(null);

  const connectionRef = useRef<LiveConnection | null>(null);
  const activeRef = useRef(AppState.currentState !== "background" && AppState.currentState !== "inactive");

  // Owns the connection's lifetime: open on mount (or host change), close
  // on unmount.
  useEffect(() => {
    let unreachableTimer: ReturnType<typeof setTimeout> | null = null;
    setLastPayloadMonotonicMs(null);

    function showPhase(next: ConnectionPhase) {
      if (next !== "live") setLastPayloadMonotonicMs(null);
      if (unreachableTimer) {
        clearTimeout(unreachableTimer);
        unreachableTimer = null;
      }
      if (next === "unreachable") {
        unreachableTimer = setTimeout(() => {
          unreachableTimer = null;
          setPhase("unreachable");
        }, UNREACHABLE_DEBOUNCE_MS);
        return;
      }
      setPhase(next);
    }

    const connection = createLiveConnection(host, {
      onDash: (payload) => {
        if (!activeRef.current) return;
        setLive(payload);
        setLastPayloadMonotonicMs(monotonicNowMs());
      },
      onPellets: setPellets,
      onPhase: showPhase,
    });
    connectionRef.current = connection;

    return () => {
      connectionRef.current = null;
      if (unreachableTimer) {
        clearTimeout(unreachableTimer);
      }
      connection.close();
    };
  }, [host]);

  // A reset receipt clock cannot regain validity merely by catching up to its
  // previous coordinate. Only onDash can establish a new receipt.
  useEffect(() => {
    if (lastPayloadMonotonicMs === null) return;
    const timer = setInterval(() => {
      const ageMs = monotonicNowMs() - lastPayloadMonotonicMs;
      if (!Number.isFinite(ageMs) || ageMs < 0) {
        setLastPayloadMonotonicMs(null);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [lastPayloadMonotonicMs]);

  // Mobile-only concern, kept separate from connection lifetime: force a
  // reconnect whenever the app comes back to the foreground.
  useEffect(() => {
    const subscription = AppState.addEventListener("change", (next) => {
      activeRef.current = next === "active";
      setLastPayloadMonotonicMs(null);
      if (next === "active") {
        connectionRef.current?.reconnect();
      }
    });
    return () => subscription.remove();
  }, []);

  const command = useMemo(() => createCommand(host), [host]);
  const controlAlive = deriveControlAlive(live);

  return { live, phase, controlAlive, pellets, command, lastPayloadMonotonicMs, host };
}
