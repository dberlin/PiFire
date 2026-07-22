import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";
import type { DashData } from "./types";
import { useDashData } from "./useDashData";

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
describe("useDashData (live mode)", () => {
  it("opens a socket.io connection and registers the expected handlers", () => {
    renderHook(() => useDashData());
    expect(ioMock).toHaveBeenCalled();
    expect(Object.keys(handlers)).toEqual(
      expect.arrayContaining(["connect", "connect_error", "disconnect", "socket_dash_data"]),
    );
  });

  it("starts in the connecting phase and flips to live + emits listen_app_data on connect", () => {
    const { result } = renderHook(() => useDashData());
    expect(result.current.phase).toBe("connecting");

    act(() => handlers.connect());

    expect(result.current.phase).toBe("live");
    expect(fakeSocket.emit).toHaveBeenCalledWith("listen_app_data");
  });

  it("the first socket_dash_data frame replaces dash and flips phase to live", () => {
    const { result } = renderHook(() => useDashData());
    const frame: DashData = { ...result.current.dash, currentMode: "Hold", smokePlus: true };

    act(() => handlers.socket_dash_data(frame));

    expect(result.current.dash.currentMode).toBe("Hold");
    expect(result.current.dash.smokePlus).toBe(true);
    expect(result.current.phase).toBe("live");
  });

  it("disconnect flips phase to unreachable", () => {
    const { result } = renderHook(() => useDashData());
    act(() => handlers.connect());
    expect(result.current.phase).toBe("live");

    act(() => handlers.disconnect());

    expect(result.current.phase).toBe("unreachable");
  });

  it("connect_error sets unreachable while not yet connected, but is a no-op once live", () => {
    const { result } = renderHook(() => useDashData());

    act(() => handlers.connect_error());
    expect(result.current.phase).toBe("unreachable");

    act(() => handlers.connect());
    expect(result.current.phase).toBe("live");

    act(() => handlers.connect_error());
    expect(result.current.phase).toBe("live");
  });

  it("closes the socket on unmount", () => {
    const { unmount } = renderHook(() => useDashData());
    unmount();
    expect(fakeSocket.close).toHaveBeenCalled();
  });

  it("derives controlAlive from dash state and exposes a command client + fallback targetUrl", () => {
    const { result } = renderHook(() => useDashData());
    expect(typeof result.current.controlAlive).toBe("boolean");
    expect(result.current.targetUrl).toBe("http://localhost:5000");
    expect(result.current.command).toBeTruthy();
    expect(typeof result.current.command.setMode).toBe("function");
  });
});
