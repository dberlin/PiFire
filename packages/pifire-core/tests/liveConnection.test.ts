import { expect, it, rs } from "@rstest/core";
import { createLiveConnection, type ConnectionPhase } from "../src/liveConnection";

// Records what the default socket factory asks socket.io for. Every other test
// in this file injects `createSocket` and never reaches this.
const ioCalls: unknown[][] = [];

function recordIo(...args: unknown[]) {
  ioCalls.push(args);
  return { on() {}, emit() {}, close() {}, connect() {} };
}

rs.mock("socket.io-client", () => ({
  io: (...args: unknown[]) => recordIo(...args),
}));

function fakeSocket() {
  const handlers: Record<string, (payload?: unknown) => void> = {};
  const emitted: string[] = [];
  let closes = 0;
  return {
    socket: {
      on(event: string, handler: (payload?: unknown) => void) {
        handlers[event] = handler;
      },
      emit(event: string) {
        emitted.push(event);
      },
      close() {
        closes += 1;
      },
      connect() {},
    },
    events: () => Object.keys(handlers),
    emitted,
    closes: () => closes,
    fire(event: string, payload?: unknown) {
      handlers[event]?.(payload);
    },
  };
}

function connect(fake: ReturnType<typeof fakeSocket>, overrides = {}) {
  const phases: ConnectionPhase[] = [];
  const connection = createLiveConnection("http://pi.local:5000", {
    onDash() {},
    onPellets() {},
    onPhase: (phase) => phases.push(phase),
    createSocket: () => fake.socket,
    ...overrides,
  });
  return { connection, phases };
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


it("subscribes to every event the backend emits", () => {
  const fake = fakeSocket();
  connect(fake);

  expect(fake.events()).toEqual(
    expect.arrayContaining([
      "connect",
      "connect_error",
      "disconnect",
      "socket_dash_data",
      "socket_pellet_data",
    ]),
  );
});

it("asks for the app data feed the moment the socket connects", () => {
  const fake = fakeSocket();
  connect(fake);

  // Nothing is streamed until this is sent, so a connection that skips it
  // sits there looking healthy and delivering nothing.
  expect(fake.emitted).toEqual([]);
  fake.fire("connect");
  expect(fake.emitted).toEqual(["listen_app_data"]);
});

it("reports unreachable on a connect_error before it is live, and ignores one after", () => {
  const fake = fakeSocket();
  const { phases } = connect(fake);

  fake.fire("connect_error");
  fake.fire("connect");
  // socket.io retries in the background, and a retry that fails while a
  // working connection is already up says nothing about that connection.
  fake.fire("connect_error");

  expect(phases).toEqual(["unreachable", "live"]);
});

it("treats a dash frame as evidence the feed is live, and delivers it", () => {
  const fake = fakeSocket();
  const frames: unknown[] = [];
  const { phases } = connect(fake, { onDash: (frame: unknown) => frames.push(frame) });

  fake.fire("socket_dash_data", { currentMode: "Hold" });

  expect(frames).toEqual([{ currentMode: "Hold" }]);
  expect(phases).toEqual(["live"]);
});

it("hands on the pellet database itself, not the envelope carrying it", () => {
  const fake = fakeSocket();
  const received: unknown[] = [];
  connect(fake, { onPellets: (pellets: unknown) => received.push(pellets) });

  fake.fire("socket_pellet_data", { uuid: "u", pellets: { brands: ["Generic"] } });

  expect(received).toEqual([{ brands: ["Generic"] }]);
});

it("closes the underlying socket when the consumer closes the connection", () => {
  const fake = fakeSocket();
  const { connection } = connect(fake);
  expect(fake.closes()).toBe(0);

  connection.close();

  expect(fake.closes()).toBe(1);
});

it("opens socket.io at the given url with the transport options the backend serves", () => {
  ioCalls.length = 0;

  createLiveConnection("http://pi.local:5000", {
    onDash() {},
    onPellets() {},
    onPhase() {},
  });

  expect(ioCalls).toEqual([
    ["http://pi.local:5000", { path: "/socket.io", reconnection: true, timeout: 4000 }],
  ]);
});

it("lets socket.io pick the origin when no url is configured", () => {
  ioCalls.length = 0;

  // "" is what a same-origin build passes; forwarding it verbatim would ask
  // socket.io to connect to the empty string rather than to the page's origin.
  createLiveConnection("", { onDash() {}, onPellets() {}, onPhase() {} });

  expect(ioCalls[0]?.[0]).toBeUndefined();
});
