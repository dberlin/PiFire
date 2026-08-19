import { StyleSheet, Text, View } from "react-native";
import Svg, { Path } from "react-native-svg";
import { THEME } from "../theme";

const tokens = THEME.ember;

/** `[epoch SECONDS, value]`. `value` is `null` for a gap (a probe with no
 *  reading at that timestamp) -- @pifire/core/history/historyAdapter pads
 *  missing samples with null rather than dropping them, so a series stays
 *  aligned with the others; the line breaks there instead of interpolating
 *  across the gap or crashing on it. */
export type HistoryPoint = [number, number | null];

export interface HistorySeriesInput {
  label: string;
  points: HistoryPoint[];
  /** Falls back to a palette entry (cycled by series index) when omitted, the
   *  same fallback @pifire/core/history/historyAdapter applies for the web
   *  chart when a dataset has no `borderColor`. */
  color?: string;
}

interface HistoryChartProps {
  series: HistorySeriesInput[];
  /** Logical (viewBox) height in SVG units; width always fills the
   *  container. Pan/zoom are out of scope for v1 -- this is a fixed,
   *  readable view of the whole requested range, nothing more. */
  height?: number;
}

// Cycled by series index when a series carries no explicit color. Matches
// the accent tokens available across mobile/src/theme.ts's three accents
// plus a couple of readable extras, so a multi-probe cook (grill + several
// food probes) still gets a distinct stroke per line.
const PALETTE = ["#ff8a2b", "#3cc7d0", "#ff6a5a", "#5ec96f", "#ffb020"];

const VIEW_WIDTH = 320;
const PADDING = 10;

function colorFor(index: number, explicit?: string): string {
  return explicit ?? PALETTE[index % PALETTE.length];
}

// HH:MM in the device's local time, deliberately not Intl.DateTimeFormat --
// this renders inside Jest's plain Node environment too (this component's
// own test suite), and a hand-rolled 24h clock needs no ICU data to do that.
function formatTime(seconds: number): string {
  const d = new Date(seconds * 1000);
  const hh = d.getHours().toString().padStart(2, "0");
  const mm = d.getMinutes().toString().padStart(2, "0");
  return `${hh}:${mm}`;
}

function formatTemp(value: number): string {
  return `${Math.round(value)}°`;
}

/** Builds one SVG path `d` string for a series, breaking (rather than
 *  interpolating across, or crashing on) any `null` reading. */
function buildPath(
  points: HistoryPoint[],
  scaleX: (x: number) => number,
  scaleY: (y: number) => number,
): string {
  const segments: string[] = [];
  let penDown = false;
  for (const [x, y] of points) {
    if (y === null || Number.isNaN(y)) {
      penDown = false;
      continue;
    }
    const px = scaleX(x);
    const py = scaleY(y);
    segments.push(`${penDown ? "L" : "M"} ${px.toFixed(2)} ${py.toFixed(2)}`);
    penDown = true;
  }
  return segments.join(" ");
}

// A readable, static chart: one stroked path per series, a legend, and
// min/max axis labels for time and temperature. Pan and zoom are explicitly
// out of scope for v1 (see task-14-brief.md) -- this is not a uPlot port,
// since uPlot is DOM-only; it is a from-scratch react-native-svg rendering
// of the same shaped data @pifire/core/history/historyAdapter produces for
// the web chart.
export function HistoryChart({ series, height = 160 }: HistoryChartProps) {
  const hasAnyPoints = series.some((s) => s.points.length > 0);

  if (!hasAnyPoints) {
    return (
      <View style={styles.empty}>
        <Text style={styles.emptyText}>No history to plot yet.</Text>
      </View>
    );
  }

  const allX: number[] = [];
  const allY: number[] = [];
  for (const s of series) {
    for (const [x, y] of s.points) {
      allX.push(x);
      if (y !== null && !Number.isNaN(y)) allY.push(y);
    }
  }

  // Degenerate domains -- a single sample, or every sample landing on the
  // same timestamp/value -- would otherwise divide by zero below and stamp
  // every coordinate NaN (a path react-native-svg silently drops, which
  // looks identical to the empty state but for the wrong reason). Widening
  // a zero-width domain by 1 on each side keeps the scale finite and centers
  // the lone value/timestamp in the plot instead.
  let xMin = Math.min(...allX);
  let xMax = Math.max(...allX);
  if (xMin === xMax) {
    xMin -= 1;
    xMax += 1;
  }

  let yMin = allY.length > 0 ? Math.min(...allY) : 0;
  let yMax = allY.length > 0 ? Math.max(...allY) : 1;
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }

  const plotWidth = VIEW_WIDTH - PADDING * 2;
  const plotHeight = height - PADDING * 2;
  const scaleX = (x: number) => PADDING + ((x - xMin) / (xMax - xMin)) * plotWidth;
  // SVG y grows downward; a higher reading should draw higher on screen.
  const scaleY = (y: number) => PADDING + (1 - (y - yMin) / (yMax - yMin)) * plotHeight;

  return (
    <View style={styles.container}>
      <View style={styles.legend}>
        {series.map((s, i) => (
          <View key={s.label} style={styles.legendItem}>
            <View style={[styles.swatch, { backgroundColor: colorFor(i, s.color) }]} />
            <Text style={styles.legendText}>{s.label}</Text>
          </View>
        ))}
      </View>

      <View style={styles.chartRow}>
        <View style={styles.yAxis}>
          <Text style={styles.axisText}>{formatTemp(yMax)}</Text>
          <Text style={styles.axisText}>{formatTemp(yMin)}</Text>
        </View>
        <Svg width="100%" height={height} viewBox={`0 0 ${VIEW_WIDTH} ${height}`}>
          {series.map((s, i) => (
            <Path
              key={s.label}
              testID="history-line"
              d={buildPath(s.points, scaleX, scaleY)}
              stroke={colorFor(i, s.color)}
              strokeWidth={2}
              fill="none"
            />
          ))}
        </Svg>
      </View>

      <View style={styles.xAxis}>
        <Text style={styles.axisText}>{formatTime(xMin)}</Text>
        <Text style={styles.axisText}>{formatTime(xMax)}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 8,
  },
  empty: {
    paddingVertical: 32,
    alignItems: "center",
    justifyContent: "center",
  },
  emptyText: {
    color: tokens.text,
    opacity: 0.7,
    fontSize: 14,
  },
  legend: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
  },
  legendItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  swatch: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  legendText: {
    color: tokens.text,
    fontSize: 12,
  },
  chartRow: {
    flexDirection: "row",
    alignItems: "stretch",
  },
  yAxis: {
    justifyContent: "space-between",
    paddingRight: 6,
    paddingVertical: PADDING,
  },
  xAxis: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  axisText: {
    color: tokens.text,
    opacity: 0.6,
    fontSize: 10,
  },
});
