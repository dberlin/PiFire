import { useEffect, useState } from "react";
import { fetchHistoryChart, type HistoryChartData } from "../../helpers/history/historyApi";
import { getSettings } from "../../helpers/settings/settingsApi";
import { CookFileList } from "../cookfiles/CookFileList";
import { NumberField } from "../settings/fields/NumberField";
import { HistoryChart } from "./HistoryChart";
import { hasPlottableHistory, toChartInput } from "./historyAdapter";

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
// requests a minute. The timer is only armed while nothing is in flight (see
// the polling effect), so a response slower than this can never queue polls up
// behind itself -- the real spacing is 5s plus however long a response takes.
const REFRESH_MS = 5000;

// How the most recent request finished, tagged with a monotonic request id
// (see requestId below) rather than the window it was for. The tag is what
// makes "loading" a *derived* value below (a request is in flight exactly
// while no outcome for the latest request has arrived yet) instead of a
// second piece of state set from inside the effect.
//
// A window-value tag isn't enough: cycling the window back to a value it
// already visited (say 120 -> 60 -> 120) makes that value collide with an
// Outcome from an *earlier* settle of the same window, even though a brand
// new request for that window is still in flight -- an id, unique per
// request, can't collide the way a window value can repeat.
interface Outcome {
  id: number;
  failed: boolean;
}

export function HistoryPage() {
  // `undefined` = "the window the user saved in Settings", which only the
  // server knows; it is replaced by an explicit number the first time the
  // control is touched.
  const [minutes, setMinutes] = useState<number | undefined>(undefined);
  // The last payload that loaded successfully. Deliberately kept across both
  // a refetch and a failure, so changing the window doesn't blank the chart
  // and a transient error doesn't throw away what the user was looking at.
  const [data, setData] = useState<HistoryChartData | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  // Bumped by Reset zoom to remount the chart -- see chartKey below.
  const [resetNonce, setResetNonce] = useState(0);
  // Settings > History > Auto Refresh ("on"/"off"). Not derived from anything
  // rendered here -- it is fetched, so it is real state, not mirrored state.
  const [autoRefresh, setAutoRefresh] = useState(false);

  // The id of the request the current window wants. Bumped synchronously
  // during render whenever `minutes` changes -- the same render-phase
  // pattern SetpointEntry/SafetyTab use to reseed local state from a prop
  // change, applied here to a request counter instead. This (not a
  // `useRef` counter written from the effect) is what lets `loading` below
  // read the id at render time: `react-hooks/refs` forbids reading a ref
  // during render, and the id has to be current in the very same render
  // that changes `minutes`, not one render later once the effect has run --
  // otherwise a window change would read as "settled" for one frame,
  // reusing whatever the previous window's Outcome was.
  const [requestId, setRequestId] = useState(0);
  const [minutesForRequestId, setMinutesForRequestId] = useState(minutes);
  if (minutes !== minutesForRequestId) {
    setMinutesForRequestId(minutes);
    setRequestId((n) => n + 1);
  }

  useEffect(() => {
    let cancelled = false;
    const id = requestId;
    fetchHistoryChart(BASE_URL, minutes)
      .then((fresh) => {
        if (cancelled) return;
        setData(fresh);
        setOutcome({ id, failed: false });
      })
      .catch(() => {
        if (!cancelled) setOutcome({ id, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [minutes, requestId]);

  // Read the auto-refresh preference once, on mount. This route has no loader
  // (see App.tsx) and GET /api/history/chart deliberately answers with chart
  // data only, so a settings read is the only way to see the flag. A failed
  // read leaves polling off -- the same fail-quiet stance DashboardRoute takes
  // for its first_time_setup check, and the safe direction to fail in.
  useEffect(() => {
    let cancelled = false;
    getSettings(BASE_URL)
      .then((s) => {
        if (!cancelled) setAutoRefresh(s.history_page?.autorefresh === "on");
      })
      .catch(() => {
        /* advisory only -- the page is fully usable without auto refresh */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Plain render-time computation from state -- no effect, no mirrored state.
  const loading = outcome === null || outcome.id !== requestId;
  const failed = !loading && outcome !== null && outcome.failed;
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

  // Auto-refresh polling. A tick bumps the SAME request counter a window
  // change bumps, so a poll travels the identical path as any other data
  // update: `chartKey` is unaffected (a refresh is not a window change), so
  // the chart re-renders instead of remounting and HistoryChart's
  // shouldResetScales preserves an active drag-zoom across the setData.
  //
  // `loading` in the deps is the in-flight guard, and it reuses the request id
  // the page already keeps rather than adding a second mechanism: while a
  // request for the current window is outstanding this effect simply holds no
  // timer, and it re-arms the moment that request settles (successfully or
  // not, so a transient failure retries on the next tick). The cleanup clears
  // the timer on unmount, on a window change, and the moment the setting is
  // read as off.
  useEffect(() => {
    if (!autoRefresh || loading) return;
    const timer = setInterval(() => setRequestId((n) => n + 1), REFRESH_MS);
    return () => clearInterval(timer);
  }, [autoRefresh, loading]);

  return (
    <div className="pf-settings">
      {/* Widen past .pf-settings-content's 720px form column: this page is one
          big chart, not a settings form. */}
      <div className="pf-settings-content" style={{ maxWidth: 1100 }}>
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
              <a
                className="pf-modal-btn"
                href={`${BASE_URL}/history/export`}
                style={{ textAlign: "center", textDecoration: "none" }}
              >
                Export CSV
              </a>
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
            {chart && <HistoryChart key={chartKey} times={chart.times} series={chart.series} />}
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
