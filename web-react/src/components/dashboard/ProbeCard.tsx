import type { ProbeCardView } from "@pifire/core/dashboard/deriveView";
import type { CSSProperties } from "react";
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
  healthLastReported = false,
}: {
  p: ProbeCardView;
  onOpenNotify(label: string): void;
  healthLastReported?: boolean;
}) {
  // Per-frame data from deriveView; the card's boxes are in dashboard.css.
  const vars = {
    "--pf-bar-pct": `${p.barPct}%`,
    "--pf-bar-color": p.barColor,
  } as CSSProperties;

  return (
    <div data-pf="probeCard" className="pf-dash-card pf-dash-probecard" style={vars}>
      <div className="pf-dash-probehead">
        <span className="pf-dash-probename">{p.name}</span>
        <span className="pf-dash-probemeta">
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
          <span className="pf-dash-probetarget" style={{ color: p.tgtColor }}>
            {p.targetStr}
          </span>
          <NotifyBell
            probeName={p.name}
            on={p.notifyOn}
            // The LABEL, not the title: the write is addressed by label and the
            // title is free text the user can rename.
            onClick={() => onOpenNotify(p.label)}
          />
        </span>
      </div>
      <div className="pf-dash-probetemp">
        <span className="pf-dash-probetemp-int">{p.tempInt === null ? "—" : p.tempInt}</span>
        <span className="pf-dash-probetemp-unit">°{p.unit}</span>
      </div>
      {/* The number above is the probe's last real reading, not a current one.
          Without this line it reads as live, which is the whole defect: a
          temperature is plausible at any value, so absence has to be said. */}
      {p.stale && <div className="pf-dash-probestale">{p.stale}</div>}
      {p.health !== null && p.health.severity !== "quiet" ? (
        <div
          className={`pf-dash-probehealth pf-dash-probehealth--${p.health.severity}`}
          aria-label={`Probe health for ${p.name}`}
        >
          <strong
            className={`pf-badge pf-badge-${
              p.health.severity === "warning"
                ? "warn"
                : p.health.severity === "danger"
                  ? "danger"
                  : "unknown"
            }`}
          >
            {healthLastReported || p.health.freshnessQualifier !== null ? "Last reported: " : null}
            {p.health.headline}
          </strong>
          {p.health.impactCopy !== null ? <span>{p.health.impactCopy}</span> : null}
          {p.health.causeCopy !== null ? <span>{p.health.causeCopy}</span> : null}
        </div>
      ) : null}
      <div className="pf-dash-bar">
        <div className="pf-dash-bar-fill" />
      </div>
      {/* Estimated time to target. Shown only while the notification is armed
          and the backend has produced a number, exactly like the Flask ETA
          button (_macro_dash_default.html:123-131). */}
      {p.etaStr !== null && <div className="pf-notify-eta">ETA {p.etaStr}</div>}
    </div>
  );
}
