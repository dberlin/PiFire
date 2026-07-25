import type { HopperView } from "../../helpers/dashboard/deriveView";

// Vertical pellet-hopper level with a subtle pellet-texture overlay; color and
// caption escalate as it drains (green -> amber -> red / REFILL PELLETS).
export function HopperGauge({ h }: { h: HopperView }) {
  return (
    <div
      data-pf="hopper"
      style={{
        flex: 1,
        background: "#2c231a",
        border: "1px solid rgba(255,255,255,0.13)",
        borderRadius: 18,
        padding: 16,
        display: "flex",
        flexDirection: "column",
        gap: 12,
        minHeight: 0,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span
          style={{
            font: "600 13px 'Barlow'",
            letterSpacing: 2.5,
            color: "#7d7264",
            textTransform: "uppercase",
          }}
        >
          Hopper
        </span>
        <span
          style={{
            font: "800 34px 'Barlow Semi Condensed'",
            fontVariantNumeric: "tabular-nums",
            color: h.color,
            lineHeight: 1,
          }}
        >
          {h.pct}%
        </span>
      </div>
      <div
        style={{
          flex: 1,
          minHeight: 0,
          position: "relative",
          borderRadius: 14,
          background: "rgba(255,255,255,0.09)",
          overflow: "hidden",
          border: "1px solid rgba(255,255,255,0.12)",
        }}
      >
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 0,
            height: `${h.pct}%`,
            background: `linear-gradient(180deg, ${h.color2}, ${h.color})`,
            transition: "height .9s ease",
          }}
        />
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 0,
            height: `${h.pct}%`,
            transition: "height .9s ease",
            backgroundImage:
              "radial-gradient(circle at 3px 3px, rgba(0,0,0,0.16) 1.6px, transparent 1.8px)",
            backgroundSize: "11px 11px",
            opacity: 0.55,
          }}
        />
      </div>
      <div
        style={{
          font: "600 12px 'Barlow'",
          letterSpacing: 2,
          color: h.labelColor,
          textTransform: "uppercase",
        }}
      >
        {h.label}
      </div>
    </div>
  );
}
