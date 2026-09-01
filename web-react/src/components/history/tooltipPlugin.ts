import type uPlot from "uplot";

import { computeTooltipPosition } from "./tooltipPosition";
import { createTooltipRow } from "./tooltipRow";

export interface SeriesShape {
  label: string;
  color: string;
  /** Selects the value's unit in the tooltip, and the scale it is drawn on. */
  axis: "temp" | "duty";
}

/**
 * Cursor-following tooltip, as a uPlot plugin (~30 lines).
 *
 * uPlot already ships a live hover readout (`legend.live` defaults to true),
 * but it renders as a legend table; this puts the values at the cursor, which
 * is what the legacy Chart.js page did.
 *
 * Takes `seriesShape` (labels/colors) as a parameter rather than reading them
 * back off the uPlot instance at cursor time, for two reasons:
 *  - uPlot normalizes every series' `stroke` option to a FUNCTION during
 *    init (`fnOrSelf` in uPlot.esm.js wraps any non-function stroke as
 *    `() => v`), so `u.series[i].stroke` is never a color -- reading it back
 *    out only ever recovers the wrapper function.
 *  - `s.label` is unescaped user input (a probe name); it must be rendered
 *    via `textContent`, not read into an HTML string, so it can never be
 *    interpreted as markup. See tooltipRow.ts for both.
 * HistoryChart rebuilds the plot (and therefore a fresh plugin instance)
 * whenever the series shape changes, so the shape passed in here is always
 * current for whatever plot it's attached to.
 *
 * Split into its own module (the same way tooltipFormat.ts/tooltipRow.ts
 * are) so it's directly unit-testable -- driving a stub uPlot-shaped object
 * through `hooks.init`/`hooks.setCursor` -- without co-exporting a
 * non-component from HistoryChart.tsx, which trips
 * react-refresh/only-export-components.
 */
export function tooltipPlugin(seriesShape: SeriesShape[]): uPlot.Plugin {
  let el: HTMLDivElement | null = null;
  return {
    hooks: {
      init: (u: uPlot) => {
        el = document.createElement("div");
        el.className = "pf-history-tip";
        el.style.display = "none";
        u.over.appendChild(el);
      },
      setCursor: (u: uPlot) => {
        if (!el) return;
        const { idx, left, top } = u.cursor;
        if (idx == null || left == null || left < 0) {
          el.style.display = "none";
          return;
        }
        const when = new Date((u.data[0][idx] as number) * 1000);
        const header = document.createElement("div");
        header.className = "t";
        header.textContent = when.toLocaleTimeString();
        // Only series that are actually drawn. Visibility is toggled on the
        // live uPlot instance (never by rebuilding the series array, which
        // would drop the user's zoom), so `show` is the authority here --
        // seriesShape lists every series the plot was built with, including
        // the ones currently switched off.
        const rows = seriesShape.flatMap((s, i) => {
          if (u.series?.[i + 1]?.show === false) return [];
          const v = u.data[i + 1][idx] as number | null;
          return [createTooltipRow(s.label, s.color, v, s.axis)];
        });
        el.replaceChildren(header, ...rows);
        el.style.display = "block";

        // Flip near the right/bottom edges so the tooltip stays on screen --
        // see computeTooltipPosition for the (unit-tested) math.
        const tipWidth = el.offsetWidth || 150;
        const tipHeight = el.offsetHeight || 0;
        const pos = computeTooltipPosition(
          left,
          top ?? 0,
          tipWidth,
          tipHeight,
          u.over.clientWidth,
          u.over.clientHeight,
        );
        el.style.left = `${pos.left}px`;
        el.style.top = `${pos.top}px`;
      },
      destroy: () => {
        el?.remove();
        el = null;
      },
    },
  };
}
