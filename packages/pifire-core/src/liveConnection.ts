import { io } from "socket.io-client";
import type { PelletSocketPayload } from "./contracts/control.gen";
import type { DashSocketPayload } from "./contracts/core.gen";

export type ConnectionPhase = "connecting" | "live" | "unreachable" | "demo";

/** Process-local receipt time; never persist or compare with producer clocks. */
export const monotonicNowMs = (): number => performance.now();

export const DURATION_RECEIPT_MAX_AGE_MS = 30_000;

/** Project only local receipt elapsed, never server or browser wall endpoints.
 * Invalid receipts display the last reported snapshot, explicitly not current. */
export function projectLiveDurations(
  payload: DashSocketPayload,
  receivedMonotonicMs: number | null,
  nowMonotonicMs: number,
  connected: boolean,
): DashSocketPayload {
  const ageMs = receivedMonotonicMs === null ? NaN : nowMonotonicMs - receivedMonotonicMs;
  const receiptCurrent = connected && Number.isFinite(ageMs) && ageMs >= 0
    && ageMs <= DURATION_RECEIPT_MAX_AGE_MS;
  const elapsedS = receiptCurrent ? ageMs / 1000 : 0;
  const timer = payload.timer;
  const durations = payload.durations;
  const advanceDurations = durations.current && durations.running;
  const elapsed = (value: number | null) =>
    value === null ? null : value + (advanceDurations ? elapsedS : 0);
  const remaining = (value: number | null, advancing: boolean) =>
    value === null ? null : Math.max(0, value - (advancing ? elapsedS : 0));
  return {
    ...payload,
    timer: {
      ...timer,
      current: timer.current && receiptCurrent,
      remainingS: remaining(timer.remainingS, timer.current && timer.state === "running"),
    },
    durations: {
      ...durations,
      current: durations.current && receiptCurrent,
      modeElapsedS: elapsed(durations.modeElapsedS),
      cookElapsedS: elapsed(durations.cookElapsedS),
      modeRemainingS: remaining(durations.modeRemainingS, advanceDurations),
      lidRemainingS: remaining(durations.lidRemainingS, advanceDurations),
    },
  };
}

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
  /** Socket factory override for tests. Production passes nothing and gets socket.io. */
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
      // Notify the consumer immediately: on the path this exists for (a
      // phone foregrounding after iOS tore the socket down), the old phase
      // ("live" or "unreachable") is stale the instant the socket is
      // closed, and must not keep being presented as current until the new
      // socket happens to connect.
      setPhase("connecting");
      socket = open();
    },
    close() {
      socket.close();
    },
  };
}
