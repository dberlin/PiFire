import { type CommandClient, createCommand } from "@pifire/core/command";
import type { PelletSocketPayload } from "@pifire/core/contracts/control";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { deriveControlAlive } from "@pifire/core/dashboard/health";
import { demoDashAt } from "@pifire/core/demoData";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import {
  type ConnectionPhase,
  createLiveConnection,
  DURATION_RECEIPT_MAX_AGE_MS,
  monotonicNowMs,
  projectLiveDurations,
} from "@pifire/core/liveConnection";
import { useEffect, useMemo, useState } from "react";

export type { ConnectionPhase };

// Exported because AppShell hands this exact bundle to its child routes via
// Outlet context (helpers/shellContext.ts). Sharing the type keeps the context
// from drifting away from what the hook actually returns.
export interface LiveStateResult {
  live: DashSocketPayload;
  phase: ConnectionPhase;
  controlAlive: boolean;
  targetUrl: string;
  command: CommandClient;
  /** The whole pellet database, or null until the first socket_pellet_data
      arrives (and forever in demo mode, which opens no socket). The backend
      emits this on change at a 1s cadence and directly to a freshly
      connected client (blueprints/mobile/socket_io.py), so the pellets page
      needs no polling and no refetch after a write. */
  pellets: PelletSocketPayload["pellets"] | null;
}

const FORCE_DEMO = import.meta.env.PUBLIC_DEMO === "1" || import.meta.env.PUBLIC_DEMO === "true";
const TARGET_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export function useLiveState(): LiveStateResult {
  const [live, setLive] = useState<DashSocketPayload>(FIXTURE_DASH);
  const [phase, setPhase] = useState<ConnectionPhase>(FORCE_DEMO ? "demo" : "connecting");
  const [pellets, setPellets] = useState<PelletSocketPayload["pellets"] | null>(null);
  const [receivedMonotonicMs, setReceivedMonotonicMs] = useState<number | null>(null);
  const [nowMonotonicMs, setNowMonotonicMs] = useState(monotonicNowMs);

  useEffect(() => {
    if (FORCE_DEMO) {
      const start = monotonicNowMs();
      const tick = () => setLive(demoDashAt((monotonicNowMs() - start) / 1000));
      tick();
      const id = window.setInterval(tick, 1000);
      return () => window.clearInterval(id);
    }
    const connection = createLiveConnection(TARGET_URL, {
      onDash: (payload) => {
        if (document.visibilityState === "hidden") return;
        const now = monotonicNowMs();
        setLive(payload);
        setReceivedMonotonicMs(now);
        setNowMonotonicMs(now);
      },
      onPellets: setPellets,
      onPhase: (next) => {
        if (next !== "live") setReceivedMonotonicMs(null);
        setPhase(next);
      },
    });
    const invalidate = () => setReceivedMonotonicMs(null);
    document.addEventListener("visibilitychange", invalidate);
    window.addEventListener("pagehide", invalidate);
    window.addEventListener("pageshow", invalidate);
    return () => {
      document.removeEventListener("visibilitychange", invalidate);
      window.removeEventListener("pagehide", invalidate);
      window.removeEventListener("pageshow", invalidate);
      connection.close();
    };
  }, []);

  useEffect(() => {
    if (FORCE_DEMO || receivedMonotonicMs === null) return;
    const id = window.setInterval(() => {
      const now = monotonicNowMs();
      const ageMs = now - receivedMonotonicMs;
      if (!Number.isFinite(ageMs) || ageMs < 0 || ageMs > DURATION_RECEIPT_MAX_AGE_MS) {
        setReceivedMonotonicMs(null);
      }
      setNowMonotonicMs(now);
    }, 1000);
    return () => window.clearInterval(id);
  }, [receivedMonotonicMs]);

  const command = useMemo(() => createCommand(TARGET_URL), []);
  const controlAlive = phase === "demo" ? true : deriveControlAlive(live);

  // `targetUrl` is for DISPLAY (ConnectionStatus), not for fetching -- the
  // fetch base is TARGET_URL, which is empty in dev on purpose so requests stay
  // same-origin and hit the proxy. When it is empty we still want to name the
  // backend truthfully, so fall back to the proxy's target rather than to a
  // hardcoded 5000 that lies in any workspace running its own backend.
  return {
    live: FORCE_DEMO ? live : projectLiveDurations(
      live, receivedMonotonicMs, nowMonotonicMs, phase === "live",
    ),
    phase,
    controlAlive,
    targetUrl: TARGET_URL || import.meta.env.PUBLIC_PIFIRE_TARGET || "http://localhost:5000",
    command,
    pellets,
  };
}
