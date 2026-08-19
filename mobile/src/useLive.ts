import { useEffect, useMemo, useRef, useState } from "react";
import { AppState } from "react-native";
import { type CommandClient, createCommand } from "@pifire/core/command";
import type { PelletSocketPayload } from "@pifire/core/contracts/control";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { deriveControlAlive } from "@pifire/core/dashboard/health";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import {
  type ConnectionPhase,
  type LiveConnection,
  createLiveConnection,
} from "@pifire/core/liveConnection";

export type { ConnectionPhase };

export interface LiveResult {
  live: DashSocketPayload;
  phase: ConnectionPhase;
  controlAlive: boolean;
  pellets: PelletSocketPayload["pellets"] | null;
  command: CommandClient;
  /** Epoch ms of the newest dash payload, or null before the first one has
   *  arrived. This is how the UI knows a reading is stale rather than
   *  presenting an old value as current. */
  lastPayloadAt: number | null;
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
  const [lastPayloadAt, setLastPayloadAt] = useState<number | null>(null);

  const connectionRef = useRef<LiveConnection | null>(null);

  // Owns the connection's lifetime: open on mount (or host change), close
  // on unmount.
  useEffect(() => {
    let unreachableTimer: ReturnType<typeof setTimeout> | null = null;

    function showPhase(next: ConnectionPhase) {
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
        setLive(payload);
        setLastPayloadAt(Date.now());
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

  // Mobile-only concern, kept separate from connection lifetime: force a
  // reconnect whenever the app comes back to the foreground.
  useEffect(() => {
    const subscription = AppState.addEventListener("change", (next) => {
      if (next === "active") {
        connectionRef.current?.reconnect();
      }
    });
    return () => subscription.remove();
  }, []);

  const command = useMemo(() => createCommand(host), [host]);
  const controlAlive = deriveControlAlive(live);

  return { live, phase, controlAlive, pellets, command, lastPayloadAt };
}
