import { useEffect, useRef, useState } from "react";
import { io, type Socket } from "socket.io-client";
import type { DashData, SendCommand } from "./types";
import { FIXTURE_DASH } from "./fixture";
import { demoDashAt } from "./demoData";

export type ConnectionPhase = "connecting" | "live" | "unreachable" | "demo";

interface DashState {
  dash: DashData;
  phase: ConnectionPhase;
  targetUrl: string;
  send: SendCommand;
}

// `bun run demo` (or VITE_DEMO=1) forces the offline live simulator.
// Plain `bun run dev` talks to a real PiFire and, if it can't reach one, shows
// an explicit "unreachable" state naming the URL — it does NOT silently fall
// back to demo data.
const FORCE_DEMO = import.meta.env.VITE_DEMO === "1" || import.meta.env.VITE_DEMO === "true";
// The address the dev server proxies /socket.io to (what the user must make
// reachable). Shown verbatim in the unreachable state.
const TARGET_URL = import.meta.env.VITE_PIFIRE_URL || "http://localhost:5000";

// Binding to PiFire's existing SocketIO contract (same channel the Qt bridge
// and current web dashboard use):
//   connect -> emit "listen_app_data" -> receive "socket_dash_data" (~1 Hz)
//   commands -> emit "post_app_data"(action, type, json_data) -> write_control
export function useDashData(): DashState {
  const [dash, setDash] = useState<DashData>(FIXTURE_DASH);
  const [phase, setPhase] = useState<ConnectionPhase>(FORCE_DEMO ? "demo" : "connecting");
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    // Forced demo: run the offline simulator, no socket.
    if (FORCE_DEMO) {
      const start = Date.now();
      const tick = () => setDash(demoDashAt((Date.now() - start) / 1000));
      tick();
      const id = window.setInterval(tick, 1000);
      return () => window.clearInterval(id);
    }

    const socket = io({ path: "/socket.io", reconnection: true, timeout: 4000 });
    socketRef.current = socket;

    socket.on("connect", () => {
      setPhase("live");
      socket.emit("listen_app_data");
    });
    // Failed/lost connection -> explicit unreachable (keeps retrying underneath).
    socket.on("connect_error", () => setPhase((p) => (p === "live" ? p : "unreachable")));
    socket.on("disconnect", () => setPhase("unreachable"));
    socket.on("socket_dash_data", (data: DashData) => setDash(data));

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, []);

  const send: SendCommand = (action, type, jsonData) => {
    const s = socketRef.current;
    if (s && s.connected) {
      s.emit("post_app_data", action, type, jsonData ?? null);
    } else {
      console.info("[offline] post_app_data not sent:", { action, type, jsonData });
    }
  };

  return { dash, phase, targetUrl: TARGET_URL, send };
}
