import type { ProbeHealthView } from "@pifire/core/dashboard/probeHealth";
import { arcLength, describeArc, polarToCartesian, valueAngle } from "@pifire/core/gaugeMath";

interface GrillGaugeProps {
  /** Already rounded by deriveView, and already the last real reading when the
   *  probe has no current one. Null only when it has produced nothing at all. */
  temp: number | null;
  /** Set when `temp` is a carried-over reading, e.g. "last data 47s ago". */
  stale: string | null;
  /** Authoritative probe health; quiet states render no dashboard pill. */
  health?: ProbeHealthView | null;
  /** Retained socket state is useful but must never read as current. */
  healthLastReported?: boolean;
  setpoint: number;
  maxTemp: number;
  frac: number;
  hasSetpoint: boolean;
  modeLabel: string;
  units: "F" | "C";
  cooking: boolean;
  animate: boolean;
}

// Center piece: 270° ember arc (reusing the POC gauge geometry in gaugeMath),
// a setpoint tick, an animated glow, and the big grill temperature + mode badge
// overlay — the heart of the design's dashboard.
export function GrillGauge({
  temp,
  stale,
  setpoint,
  health = null,
  healthLastReported = false,
  maxTemp,
  frac,
  hasSetpoint,
  modeLabel,
  units,
  cooking,
  animate,
}: GrillGaugeProps) {
  const CX = 110;
  const CY = 110;
  const R = 90;
  const track = describeArc(CX, CY, R, -135, 135);
  const len = arcLength(R);
  const offset = len * (1 - frac);
  const spAngle = valueAngle(setpoint, maxTemp);
  const inner = polarToCartesian(CX, CY, R - 13, spAngle);
  const outer = polarToCartesian(CX, CY, R + 9, spAngle);
  const glowAnim = cooking && animate ? "pf-glow 3.2s ease-in-out infinite" : "none";

  return (
    <div data-pf="gauge" className="pf-dash-card pf-dash-gauge">
      <div className="pf-dash-gauge-glow" style={{ animation: glowAnim }} />
      {/* No width/height attributes: the size is in dashboard.css so a
          breakpoint can reach it. viewBox keeps the drawing coordinates. */}
      <svg className="pf-dash-gauge-svg" viewBox="0 0 220 220">
        <defs>
          <linearGradient id="pfGauge" x1="0" y1="1" x2="1" y2="0">
            {/* Theme.arcStop0 / arcStop1 / arcStop2. The middle stop is its own
                Qt token, not accentColor: they coincide only for Ember. */}
            <stop offset="0" stopColor="var(--accent-2)" />
            <stop offset="0.55" stopColor="var(--accent-mid)" />
            <stop offset="1" stopColor="var(--accent-1)" />
          </linearGradient>
        </defs>
        <path d={track} fill="none" stroke="var(--track)" strokeWidth={16} strokeLinecap="round" />
        <path
          d={track}
          fill="none"
          stroke="url(#pfGauge)"
          strokeWidth={16}
          strokeLinecap="round"
          className="pf-dash-gauge-arc"
          style={{ strokeDasharray: len, strokeDashoffset: offset }}
        />
        {hasSetpoint && (
          <line
            x1={inner.x}
            y1={inner.y}
            x2={outer.x}
            y2={outer.y}
            stroke="var(--setpoint)"
            strokeWidth={4}
            strokeLinecap="round"
          />
        )}
      </svg>
      <div className="pf-dash-gauge-overlay">
        <div className="pf-dash-gauge-caption">Grill</div>
        <div className="pf-dash-gauge-num">
          <span className="pf-dash-gauge-temp">{temp === null ? "—" : temp}</span>
          <span className="pf-dash-gauge-unit">°{units}</span>
        </div>
        {stale && <div className="pf-dash-gauge-stale">{stale}</div>}
        {health !== null && health.severity !== "quiet" ? (
          <div
            className={`pf-dash-gauge-health pf-dash-gauge-health--${health.severity}`}
            role="status"
            aria-label="Control probe health"
          >
            <strong
              className={`pf-badge pf-badge-${
                health.severity === "warning"
                  ? "warn"
                  : health.severity === "danger"
                    ? "danger"
                    : "unknown"
              }`}
            >
              {healthLastReported || health.freshnessQualifier !== null ? "Last reported: " : null}
              {health.headline}
            </strong>
            {health.impactCopy !== null ? <span>{health.impactCopy}</span> : null}
            {health.causeCopy !== null ? <span>{health.causeCopy}</span> : null}
          </div>
        ) : null}
        {hasSetpoint && <div className="pf-dash-gauge-set">SET {Math.round(setpoint)}°</div>}
        <div className="pf-dash-gauge-mode">{modeLabel}</div>
      </div>
    </div>
  );
}
