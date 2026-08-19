import { io } from "socket.io-client";
import type { PelletSocketPayload } from "./contracts/control.gen";
import type { DashSocketPayload } from "./contracts/core.gen";

export type ConnectionPhase = "connecting" | "live" | "unreachable" | "demo";

/** Minimal shape createLiveConnection needs from a socket. socket.io-client's
 *  `Socket` satisfies this structurally; tests inject a fake one instead of
 *  opening a real connection. */
export interface SocketLike {
  on(event: string, handler: (payload?: unknown) => void): unknown;
  emit(event: string, ...args: unknown[]): unknown;
  close(): unknown;
  connect(): unknown;
}

export interface LiveConnectionHandlers {
  onDash(payload: DashSocketPayload): void;
  onPellets(pellets: PelletSocketPayload["pellets"]): void;
  onPhase(phase: ConnectionPhase): void;
  /** Injection seam for tests. Production passes nothing and gets socket.io. */
  createSocket?: (url: string) => SocketLike;
}

export interface LiveConnection {
  /** Force a reconnect — mobile calls this when the app returns to the
   *  foreground and iOS has torn the socket down underneath it. */
  reconnect(): void;
  close(): void;
}

function defaultCreateSocket(url: string): SocketLike {
  return io(url || undefined, {
    path: "/socket.io",
    reconnection: true,
    timeout: 4000,
  });
}

export function createLiveConnection(
  url: string,
  handlers: LiveConnectionHandlers,
): LiveConnection {
  const createSocket = handlers.createSocket ?? defaultCreateSocket;
  // Tracked locally (rather than relying on handlers.onPhase's caller to
  // remember it) so connect_error can replicate the same "don't downgrade an
  // already-live connection" guard the React hook used to implement via a
  // setState updater.
  let phase: ConnectionPhase = "connecting";
  let socket: SocketLike;

  function setPhase(next: ConnectionPhase) {
    phase = next;
    handlers.onPhase(next);
  }

  function open(): SocketLike {
    const s = createSocket(url);
    s.on("connect", () => {
      setPhase("live");
      s.emit("listen_app_data");
    });
    s.on("connect_error", () => {
      if (phase !== "live") setPhase("unreachable");
    });
    s.on("disconnect", () => setPhase("unreachable"));
    s.on("socket_dash_data", (data?: unknown) => {
      setPhase("live");
      handlers.onDash(data as DashSocketPayload);
    });
    // Deliberately does NOT touch phase: phase is socket_dash_data's and
    // connect's business, and a pellet payload arriving is not evidence the
    // dash feed is healthy.
    s.on("socket_pellet_data", (data?: unknown) => {
      handlers.onPellets((data as PelletSocketPayload).pellets);
    });
    return s;
  }

  socket = open();

  return {
    reconnect() {
      socket.close();
      phase = "connecting";
      socket = open();
    },
    close() {
      socket.close();
    },
  };
}
