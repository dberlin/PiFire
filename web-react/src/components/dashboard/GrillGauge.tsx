import { arcLength, describeArc, polarToCartesian, valueAngle } from "../../helpers/gaugeMath";

interface GrillGaugeProps {
  temp: number;
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
  setpoint,
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
    <div
      style={{
        background: "#2c231a",
        border: "1px solid rgba(255,255,255,0.13)",
        borderRadius: 22,
        flex: 1,
        position: "relative",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          width: 360,
          height: 360,
          borderRadius: "50%",
          background: "radial-gradient(closest-side, var(--accent), transparent 68%)",
          opacity: 0.3,
          filter: "blur(6px)",
          animation: glowAnim,
        }}
      />
      <svg width={392} height={392} viewBox="0 0 220 220" style={{ position: "relative" }}>
        <defs>
          <linearGradient id="pfGauge" x1="0" y1="1" x2="1" y2="0">
            <stop offset="0" stopColor="var(--accent-2)" />
            <stop offset="0.55" stopColor="var(--accent)" />
            <stop offset="1" stopColor="var(--accent-1)" />
          </linearGradient>
        </defs>
        <path d={track} fill="none" stroke="#4a4034" strokeWidth={16} strokeLinecap="round" />
        <path
          d={track}
          fill="none"
          stroke="url(#pfGauge)"
          strokeWidth={16}
          strokeLinecap="round"
          style={{
            strokeDasharray: len,
            strokeDashoffset: offset,
            transition: "stroke-dashoffset .9s ease",
            filter: "drop-shadow(0 0 7px var(--glow))",
          }}
        />
        {hasSetpoint && (
          <line
            x1={inner.x}
            y1={inner.y}
            x2={outer.x}
            y2={outer.y}
            stroke="#6cc8ff"
            strokeWidth={4}
            strokeLinecap="round"
          />
        )}
      </svg>
      <div
        style={{
          position: "absolute",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 2,
        }}
      >
        <div
          style={{
            font: "600 14px 'Barlow'",
            letterSpacing: 4,
            color: "#7d7264",
            textTransform: "uppercase",
          }}
        >
          Grill
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            fontFamily: "'Barlow Semi Condensed'",
            fontVariantNumeric: "tabular-nums",
            lineHeight: 0.9,
          }}
        >
          <span style={{ fontSize: 112, fontWeight: 800, color: "#f8f2e8" }}>
            {Math.round(temp)}
          </span>
          <span style={{ fontSize: 40, fontWeight: 600, color: "#8a7f70", marginLeft: 4 }}>
            °{units}
          </span>
        </div>
        {hasSetpoint && (
          <div
            style={{
              font: "600 20px 'Barlow'",
              letterSpacing: 1,
              color: "#6cc8ff",
              marginTop: 2,
              whiteSpace: "nowrap",
            }}
          >
            SET {Math.round(setpoint)}°
          </div>
        )}
        <div
          style={{
            marginTop: 12,
            padding: "6px 20px",
            borderRadius: 999,
            background: "color-mix(in srgb, var(--accent) 14%, transparent)",
            border: "1.5px solid color-mix(in srgb, var(--accent) 55%, transparent)",
            font: "700 17px 'Barlow'",
            letterSpacing: 3,
            color: "var(--accent)",
            textTransform: "uppercase",
            whiteSpace: "nowrap",
          }}
        >
          {modeLabel}
        </div>
      </div>
    </div>
  );
}
