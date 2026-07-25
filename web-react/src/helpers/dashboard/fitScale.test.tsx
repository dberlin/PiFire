import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, render, screen } from "@testing-library/react";
import { useFitScale } from "./hooks";

// useFitScale sizes the 1280x720 dashboard stage to the box it is shown in.
// jsdom lays nothing out, so getBoundingClientRect is stubbed to stand in for
// the browser's layout; window.innerWidth/innerHeight are jsdom's defaults
// (1024x768) unless a test changes them.

function Probe() {
  const { scale, ref } = useFitScale(1280, 720);
  return (
    <div ref={ref}>
      <span data-testid="scale">{scale}</span>
    </div>
  );
}

const readScale = () => Number(screen.getByTestId("scale").textContent);

const realRect = HTMLDivElement.prototype.getBoundingClientRect;
const realObserver = globalThis.ResizeObserver;

function stubBox(width: number, height: number) {
  HTMLDivElement.prototype.getBoundingClientRect = () =>
    ({ width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0 }) as DOMRect;
}

afterEach(() => {
  HTMLDivElement.prototype.getBoundingClientRect = realRect;
  globalThis.ResizeObserver = realObserver;
  cleanup();
});

describe("useFitScale", () => {
  it("scales to the measured box, not the window", () => {
    // A window-based scale would be min(1024/1280, 768/720) = 0.8; measuring
    // this box gives min(1280/1280, 648/720) = 0.9. The two answers have to
    // differ or the assertion proves nothing.
    stubBox(1280, 648);

    render(<Probe />);

    expect(readScale()).toBeCloseTo(0.9);
  });

  it("fits the limiting dimension", () => {
    stubBox(640, 720);

    render(<Probe />);

    expect(readScale()).toBeCloseTo(0.5);
  });

  it("falls back to the viewport when the box has not been laid out", () => {
    // No stub: jsdom reports a 0x0 rect for everything.
    render(<Probe />);

    expect(readScale()).toBeCloseTo(Math.min(window.innerWidth / 1280, window.innerHeight / 720));
  });

  it("re-measures when the box changes without a window resize", () => {
    // Exactly what happens when the shell's timer strip or alert strip appears:
    // the window is untouched, the dashboard's box is not.
    const observe = rs.fn();
    let notify: (() => void) | null = null;
    class FakeResizeObserver {
      constructor(callback: () => void) {
        notify = callback;
      }
      observe = observe;
      unobserve = rs.fn();
      disconnect = rs.fn();
    }
    globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
    stubBox(1280, 720);

    render(<Probe />);
    expect(readScale()).toBeCloseTo(1);
    expect(observe).toHaveBeenCalled();

    stubBox(1280, 648);
    act(() => notify?.());

    expect(readScale()).toBeCloseTo(0.9);
  });
});
