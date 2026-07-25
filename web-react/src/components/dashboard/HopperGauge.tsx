import { type CSSProperties, useState } from "react";
import type { HopperView } from "../../helpers/dashboard/deriveView";

// Vertical pellet-hopper level with a subtle pellet-texture overlay; color and
// caption escalate as it drains (green -> amber -> red / REFILL PELLETS).
export function HopperGauge({
  h,
  onRefresh,
}: {
  h: HopperView;
  /** Asks the distance sensor to re-measure. Flask labels this "Refresh
   *  Status" (_macro_dash_default.html:358). */
  onRefresh(): Promise<unknown>;
}) {
  const [refreshing, setRefreshing] = useState(false);
  const refresh = async () => {
    setRefreshing(true);
    try {
      await onRefresh();
    } finally {
      setRefreshing(false);
    }
  };

  // Per-frame data from deriveView, handed to the stylesheet as custom
  // properties. The layout is in dashboard.css; only these values are inline.
  const vars = {
    "--pf-hopper-pct": `${h.pct}%`,
    "--pf-hopper-color": h.color,
    "--pf-hopper-color2": h.color2,
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
        {/* The reading is only as fresh as the last measurement, and the
            controller does not re-measure on demand otherwise. Sits in the
            card's existing caption row so the card's box does not change. */}
        <button className="pf-toggle" onClick={refresh} disabled={refreshing}>
          Refresh Status
        </button>
      </div>
    </div>
  );
}
