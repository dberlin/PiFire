import type { HopperView } from "@pifire/core/dashboard/deriveView";
import type { CSSProperties } from "react";
import { Link } from "react-router";

// Vertical pellet-hopper level with a subtle pellet-texture overlay; color and
// caption escalate as it drains (green -> amber -> red / REFILL PELLETS).
//
// There is deliberately no "Refresh Status" button here, though Flask has one
// (_macro_dash_default.html:359). The control loop re-reads the hopper level
// every ~10s on its own and the socket pushes hopperLevel with every frame
// (socket_io.py:258), so this card is already live; a manual refresh would ask
// for what is on its way regardless. See distance/intervals.py.
//
// The card's other Flask control, the "Manager" link (_macro_dash_default.html:360),
// IS kept: ruled 2026-07-26 that it exists because it exists in Bootstrap. In
// Flask it is the ONLY way to reach the pellet manager (base.html has no navbar
// entry for it); React also carries a navbar entry, so here it is a shortcut
// from the card that raises the question rather than the sole door.
//
// A router <Link>, not an <a href>: a bare href reloads the whole SPA and drops
// the live socket, which is what the earlier "no link at all" decision was
// really guarding against, back when /pellets was still a Flask page.
export function HopperGauge({ h }: { h: HopperView }) {
  // Per-frame data from deriveView, handed to the stylesheet as custom
  // properties. The layout is in dashboard.css; only these values are inline.
  const vars = {
    "--pf-hopper-pct": `${h.pct}%`,
    "--pf-hopper-color": h.color,
    "--pf-hopper-label-color": h.labelColor,
  } as CSSProperties;

  return (
    <div data-pf="hopper" className="pf-dash-card pf-dash-hopper" style={vars}>
      <div className="pf-dash-hopper-head">
        <span className="pf-dash-caption">Hopper</span>
        <span className="pf-dash-hopper-val">{h.pct}%</span>
      </div>
      <div className="pf-dash-hopper-track">
        <div className="pf-dash-hopper-fill" />
        <div className="pf-dash-hopper-tex" />
      </div>
      <div className="pf-dash-hopper-foot">
        <span className="pf-dash-hopper-label">{h.label}</span>
        <Link className="pf-dash-hopper-link" to="/pellets">
          Manager
        </Link>
      </div>
    </div>
  );
}
