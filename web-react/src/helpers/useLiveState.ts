import { useEffect, useMemo, useRef, useState } from "react";
import { io, type Socket } from "socket.io-client";
import { type CommandClient, createCommand } from "./command";
import { deriveControlAlive } from "./dashboard/health";
import { demoDashAt } from "./demoData";
import { FIXTURE_DASH } from "./fixture";
import type { LiveState } from "./types";

export type ConnectionPhase = "connecting" | "live" | "unreachable" | "demo";

// Exported because AppShell hands this exact bundle to its child routes via
// Outlet context (helpers/shellContext.ts). Sharing the type keeps the context
// from drifting away from what the hook actually returns.
export interface LiveStateResult {
  live: LiveState;
  phase: ConnectionPhase;
  controlAlive: boolean;
  targetUrl: string;
  command: CommandClient;
}

const FORCE_DEMO = import.meta.env.PUBLIC_DEMO === "1" || import.meta.env.PUBLIC_DEMO === "true";
const TARGET_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export function useLiveState(): LiveStateResult {
  const [live, setLive] = useState<LiveState>(FIXTURE_DASH);
  const [phase, setPhase] = useState<ConnectionPhase>(FORCE_DEMO ? "demo" : "connecting");
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    if (FORCE_DEMO) {
      const start = Date.now();
      const tick = () => setLive(demoDashAt((Date.now() - start) / 1000));
      tick();
      const id = window.setInterval(tick, 1000);
      return () => window.clearInterval(id);
    }
    const socket = io(TARGET_URL || undefined, {
      path: "/socket.io",
      reconnection: true,
      timeout: 4000,
    });
    socketRef.current = socket;
    socket.on("connect", () => {
      setPhase("live");
      socket.emit("listen_app_data");
    });
    socket.on("connect_error", () => setPhase((p) => (p === "live" ? p : "unreachable")));
    socket.on("disconnect", () => setPhase("unreachable"));
    socket.on("socket_dash_data", (data: LiveState) => {
      setPhase("live");
      setLive(data);
    });
    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, []);

  const command = useMemo(() => createCommand(TARGET_URL), []);
  const controlAlive = phase === "demo" ? true : deriveControlAlive(live);

  // `targetUrl` is for DISPLAY (ConnectionStatus), not for fetching -- the
  // fetch base is TARGET_URL, which is empty in dev on purpose so requests stay
  // same-origin and hit the proxy. When it is empty we still want to name the
  // backend truthfully, so fall back to the proxy's target rather than to a
  // hardcoded 5000 that lies in any workspace running its own backend.
  return {
    live,
    phase,
    controlAlive,
    targetUrl: TARGET_URL || import.meta.env.PUBLIC_PIFIRE_TARGET || "http://localhost:5000",
    command,
  };
}
