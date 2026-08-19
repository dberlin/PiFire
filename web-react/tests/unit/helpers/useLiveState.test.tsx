import type { PelletDbSchema } from "@pifire/core/contracts/control";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import type { ConnectionPhase, LiveConnectionHandlers } from "@pifire/core/liveConnection";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";

const PELLET_DB: PelletDbSchema = {
  schema_version: 2,
  current: {
    pelletid: "p1",
    hopper_level: 62,
    date_loaded: "2026-07-25 09:00:00",
    est_usage: 1200,
  },
  brands: ["Generic", "Custom"],
  woods: ["Alder", "Oak"],
  archive: {
    p1: { brand: "Generic", wood: "Alder", rating: 4, comments: "placeholder" },
  },
  log: { "1785013200000": { pelletid: "p1", deleted: false } },
  lastupdated: { time: 1785000000 },
};

// The socket lives in @pifire/core now, with the wire protocol -- handler set,
// listen_app_data, the connect_error guard, the pellet envelope -- covered by
// packages/pifire-core/tests/liveConnection.test.ts. What is left for the hook
// is the adapter: subscribe once, map each callback onto its own piece of
// state, and close the connection on unmount. So the seam mocked here is
// createLiveConnection, not socket.io-client: core imports socket.io-client
// through its OWN node_modules, a different resolved path than web-react's, so
// a socket.io-client mock registered from this file never intercepts it.
let handlers: LiveConnectionHandlers;
let urls: string[];
const closeMock = rs.fn();
const reconnectMock = rs.fn();

// A function declaration, not a const: rs.mock factories are hoisted above
// every top-level binding in this file, so anything they reach for at module
// init has to be hoisted too.
function fakeConnection(url: string, incoming: LiveConnectionHandlers) {
  urls.push(url);
  handlers = incoming;
  return { close: closeMock, reconnect: reconnectMock };
}

rs.mock("@pifire/core/liveConnection", () => ({
  createLiveConnection: (url: string, incoming: LiveConnectionHandlers) =>
    fakeConnection(url, incoming),
}));

const { useLiveState } = await import("../../../src/helpers/useLiveState");

beforeEach(() => {
  urls = [];
  closeMock.mockClear();
  reconnectMock.mockClear();
});

afterEach(() => {
  handlers = undefined as unknown as LiveConnectionHandlers;
});

// PUBLIC_DEMO is an import.meta.env value baked in at build time, unset in
// this test build, so FORCE_DEMO is always false here -- only the live-socket
// branch is reachable. See task-2b22 brief: demo branch left uncovered
// rather than contorting the module to reach it.
describe("useLiveState (live mode)", () => {
  it("opens one connection to the configured target and hands it all three callbacks", () => {
    const { rerender } = renderHook(() => useLiveState());

    // "" is what PUBLIC_PIFIRE_URL resolves to under rstest.config.ts, i.e.
    // same-origin, which is what a request through the dev proxy needs.
    expect(urls).toEqual([""]);
    expect(typeof handlers.onDash).toBe("function");
    expect(typeof handlers.onPellets).toBe("function");
    expect(typeof handlers.onPhase).toBe("function");

    // The effect has no dependencies, so a re-render must not tear down a
    // healthy socket and open a second one.
    rerender();
    expect(urls).toEqual([""]);
  });

  it("starts in the connecting phase and reports whatever phase the connection announces", () => {
    const { result } = renderHook(() => useLiveState());
    expect(result.current.phase).toBe("connecting");

    act(() => handlers.onPhase("live"));
    expect(result.current.phase).toBe("live");

    act(() => handlers.onPhase("unreachable"));
    expect(result.current.phase).toBe("unreachable");
  });

  it("replaces the live state with each dash frame", () => {
    const { result } = renderHook(() => useLiveState());
    const frame: DashSocketPayload = {
      ...result.current.live,
      currentMode: "Hold",
      smokePlus: true,
    };

    act(() => handlers.onDash(frame));

    expect(result.current.live.currentMode).toBe("Hold");
    expect(result.current.live.smokePlus).toBe(true);
  });

  // The pellet database arrives on its own socket channel, which the backend
  // emits on change and directly to a freshly connected client
  // (blueprints/mobile/socket_io.py). The pellets page is its only consumer.
  //
  // NOT covered here: "in FORCE_DEMO mode pellets stays null". PUBLIC_DEMO is
  // baked in at build time and unset in this test build, so the demo branch is
  // unreachable from these tests -- see the describe-block comment above.
  it("exposes pellets as null until the first pellet database arrives", () => {
    const { result } = renderHook(() => useLiveState());
    expect(result.current.pellets).toBeNull();

    act(() => handlers.onPellets(PELLET_DB));

    expect(result.current.pellets).toEqual(PELLET_DB);
  });

  it("a dash frame does not clear an already-received pellet database", () => {
    const { result } = renderHook(() => useLiveState());
    act(() => handlers.onPellets(PELLET_DB));

    act(() => handlers.onDash({ ...result.current.live, currentMode: "Hold" }));

    expect(result.current.pellets).toEqual(PELLET_DB);
  });

  it("closes the connection on unmount", () => {
    const { unmount } = renderHook(() => useLiveState());
    expect(closeMock).not.toHaveBeenCalled();

    unmount();

    expect(closeMock).toHaveBeenCalled();
  });

  it("derives controlAlive from the live state and exposes a command client + fallback targetUrl", () => {
    const { result } = renderHook(() => useLiveState());
    expect(typeof result.current.controlAlive).toBe("boolean");
    expect(result.current.targetUrl).toBe("http://localhost:5000");
    expect(result.current.command).toBeTruthy();
    expect(typeof result.current.command.setMode).toBe("function");
  });

  it("treats the demo phase as control-alive without a dash frame to prove it", () => {
    const { result } = renderHook(() => useLiveState());

    act(() => handlers.onPhase("demo" as ConnectionPhase));

    expect(result.current.controlAlive).toBe(true);
  });
});
