import { type RefObject, useEffect, useLayoutEffect, useRef, useState } from "react";

// Ticks once a second so the header clock and cook-time counter stay live.
export function useClock(): Date {
  const [now, setNow] = useState<Date>(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

// The dashboard is authored at a fixed 1280x720 (the on-device touchscreen).
// Scale it uniformly to fit the box it is shown in -- attach the returned ref
// to that box.
//
// It measures the box rather than the viewport because the dashboard is no
// longer alone on screen: the app shell puts a navbar above it, and sometimes
// a timer strip and an alert strip too, so the area left for the stage is
// shorter than the window. Scaling against window.innerHeight would oversize
// the stage by exactly the height of that chrome and clip its header. A
// ResizeObserver is what catches the chrome appearing and disappearing, since
// that changes the box without any window resize.
//
// The window is still the fallback: an element that measures 0x0 has not been
// laid out (jsdom never lays anything out), and the viewport is the best
// available answer then -- which is also what this hook did before the shell.
export function useFitScale(
  w: number,
  h: number,
): { scale: number; ref: RefObject<HTMLDivElement | null> } {
  const ref = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);

  useLayoutEffect(() => {
    const updateScale = () => {
      const box = ref.current?.getBoundingClientRect();
      const availW = box && box.width > 0 ? box.width : window.innerWidth;
      const availH = box && box.height > 0 ? box.height : window.innerHeight;
      setScale(Math.min(availW / w, availH / h));
    };
    updateScale();
    window.addEventListener("resize", updateScale);
    // Guarded: jsdom has no ResizeObserver, and the window listener above
    // already covers the case the tests can actually produce.
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => updateScale());
    if (observer !== null && ref.current !== null) observer.observe(ref.current);
    return () => {
      window.removeEventListener("resize", updateScale);
      observer?.disconnect();
    };
  }, [w, h]);

  return { scale, ref };
}
