import { hasPlottableHistory, toChartInput } from "@pifire/core/history/historyAdapter";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { fetchHistoryChart } from "../../helpers/history/historyApi";
import { queryKeys } from "../../helpers/query/keys";
import { useSettings } from "../../helpers/settings/useSettings";
import { CookFileList } from "../cookfiles/CookFileList";
import { NumberField } from "../settings/fields/NumberField";
import { HistoryChart } from "./HistoryChart";
import { SeriesToggles } from "./SeriesToggles";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

// Shown in the Minutes control until the first response lands: the real
// default is whatever the user saved in Settings > History, which the server
// applies when the request carries no `minutes` (see fetchHistoryChart) and
// echoes back as `minutes`.
const PLACEHOLDER_MINUTES = 60;

// How long to wait between auto-refresh polls (Settings > History > Auto
// Refresh). The history store gains a sample roughly every 3s (the API route's
// SAMPLES_PER_MINUTE = 20), and every poll makes the server re-read AND
// re-downsample the whole window, so polling at the sample cadence would spend
// a lot of work per new point. 5s is the shortest interval that still picks up
// at least one fresh sample on every tick while holding the page to 12
// requests a minute. react-query dedupes a refetchInterval tick against a
// request that hasn't settled yet, so a response slower than this can never
// queue polls up behind itself -- the real spacing is 5s plus however long a
// response takes.
const REFRESH_MS = 5000;

export function HistoryPage() {
  // `undefined` = "the window the user saved in Settings", which only the
  // server knows; it is replaced by an explicit number the first time the
  // control is touched.
  const [minutes, setMinutes] = useState<number | undefined>(undefined);
  // Bumped by Reset zoom to remount the chart -- see chartKey below.
  const [resetNonce, setResetNonce] = useState(0);

  //  Which series the user has explicitly switched on or off, by label.
  //  Deliberately OVERRIDES rather than the visibility itself: the server
  //  decides each series' default (probes on, a probe disabled in Settings
  //  off, duty off), and a series that appears mid-session -- the first time a
  //  window includes duty, say -- has to pick up that default rather than
  //  whatever a snapshot of the earlier series list happened to hold.
  //
  //  Keyed by label because that is what survives a refetch: `chart.series` is
  //  a fresh array on every 5s poll and on every window change.
  const [seriesOverrides, setSeriesOverrides] = useState<Record<string, boolean>>({});

  //  The auto-refresh preference. This route has no loader (see App.tsx) and
  //  GET /api/history/chart answers with chart data only, so a settings read
  //  is the only way to see the flag -- now the app's shared entry rather than
  //  a GET of its own. A failed read leaves polling off, the same fail-quiet
  //  direction this always failed in.
  const { data: settings } = useSettings();
  const autoRefresh = settings?.history_page?.autorefresh === "on";

  //  The chart. `minutes` is part of the key, so changing the window IS the
  //  refetch -- which is what retires the requestId counter that used to be
  //  the cache key, the in-flight guard, the race resolver and the poll
  //  trigger all at once.
  //
  //  placeholderData keeps the previous window's chart on screen while the new
  //  one loads, rather than dropping to the loading branch and back.
  //  refetchInterval is the auto-refresh poll: react-query already holds a
  //  request per key, so the in-flight guard the old effect hand-rolled (the
  //  `loading` dependency) has nothing left to do.
  const { data, isPending, isError } = useQuery({
    queryKey: queryKeys.historyChart(minutes ?? undefined),
    queryFn: () => fetchHistoryChart(BASE_URL, minutes ?? undefined),
    placeholderData: (previous) => previous,
    refetchInterval: autoRefresh ? REFRESH_MS : false,
  });

  const loading = isPending;
  const failed = isError;
  const shownMinutes = minutes ?? data?.minutes ?? PLACEHOLDER_MINUTES;
  const chart = data && hasPlottableHistory(data) ? toChartInput(data) : null;

  // HistoryChart deliberately preserves a drag-zoom across data updates, and
  // shouldResetScales cannot tell a data tick from a window change: it
  // compares the current x-scale against the data the plot already holds, so a
  // zoom dragged BEFORE the window changed looks identical to "not zoomed
  // yet", and the new window's data would render off-screen with no way back.
  // This page owns the window control, so it forces the reset the chart can't
  // infer: keying the chart on the window remounts it, and a fresh uPlot
  // autoscales to whatever it is built with. Reset zoom rides the same
  // mechanism as an explicit escape hatch.
  const chartKey = `${shownMinutes}-${resetNonce}`;

  //  Applied here rather than inside HistoryChart so the chart keeps taking a
  //  plain list of series and stays unaware of who decided what is drawn.
  const shownSeries = chart?.series.map((s) => ({
    ...s,
    visible: seriesOverrides[s.label] ?? s.visible,
  }));

  const toggleSeries = (label: string) =>
    setSeriesOverrides((current) => {
      const target = chart?.series.find((s) => s.label === label);
      const showing = current[label] ?? target?.visible ?? true;
      return { ...current, [label]: !showing };
    });

  return (
    <div className="pf-settings">
      <div className="pf-settings-content pf-settings-content--wide">
        <div className="pf-section">
          <h2 className="pf-section-title">History</h2>
          <div className="pf-section-body">
            <NumberField
              label="Minutes"
              value={shownMinutes}
              onChange={setMinutes}
              min={1}
              suffix="of history"
            />
            <div className="pf-settings-actions">
              {/* Served by the legacy Flask route (blueprints/history/routes.py
                  `export`), which streams a CSV attachment -- a plain link, not
                  a fetch. */}
              <a className="pf-modal-btn pf-modal-btn-link" href={`${BASE_URL}/history/export`}>
                Export CSV
              </a>
              {/* blueprints/history/templates/history/index.html:47. The ONLY
                  link into /metrics in the Flask tree -- templates/base.html's
                  navbar has never had one, and React's does not either. A
                  <Link>, not an <a>: an href would reload the SPA and drop the
                  shell's live socket. */}
              <Link className="pf-modal-btn pf-modal-btn-link" to="/metrics">
                Metrics
              </Link>
              <button
                type="button"
                className="pf-modal-btn"
                onClick={() => setResetNonce((n) => n + 1)}
              >
                Reset zoom
              </button>
            </div>

            {failed && (
              <div className="pf-banner pf-banner--error">
                Couldn't load history. Check the PiFire connection and try again.
              </div>
            )}
            {loading && <p className="pf-settings-hint">Loading history…</p>}
            {chart && shownSeries && (
              <>
                <SeriesToggles
                  series={chart.series}
                  overrides={seriesOverrides}
                  onToggle={toggleSeries}
                />
                <HistoryChart key={chartKey} times={chart.times} series={shownSeries} />
              </>
            )}
            {data && !chart && (
              <p className="pf-settings-hint">No history yet — start a cook to see the chart.</p>
            )}
          </div>
        </div>

        {/* Faithful placement: Flask renders the cook-file list on /history
            (blueprints/history/templates/history/index.html), not on a page of
            its own, so there is no nav entry to add either. */}
        <div className="pf-section">
          <h2 className="pf-section-title">Saved cooks</h2>
          <div className="pf-section-body">
            <CookFileList />
          </div>
        </div>
      </div>
    </div>
  );
}
