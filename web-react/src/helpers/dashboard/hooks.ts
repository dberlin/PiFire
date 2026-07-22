import { useEffect, useLayoutEffect, useState } from "react";

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
// Scale it uniformly to fit whatever browser viewport it's shown in.
export function useFitScale(w: number, h: number): number {
  const [scale, setScale] = useState(1);
  useLayoutEffect(() => {
    const updateScale = () => setScale(Math.min(window.innerWidth / w, window.innerHeight / h));
    updateScale();
    window.addEventListener("resize", updateScale);
    return () => window.removeEventListener("resize", updateScale);
  }, [w, h]);
  return scale;
}
