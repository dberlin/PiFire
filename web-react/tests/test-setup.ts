import { afterEach, beforeEach, expect } from "@rstest/core";
import * as matchers from "@testing-library/jest-dom/matchers";
import { cleanup } from "@testing-library/react";

import { assertConsoleClean, installConsoleGuard } from "./consoleGuard";

expect.extend(matchers);

// Unmount before the console check, in the same hook: teardown effects log too,
// and a separate afterEach would leave the order between them to the runner.
beforeEach(installConsoleGuard);
afterEach(() => {
  cleanup();
  assertConsoleClean();
});

// jsdom implements neither `window.matchMedia` nor canvas 2D contexts. uPlot
// (src/components/history/HistoryChart.tsx) touches both at construction
// time to track devicePixelRatio and to draw. Component tests only assert
// the mount/update/unmount contract (jsdom has no canvas to assert pixels
// against), so a minimal no-op stub is enough to let uPlot construct without
// throwing.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}

// jsdom's canvas getContext("2d") returns null unless the (native-binding)
// `canvas` npm package is installed, which this project intentionally does
// not depend on for a headless test run. Stand in a permissive stub: stored
// properties (e.g. `ctx.font = ...`) round-trip, everything else no-ops as a
// function call. Good enough for uPlot to run its draw routines without
// throwing; pixel output isn't something these tests assert on.
if (typeof HTMLCanvasElement !== "undefined") {
  const noop = () => {};
  HTMLCanvasElement.prototype.getContext = ((..._args: unknown[]) =>
    new Proxy(
      {},
      {
        get: (target, prop) => (prop in target ? (target as never)[prop] : noop),
      },
    )) as unknown as typeof HTMLCanvasElement.prototype.getContext;
}

// jsdom implements no ResizeObserver. virtua, the virtualizer inside
// @melloware/react-logviewer (src/components/logs/LogViewer.tsx), constructs
// one per list and throws outright without it -- the component never mounts.
// A no-op observer is enough: jsdom reports every box as 0x0 anyway, so there
// is no measurement to deliver, and virtua falls back to its estimated item
// size and renders its initial window. Line CONTENT is what these tests
// assert; which lines virtualization keeps mounted is not assertable here.
if (typeof (globalThis as { ResizeObserver?: unknown }).ResizeObserver === "undefined") {
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// jsdom also has no Path2D (used by uPlot to build series paths before
// stroking them via the stubbed context above). Same permissive-stub idea,
// via a plain constructor function rather than a class -- a class
// constructor returning a value trips the noConstructorReturn lint, but an
// ordinary function invoked with `new` is allowed to override `this` this way.
if (typeof (globalThis as { Path2D?: unknown }).Path2D === "undefined") {
  function Path2DStub() {
    return new Proxy(
      {},
      {
        get: (target, prop) => (prop in target ? (target as never)[prop] : () => {}),
      },
    );
  }
  (globalThis as { Path2D?: unknown }).Path2D = Path2DStub;
}
