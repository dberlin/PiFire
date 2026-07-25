import type { ProbeCardView } from "../../helpers/dashboard/deriveView";
import { NotifyBell } from "./NotifyBell";

/** Flask's four battery icons, as text: empty / half / three-quarters / full. */
const BATTERY_GLYPH = ["\u25AB", "\u25E7", "\u25E8", "\u25AA"] as const;

// A single food-probe card: name, target, big current temp, and a progress bar
// toward target (green when within 1° of done). The bell opens the per-probe
// target-notification modal, mirroring the Flask probe card
// (_macro_dash_default.html:108-134), which carries the bell and the ETA
// readout in the same place.
export function ProbeCard({
  p,
  onOpenNotify,
}: {
  p: ProbeCardView;
  onOpenNotify(label: string): void;
}) {
  return (
    <div
      data-pf="probeCard"
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
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {/* Bluetooth-only badges. Both are null for a wired ADC probe, and
              they render NOTHING at all then -- not an empty span, which would
              still take gap space and move the card. */}
          {p.conn !== null && (
            <span className={`pf-badge pf-badge-${p.conn.tone}`} title={p.conn.label}>
              {p.conn.tone === "ok" ? "\u21c4" : "\u2298"}
            </span>
          )}
          {p.battery !== null && (
            <span className={`pf-badge pf-badge-${p.battery.tone}`} title={p.battery.text}>
              {BATTERY_GLYPH[p.battery.level]}
            </span>
          )}
          <span style={{ font: "600 15px 'Barlow'", color: p.tgtColor }}>{p.targetStr}</span>
          <NotifyBell
            probeName={p.name}
            on={p.notifyOn}
            // The LABEL, not the title: the write is addressed by label and the
            // title is free text the user can rename.
            onClick={() => onOpenNotify(p.label)}
          />
        </span>
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
      {/* Estimated time to target. Shown only while the notification is armed
          and the backend has produced a number, exactly like the Flask ETA
          button (_macro_dash_default.html:123-131). */}
      {p.etaStr !== null && <div className="pf-notify-eta">ETA {p.etaStr}</div>}
    </div>
  );
}
