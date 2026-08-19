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
