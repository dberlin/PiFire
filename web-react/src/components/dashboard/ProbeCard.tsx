import type { ProbeCardView } from "../../helpers/dashboard/deriveView";

// A single food-probe card: name, target, big current temp, and a progress bar
// toward target (green when within 1° of done).
export function ProbeCard({ p }: { p: ProbeCardView }) {
  return (
    <div
      style={{
        background: "#2c231a",
        border: "1px solid rgba(255,255,255,0.13)",
        borderRadius: 18,
        padding: "15px 18px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        position: "relative",
        overflow: "hidden",
        flex: 1,
        justifyContent: "center",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span
          style={{
            font: "600 15px 'Barlow'",
            letterSpacing: 1.5,
            color: "#b7ac9c",
            textTransform: "uppercase",
          }}
        >
          {p.name}
        </span>
        <span style={{ font: "600 15px 'Barlow'", color: p.tgtColor }}>{p.targetStr}</span>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          fontFamily: "'Barlow Semi Condensed'",
          fontVariantNumeric: "tabular-nums",
          lineHeight: 0.92,
        }}
      >
        <span style={{ fontSize: 66, fontWeight: 800, color: "#f4ede2" }}>{p.tempInt}</span>
        <span style={{ fontSize: 26, fontWeight: 600, color: "#8a7f70", marginLeft: 2 }}>
          °{p.unit}
        </span>
      </div>
      <div
        style={{
          height: 6,
          borderRadius: 6,
          background: "rgba(255,255,255,0.11)",
          marginTop: 8,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "100%",
            borderRadius: 6,
            width: `${p.barPct}%`,
            background: p.barColor,
            transition: "width .9s ease",
          }}
        />
      </div>
    </div>
  );
}
