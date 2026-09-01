import type { HistoryAnnotation } from "@pifire/core/contracts/content";
import type { ChartSeries } from "@pifire/core/history/historyAdapter";

import "uplot/dist/uPlot.min.css";
import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";

import { annotationPlugin } from "./annotationPlugin";

import "./historyChart.css";
import { shouldResetScales } from "./scaleReset";
import { type SeriesShape, tooltipPlugin } from "./tooltipPlugin";

export type { ChartSeries };

export interface HistoryChartProps {
  /** Epoch SECONDS -- uPlot's x-axis convention. */
  times: number[];
  series: ChartSeries[];
  height?: number;
  /** Mode-change markers, x in epoch SECONDS. Omitted on /history, which
   *  receives annotations from its endpoint but has never drawn them. */
  annotations?: HistoryAnnotation[];
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
  // `visible` is deliberately NOT part of the shape. Toggling a series must
  // not rebuild the plot -- a rebuild drops whatever the user zoomed to -- so
  // visibility is pushed onto the live instance with setSeries instead. See
  // the effect below.
  const key = JSON.stringify(series.map((s) => ({ label: s.label, color: s.color, axis: s.axis })));
  return useMemo(() => JSON.parse(key) as SeriesShape[], [key]);
}

/**
 * Same stabilisation for annotations, and it is load-bearing rather than an
 * optimisation: uPlot reads `plugins` ONLY when the plot is constructed, so a
 * change of annotations has to join the rebuild condition below. Without it,
 * toggling the markers off would leave them painted until some unrelated shape
 * change happened to rebuild the plot.
 */
function useStableAnnotations(
  annotations: HistoryAnnotation[] | undefined,
): HistoryAnnotation[] | null {
  const key = annotations === undefined ? "" : JSON.stringify(annotations);
  return useMemo(() => (key === "" ? null : (JSON.parse(key) as HistoryAnnotation[])), [key]);
}

export function HistoryChart({ times, series, height = 360, annotations }: HistoryChartProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const seriesShape = useStableSeriesShape(series);
  const stableAnnotations = useStableAnnotations(annotations);

  // Tracks the shape/height a plot was last built with, and the height the
  // resize handler below should apply on the next resize event. Written
  // from inside the data effect (writes during render trip
  // react-hooks/refs; writes inside effects are fine).
  const builtShapeRef = useRef<SeriesShape[] | null>(null);
  const builtHeightRef = useRef<number | null>(null);
  const builtAnnotationsRef = useRef<HistoryAnnotation[] | null | undefined>(undefined);
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
      !existing ||
      builtShapeRef.current !== seriesShape ||
      builtHeightRef.current !== height ||
      builtAnnotationsRef.current !== stableAnnotations;

    if (rebuild) {
      existing?.destroy();

      const hasDuty = seriesShape.some((s) => s.axis === "duty");

      const opts: uPlot.Options = {
        width: host.clientWidth || 800,
        height,
        series: [
          {},
          ...seriesShape.map((s, i) => ({
            label: s.label,
            stroke: s.color,
            // Thinner and slightly faded: duty sits underneath the
            // temperatures as a control signal rather than competing with
            // them for the reader's attention.
            width: s.axis === "duty" ? 1.2 : 1.5,
            alpha: s.axis === "duty" ? 0.85 : 1,
            // Only duty gets a named scale. Temperatures stay on uPlot's
            // default "y", which is what `axes[1]` below addresses -- moving
            // them to a scale of their own leaves that axis with no series and
            // silently unlabelled.
            ...(s.axis === "duty" ? { scale: "duty" } : {}),
            // Duty is a step function -- a commanded ratio that holds until
            // the controller re-solves. Interpolating between samples draws
            // ramps that never happened, and the server relies on this: it
            // reduces duty by keeping its transitions, which is exact ONLY
            // under stepped rendering.
            ...(s.axis === "duty" ? { paths: uPlot.paths.stepped?.({ align: 1 }) } : {}),
            points: { show: false },
            show: series[i]?.visible ?? true,
          })),
        ],
        scales: {
          // Pinned rather than autoscaled, so a line's height means the same
          // thing in every window and every cook: half-way up is 50% duty,
          // full stop. An autoscaled duty axis would silently re-zoom itself
          // whenever the grill settled into a narrow band.
          ...(hasDuty ? { duty: { range: [0, 100] as [number, number], auto: false } } : {}),
        },
        axes: [
          { stroke: "#9aa3ad", grid: { stroke: "rgba(255,255,255,0.07)" } },
          { stroke: "#9aa3ad", grid: { stroke: "rgba(255,255,255,0.07)" } },
          // The duty axis draws on the right and paints NO gridlines: the
          // temperature axis already rules the plot, and a second set at
          // different intervals reads as a moiré rather than as information.
          ...(hasDuty
            ? [
                {
                  scale: "duty",
                  side: 1 as const,
                  stroke: "#9aa3ad",
                  grid: { show: false },
                  values: (_u: uPlot, splits: number[]) => splits.map((v) => `${v}%`),
                },
              ]
            : []),
        ],
        cursor: { drag: { x: true, y: false } }, // drag-to-zoom
        // uPlot's own legend is off: the chip row above the chart replaces it.
        // Leaving it on gives the same series TWO controls -- uPlot toggles
        // `show` directly on the instance when a legend entry is clicked, which
        // the chips know nothing about, so the two would disagree until some
        // unrelated change resynced them. Its live value readout is redundant
        // too; tooltipPlugin exists because that readout renders as a table
        // rather than at the cursor.
        legend: { show: false },
        plugins: [
          tooltipPlugin(seriesShape),
          //  Omitted entirely when the prop is absent, so /history builds the
          //  exact plugin list it always has.
          ...(stableAnnotations ? [annotationPlugin(stableAnnotations)] : []),
        ],
      };

      plotRef.current = new uPlot(opts, data, host);
      builtShapeRef.current = seriesShape;
      builtHeightRef.current = height;
      builtAnnotationsRef.current = stableAnnotations;
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
  }, [height, seriesShape, stableAnnotations, times, series]);

  // Visibility, pushed onto the LIVE instance.
  //
  // This is the whole reason `visible` is kept out of the series shape above.
  // Adding or removing entries in the `series` array changes the shape, which
  // forces the effect above to destroy and rebuild the plot -- and a fresh
  // uPlot autoscales, so the user loses whatever they had zoomed to. Every
  // series is therefore always constructed, and only `show` changes.
  //
  // `visibility` is a plain string rather than the array, so this effect
  // reruns when the toggles change and not on every data tick.
  const visibility = series.map((s) => (s.visible ? "1" : "0")).join("");
  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    for (let i = 0; i < visibility.length; i += 1) {
      const show = visibility[i] === "1";
      if (plot.series[i + 1] && plot.series[i + 1].show !== show) {
        plot.setSeries(i + 1, { show });
      }
    }
  }, [visibility]);

  return <div ref={hostRef} className="pf-history-chart" style={{ height }} />;
}
