import { expect, it } from "@rstest/core";
import { createLiveConnection, type ConnectionPhase } from "../src/liveConnection";

function fakeSocket() {
  const handlers: Record<string, (payload?: unknown) => void> = {};
  return {
    socket: {
      on(event: string, handler: (payload?: unknown) => void) {
        handlers[event] = handler;
      },
      emit() {},
      close() {},
      connect() {},
    },
    fire(event: string, payload?: unknown) {
      handlers[event]?.(payload);
    },
  };
}

it("reports live on connect and unreachable on disconnect", () => {
  const phases: ConnectionPhase[] = [];
  const fake = fakeSocket();
  createLiveConnection("http://pi.local:5000", {
    onDash() {},
    onPellets() {},
    onPhase: (phase) => phases.push(phase),
    createSocket: () => fake.socket,
  });
  fake.fire("connect");
  fake.fire("disconnect");
  expect(phases).toEqual(["live", "unreachable"]);
});

it("does not let a pellet payload claim the dash feed is healthy", () => {
  const phases: ConnectionPhase[] = [];
  const fake = fakeSocket();
  createLiveConnection("http://pi.local:5000", {
    onDash() {},
    onPellets() {},
    onPhase: (phase) => phases.push(phase),
    createSocket: () => fake.socket,
  });
  fake.fire("socket_pellet_data", { pellets: {} });
  expect(phases).toEqual([]);
});

it("reconnect() announces connecting, opens a fresh socket, and rewires handlers to it", () => {
  const phases: ConnectionPhase[] = [];
  const dashPayloads: unknown[] = [];
  const fakes = [fakeSocket(), fakeSocket()];
  let socketsCreated = 0;

  const connection = createLiveConnection("http://pi.local:5000", {
    onDash: (payload) => dashPayloads.push(payload),
    onPellets() {},
    onPhase: (phase) => phases.push(phase),
    createSocket: () => fakes[socketsCreated++].socket,
  });

  fakes[0].fire("connect");
  connection.reconnect();
  fakes[1].fire("connect");
  fakes[1].fire("socket_dash_data", { currentMode: "Hold" });

  // "connecting" must be reported the moment the stale socket is torn down,
  // not silently skipped in favor of whatever phase happens to follow. The
  // trailing "live" is socket_dash_data's own (separate) phase report.
  expect(phases).toEqual(["live", "connecting", "live", "live"]);
  expect(socketsCreated).toBe(2);
  // Proves handlers were rewired onto the SECOND socket, not left pointed at
  // the first (a reconnect that silently stops delivering data would pass
  // the phase assertion above but fail this one).
  expect(dashPayloads).toEqual([{ currentMode: "Hold" }]);
});
