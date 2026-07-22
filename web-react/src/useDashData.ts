import { useEffect, useMemo, useRef, useState } from "react";
import { io, type Socket } from "socket.io-client";
import type { DashData } from "./types";
import { FIXTURE_DASH } from "./fixture";
import { demoDashAt } from "./demoData";
import { createCommand, type CommandClient } from "./command";
import { deriveControlAlive } from "./dashboard/health";

export type ConnectionPhase = "connecting" | "live" | "unreachable" | "demo";

interface DashState {
  dash: DashData;
  phase: ConnectionPhase;
  controlAlive: boolean;
  targetUrl: string;
  command: CommandClient;
}

const FORCE_DEMO = import.meta.env.VITE_DEMO === "1" || import.meta.env.VITE_DEMO === "true";
const TARGET_URL = import.meta.env.VITE_PIFIRE_URL || "";

export function useDashData(): DashState {
  const [dash, setDash] = useState<DashData>(FIXTURE_DASH);
  const [phase, setPhase] = useState<ConnectionPhase>(FORCE_DEMO ? "demo" : "connecting");
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    if (FORCE_DEMO) {
      const start = Date.now();
      const tick = () => setDash(demoDashAt((Date.now() - start) / 1000));
      tick();
      const id = window.setInterval(tick, 1000);
      return () => window.clearInterval(id);
    }
    const socket = io(TARGET_URL || undefined, { path: "/socket.io", reconnection: true, timeout: 4000 });
    socketRef.current = socket;
    socket.on("connect", () => { setPhase("live"); socket.emit("listen_app_data"); });
    socket.on("connect_error", () => setPhase((p) => (p === "live" ? p : "unreachable")));
    socket.on("disconnect", () => setPhase("unreachable"));
    socket.on("socket_dash_data", (data: DashData) => { setPhase("live"); setDash(data); });
    return () => { socket.close(); socketRef.current = null; };
  }, []);

  const command = useMemo(() => createCommand(TARGET_URL), []);
  const controlAlive = phase === "demo" ? true : deriveControlAlive(dash);

  return { dash, phase, controlAlive, targetUrl: TARGET_URL || "http://localhost:5000", command };
}
