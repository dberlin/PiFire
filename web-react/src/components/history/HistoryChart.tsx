import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import "./historyChart.css";
import { shouldResetScales } from "./scaleReset";
import { type SeriesShape, tooltipPlugin } from "./tooltipPlugin";

export interface ChartSeries {
  label: string;
  color: string;
  values: (number | null)[];
}

export interface HistoryChartProps {
  /** Epoch SECONDS -- uPlot's x-axis convention. */
  times: number[];
  series: ChartSeries[];
  height?: number;
}

/**
 * Returns a referentially-stable value: the same array reference is handed
 * back across renders as long as its JSON shape is unchanged, and only a new
 * reference is returned once the shape actually differs.
 *
 * `series` gets a new array identity on every data tick (new `values`), but
 * the plot only needs to be rebuilt when the *shape* (labels/colors/count)
 * changes. This lets the effect below list its real dependency
 * (`seriesShape`) and satisfy exhaustive-deps, without rebuilding uPlot --
 * and dropping the user's zoom -- on every tick.
 *
 * `key` is plain, ordinary render-time computation (reading props during
 * render is fine); only `useMemo`'s factory needs to avoid touching `series`
 * directly so the compiler doesn't ask for it as a dependency it can't
 * usefully react to. `JSON.parse(key)` re-derives the shape purely from the
 * (already deps-tracked) string, so `key` is the only thing referenced.
 */
function useStableSeriesShape(series: ChartSeries[]): SeriesShape[] {
  const key = JSON.stringify(series.map((s) => ({ label: s.label, color: s.color })));
  return useMemo(() => JSON.parse(key) as SeriesShape[], [key]);
}

export function HistoryChart({ times, series, height = 360 }: HistoryChartProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const seriesShape = useStableSeriesShape(series);

  // Tracks the shape/height a plot was last built with, and the height the
  // resize handler below should apply on the next resize event. Written
  // from inside the data effect (writes during render trip
  // react-hooks/refs; writes inside effects are fine).
  const builtShapeRef = useRef<SeriesShape[] | null>(null);
  const builtHeightRef = useRef<number | null>(null);
  const heightRef = useRef(height);

  // Lifecycle-only: attaches the resize listener once and destroys the plot
  // exactly once, on unmount. Has no reactive dependencies, and its
  // correctness doesn't depend on running before or after the data effect
  // below -- it only ever reads `plotRef.current` / `heightRef.current`
  // (refs, always current) at call time, so it behaves identically no
  // matter how many times that effect has rebuilt the plot in between.
  useEffect(() => {
    const host = hostRef.current;
    const onResize = () => {
      const plot = plotRef.current;
      if (plot && host) {
        plot.setSize({ width: host.clientWidth || 800, height: heightRef.current });
      }
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      plotRef.current?.destroy();
      plotRef.current = null;
    };
  }, []);

  // uPlot is an imperative canvas library: build the plot once per series
  // SHAPE+height, then feed subsequent data via setData -- rebuilding on
  // every tick would drop the user's zoom and thrash the canvas. This one
  // effect owns that whole decision: it tracks the shape/height it last
  // built in refs (updated here, inside the effect), and either rebuilds
  // the plot or calls setData.
  //
  // `times`/`series` are ordinary, always-fresh closure values here -- this
  // effect reruns on every data tick (it's in the dependency array below),
  // so there's no need to smuggle fresh data in via a ref written by a
  // sibling effect.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    heightRef.current = height;

    const data = [times, ...series.map((s) => s.values)] as uPlot.AlignedData;

    const existing = plotRef.current;
    const rebuild =
      !existing || builtShapeRef.current !== seriesShape || builtHeightRef.current !== height;

    if (rebuild) {
      existing?.destroy();

      const opts: uPlot.Options = {
        width: host.clientWidth || 800,
        height,
        series: [
          {},
          ...seriesShape.map((s) => ({
            label: s.label,
            stroke: s.color,
            width: 1.5,
            points: { show: false },
          })),
        ],
        axes: [
          { stroke: "#9aa3ad", grid: { stroke: "rgba(255,255,255,0.07)" } },
          { stroke: "#9aa3ad", grid: { stroke: "rgba(255,255,255,0.07)" } },
        ],
        cursor: { drag: { x: true, y: false } }, // drag-to-zoom
        legend: { live: true },
        plugins: [tooltipPlugin(seriesShape)],
      };

      plotRef.current = new uPlot(opts, data, host);
      builtShapeRef.current = seriesShape;
      builtHeightRef.current = height;
      return;
    }

    // Shape/height are unchanged: feed the new data into the existing plot
    // instead of rebuilding it. Whether that should reset the x-scale (and
    // so cancel a user's in-progress zoom) or preserve it depends on
    // whether the user has actually zoomed in -- see shouldResetScales.
    const [heldTimes] = existing.data;
    const dataMin = heldTimes.length ? (heldTimes[0] as number) : undefined;
    const dataMax = heldTimes.length ? (heldTimes[heldTimes.length - 1] as number) : undefined;
    const scaleX = existing.scales.x;
    const reset = shouldResetScales(scaleX?.min, scaleX?.max, dataMin, dataMax);
    existing.setData(data, reset);
    // Runs on every shape/height change (rebuild) AND on every data tick
    // (setData) -- see the branch above for which happens.
  }, [height, seriesShape, times, series]);

  return <div ref={hostRef} className="pf-history-chart" style={{ height }} />;
}
