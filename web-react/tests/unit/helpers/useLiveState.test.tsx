import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";
import type { PelletDb } from "../../../src/helpers/pellets/pelletTypes";
import type { LiveState } from "../../../src/helpers/types";
import { useLiveState } from "../../../src/helpers/useLiveState";

const PELLET_DB: PelletDb = {
  current: {
    pelletid: "p1",
    hopper_level: 62,
    date_loaded: "2026-07-25 09:00:00",
    est_usage: 1200,
  },
  brands: ["Generic", "Custom"],
  woods: ["Alder", "Oak"],
  archive: {
    p1: { id: "p1", brand: "Generic", wood: "Alder", rating: 4, comments: "placeholder" },
  },
  log: { "2026-07-25 09:00:00": "p1" },
  lastupdated: { time: 1785000000 },
};

type Handler = (...args: unknown[]) => void;

let handlers: Record<string, Handler>;
let fakeSocket: {
  on: ReturnType<typeof rs.fn>;
  off: ReturnType<typeof rs.fn>;
  close: ReturnType<typeof rs.fn>;
  emit: ReturnType<typeof rs.fn>;
};
const ioMock = rs.fn();

rs.mock("socket.io-client", () => ({
  io: (...args: unknown[]) => ioMock(...args),
}));

beforeEach(() => {
  handlers = {};
  fakeSocket = {
    on: rs.fn((event: string, cb: Handler) => {
      handlers[event] = cb;
      return fakeSocket;
    }),
    off: rs.fn(),
    close: rs.fn(),
    emit: rs.fn(),
  };
  ioMock.mockReturnValue(fakeSocket);
});

afterEach(() => {
  ioMock.mockReset();
});

// PUBLIC_DEMO is an import.meta.env value baked in at build time, unset in
// this test build, so FORCE_DEMO is always false here — only the live-socket
// branch is reachable. See task-2b22 brief: demo branch left uncovered
// rather than contorting the module to reach it.
describe("useLiveState (live mode)", () => {
  it("opens a socket.io connection and registers the expected handlers", () => {
    renderHook(() => useLiveState());
    expect(ioMock).toHaveBeenCalled();
    expect(Object.keys(handlers)).toEqual(
      expect.arrayContaining(["connect", "connect_error", "disconnect", "socket_dash_data"]),
    );
  });

  it("starts in the connecting phase and flips to live + emits listen_app_data on connect", () => {
    const { result } = renderHook(() => useLiveState());
    expect(result.current.phase).toBe("connecting");

    act(() => handlers.connect());

    expect(result.current.phase).toBe("live");
    expect(fakeSocket.emit).toHaveBeenCalledWith("listen_app_data");
  });

  it("the first socket_dash_data frame replaces the live state and flips phase to live", () => {
    const { result } = renderHook(() => useLiveState());
    const frame: LiveState = { ...result.current.live, currentMode: "Hold", smokePlus: true };

    act(() => handlers.socket_dash_data(frame));

    expect(result.current.live.currentMode).toBe("Hold");
    expect(result.current.live.smokePlus).toBe(true);
    expect(result.current.phase).toBe("live");
  });

  it("disconnect flips phase to unreachable", () => {
    const { result } = renderHook(() => useLiveState());
    act(() => handlers.connect());
    expect(result.current.phase).toBe("live");

    act(() => handlers.disconnect());

    expect(result.current.phase).toBe("unreachable");
  });

  it("connect_error sets unreachable while not yet connected, but is a no-op once live", () => {
    const { result } = renderHook(() => useLiveState());

    act(() => handlers.connect_error());
    expect(result.current.phase).toBe("unreachable");

    act(() => handlers.connect());
    expect(result.current.phase).toBe("live");

    act(() => handlers.connect_error());
    expect(result.current.phase).toBe("live");
  });

  it("closes the socket on unmount", () => {
    const { unmount } = renderHook(() => useLiveState());
    unmount();
    expect(fakeSocket.close).toHaveBeenCalled();
  });

  // The pellet database arrives on its own socket channel, which the backend
  // emits on change and directly to a freshly connected client
  // (blueprints/mobile/socket_io.py). The pellets page is its only consumer.
  //
  // NOT covered here: "in FORCE_DEMO mode pellets stays null". PUBLIC_DEMO is
  // baked in at build time and unset in this test build, so the demo branch is
  // unreachable from these tests -- see the describe-block comment above.
  it("registers a socket_pellet_data handler", () => {
    renderHook(() => useLiveState());
    expect(Object.keys(handlers)).toEqual(expect.arrayContaining(["socket_pellet_data"]));
  });

  it("exposes pellets as null until the first socket_pellet_data frame arrives", () => {
    const { result } = renderHook(() => useLiveState());
    expect(result.current.pellets).toBeNull();
  });

  it("stores the inner pellets object, not the {uuid, pellets} envelope", () => {
    const { result } = renderHook(() => useLiveState());

    act(() => handlers.socket_pellet_data({ uuid: "u", pellets: PELLET_DB }));

    expect(result.current.pellets).toEqual(PELLET_DB);
    // Guard against storing the envelope: uuid must not leak into the value.
    expect(result.current.pellets).not.toHaveProperty("uuid");
  });

  it("a socket_dash_data frame does not clear an already-received pellet database", () => {
    const { result } = renderHook(() => useLiveState());
    act(() => handlers.socket_pellet_data({ uuid: "u", pellets: PELLET_DB }));

    act(() => handlers.socket_dash_data({ ...result.current.live, currentMode: "Hold" }));

    expect(result.current.pellets).toEqual(PELLET_DB);
  });

  it("derives controlAlive from the live state and exposes a command client + fallback targetUrl", () => {
    const { result } = renderHook(() => useLiveState());
    expect(typeof result.current.controlAlive).toBe("boolean");
    expect(result.current.targetUrl).toBe("http://localhost:5000");
    expect(result.current.command).toBeTruthy();
    expect(typeof result.current.command.setMode).toBe("function");
  });
});
