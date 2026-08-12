import { type RefObject, useLayoutEffect, useRef, useState } from "react";

/** The width at and above which the dashboard keeps its fixed scaled board.
 *  Must match the `@media (min-width: 1280px)` block in dashboard.css -- the
 *  CSS decides the geometry, this decides whether to drive it. */
export const FIT_QUERY = "(min-width: 1280px)";

export interface FitScale {
  /** Uniform scale for the 1280x720 board. 1 when not fitting. */
  scale: number;
  /** Whether the fixed, scaled board is the active layout. */
  fitted: boolean;
  ref: RefObject<HTMLDivElement | null>;
}

/**
 * Scale-to-fit for the desktop board, and ONLY for the desktop board.
 *
 * The reflow (C8) made the dashboard responsive, and it is responsive below
 * 1280px. At 1280px and above it goes back to being a fixed 1280x720 board
 * scaled uniformly into the space the app shell leaves it, because that is what
 * "do not change how it looks at 1280x720" turned out to require: in a literal
 * 1280x720 window the navbar takes ~56px, so an unscaled board pushes its
 * control row -- Smoke / Hold / Smoke+ / Shutdown / Stop -- off the bottom
 * edge. Clipping the primary controls for a live fire is not a tradeoff worth
 * making for a breakpoint nobody uses at that width.
 *
 * `fitted` is deliberately separate from `scale === 1`: at a viewport with no
 * chrome the scale IS 1, but the board still needs its centring transform,
 * because the CSS pins it at top/left 50%.
 *
 * It measures its own box rather than the viewport because the shell puts a
 * navbar above the dashboard, and sometimes a timer strip and an alert strip
 * too. A ResizeObserver catches that chrome appearing and disappearing, which
 * changes the box without any window resize. The window is the fallback: an
 * element measuring 0x0 has not been laid out (jsdom never lays anything out).
 */
export function useFitScale(w: number, h: number): FitScale {
  const ref = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);
  const [fitted, setFitted] = useState(false);

  useLayoutEffect(() => {
    // jsdom implements matchMedia but evaluates width queries against a 1024px
    // window, so this is false there -- which is the correct answer for a
    // viewport that size, and keeps the component tests on the reflow branch.
    const query = window.matchMedia(FIT_QUERY);

    const update = () => {
      if (!query.matches) {
        setFitted(false);
        setScale(1);
        return;
      }
      const box = ref.current?.getBoundingClientRect();
      const availW = box && box.width > 0 ? box.width : window.innerWidth;
      const availH = box && box.height > 0 ? box.height : window.innerHeight;
      setFitted(true);
      setScale(Math.min(availW / w, availH / h));
    };

    update();
    window.addEventListener("resize", update);
    query.addEventListener("change", update);
    // Guarded: jsdom has no ResizeObserver, and the window listener above
    // already covers the case the tests can actually produce.
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => update());
    if (observer !== null && ref.current !== null) observer.observe(ref.current);
    return () => {
      window.removeEventListener("resize", update);
      query.removeEventListener("change", update);
      observer?.disconnect();
    };
  }, [w, h]);

  return { scale, fitted, ref };
}
