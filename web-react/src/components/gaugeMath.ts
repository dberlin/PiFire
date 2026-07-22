// Pure geometry for the 270° arc gauge (QML Gauge.qml equivalent).
// The gauge sweeps 270°, symmetric gap at the bottom: track runs from -135°
// to +135° (0° = 12 o'clock, clockwise positive).

export const GAUGE_SWEEP = 270;
export const GAUGE_START = -135; // degrees from top, clockwise

export function clampFraction(value: number, max: number): number {
  if (max <= 0) return 0;
  const f = value / max;
  return f < 0 ? 0 : f > 1 ? 1 : f;
}

// Angle (deg from top, clockwise) for a given value on the gauge.
export function valueAngle(value: number, max: number): number {
  return GAUGE_START + clampFraction(value, max) * GAUGE_SWEEP;
}

export function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const a = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
}

// SVG path for an arc from startAngle to endAngle (clockwise), 0° = top.
export function describeArc(cx: number, cy: number, r: number, startAngle: number, endAngle: number): string {
  const start = polarToCartesian(cx, cy, r, startAngle);
  const end = polarToCartesian(cx, cy, r, endAngle);
  const largeArc = endAngle - startAngle > 180 ? 1 : 0;
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 1 ${end.x} ${end.y}`;
}

// Circumference of the full 270° track — used for the stroke-dashoffset fill.
export function arcLength(r: number): number {
  return r * (GAUGE_SWEEP * Math.PI) / 180;
}
