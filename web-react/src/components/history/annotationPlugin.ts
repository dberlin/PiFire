import type { HistoryAnnotation } from "@pifire/core/contracts/content";
import type uPlot from "uplot";

/**
 * Draw mode-change markers as vertical rules with a caption.
 *
 * The Flask charts get this from chartjs-plugin-annotation (cookfile.js).
 * uPlot has no annotation concept, so this is a `draw` hook: uPlot calls it
 * after the series are painted, and valToPos converts a data-space x into a
 * canvas x for whatever zoom is current -- which is what makes the markers
 * track a drag-zoom for free, with no bookkeeping here.
 *
 * `xMin` must already be epoch SECONDS (see cookfileAdapter.toChartAnnotations).
 *
 * Lives beside the chart it extends rather than under components/cookfiles/,
 * because it is a HistoryChart capability: /history receives annotations from
 * its endpoint too and has simply never drawn them.
 */
export function annotationPlugin(annotations: HistoryAnnotation[]): uPlot.Plugin {
  return {
    hooks: {
      draw: (u: uPlot) => {
        const ctx = u.ctx;
        ctx.save();
        //  Clip to the plotting area so a marker just outside the current zoom
        //  window cannot paint over the axes.
        ctx.beginPath();
        ctx.rect(u.bbox.left, u.bbox.top, u.bbox.width, u.bbox.height);
        ctx.clip();
        for (const a of annotations) {
          const x = u.valToPos(a.xMin, "x", true);
          if (x < u.bbox.left || x > u.bbox.left + u.bbox.width) continue;
          ctx.strokeStyle = a.borderColor;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(x, u.bbox.top);
          ctx.lineTo(x, u.bbox.top + u.bbox.height);
          ctx.stroke();
          const caption = a.label?.content;
          if (caption) {
            ctx.save();
            ctx.translate(x + 4, u.bbox.top + 4);
            ctx.fillStyle = a.borderColor;
            ctx.font = "11px sans-serif";
            ctx.textBaseline = "top";
            ctx.fillText(caption, 0, 0);
            ctx.restore();
          }
        }
        ctx.restore();
      },
    },
  };
}
