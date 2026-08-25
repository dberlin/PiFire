import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import type { HistoryChartData } from "@pifire/core/contracts/content";
import { hasPlottableHistory, toChartInput } from "@pifire/core/history/historyAdapter";
import { HistoryChart, type HistorySeriesInput } from "../../src/components/HistoryChart";
import { THEME } from "../../src/theme";
import { useLiveContext, usePrefsContext } from "../_layout";

// NOT Flask's default: web-react/src/helpers/settings/settingsDefaults.gen.ts
// puts history_page.minutes at 15. This screen has no settings UI to read
// that value from and no per-user override to persist one, so it picks its
// own fixed window instead -- two hours, because a full recent cook tends to
// run far longer, but the last couple of hours is what a user checking this
// screen from beside the grill usually wants.
const DEFAULT_MINUTES = 120;

// toChartInput's ChartInput is `{ times: number[], series: { values:
// (number|null)[] }[] }` -- parallel arrays, matching how uPlot (and the
// backend's prepare_chartdata) shape a series. HistoryChart's own contract is
// `{ points: [time, value][] }[]` -- point tuples, easier to build an SVG
// path from directly. This zips the two back together; it is the one piece
// of shaping specific to the mobile chart, so it stays here rather than in
// the shared adapter.
function toPointSeries(data: HistoryChartData): HistorySeriesInput[] {
  const { times, series } = toChartInput(data);
  return series
    // Temperatures only. This chart has ONE y-axis, and duty is a 0-100%
    // control signal -- plotted against degrees it would sit as a flat line on
    // the floor of a 225-degree scale. Filtered explicitly rather than left to
    // chance so a duty series added server-side can never silently appear here
    // mis-scaled; giving this chart a second axis is what would lift it.
    .filter((s) => s.axis === "temp")
    // `visible: false` is the shared adapter's way of saying "off, but
    // reachable from the chart's controls". This chart has no controls, so
    // off means not drawn.
    .filter((s) => s.visible)
    .map((s) => ({
    label: s.label,
    color: s.color,
    points: times.map((t, i) => [t, s.values[i] ?? null] as [number, number | null]),
  }));
}

export default function History() {
  const { host } = useLiveContext();
  const { prefs } = usePrefsContext();
  const tokens = THEME[prefs.accent];
  const [data, setData] = useState<HistoryChartData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(
    async (opts: { showSpinner: boolean }) => {
      if (opts.showSpinner) setRefreshing(true);
      try {
        const res = await fetch(`${host}/api/history/chart?minutes=${DEFAULT_MINUTES}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = (await res.json()) as HistoryChartData;
        setData(json);
        setError(null);
      } catch {
        // A grill that answers the live socket but not this REST endpoint
        // (e.g. mid-restart) should say so rather than show a stale chart
        // silently -- same "explicit over blank" rule the empty state
        // follows for a genuinely empty history.
        setError("Could not load history from the grill.");
      } finally {
        if (opts.showSpinner) setRefreshing(false);
      }
    },
    [host],
  );

  useEffect(() => {
    load({ showSpinner: false });
  }, [load]);

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: tokens.background }]}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => load({ showSpinner: true })}
          tintColor={tokens.accent}
        />
      }
    >
      <Text style={[styles.title, { color: tokens.text }]}>History</Text>

      {data === null && error === null ? (
        <ActivityIndicator color={tokens.accent} style={styles.spinner} />
      ) : null}

      {error !== null ? <Text style={[styles.error, { color: tokens.danger }]}>{error}</Text> : null}

      {data !== null && hasPlottableHistory(data) ? (
        <HistoryChart series={toPointSeries(data)} />
      ) : null}

      {data !== null && !hasPlottableHistory(data) ? (
        <HistoryChart series={[]} />
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingVertical: 24,
    paddingHorizontal: 16,
    gap: 16,
  },
  title: {
    fontSize: 20,
    fontWeight: "700",
  },
  spinner: {
    marginTop: 32,
  },
  error: {
    fontSize: 14,
  },
});
