import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { cookFileExportUrl, fetchCookFileChart } from "../../helpers/files/cookfileApi";
import { queryKeys } from "../../helpers/query/keys";
import { HistoryChart } from "../history/HistoryChart";
import { hasNumericTimes, toChartAnnotations, toCookChartInput } from "./cookfileAdapter";

// Fetches the chart separately from the detail payload, exactly as Flask does
// (cookfile/index.html renders first with a "Loading Graph..." spinner, then
// cookfile.js calls refreshChart). A long cook's graph_data is by far the
// largest member of the archive, and making the metadata card wait for it would
// leave the page blank for seconds on a Pi.
//
// The chart itself is components/history/HistoryChart, unchanged: a saved cook
// and a live window are the same shape of data, so there is one chart to
// maintain, not two. What this adds is the annotation toggle and a reset.
//
// Accepted regression from the Flask page: drag-zoom on x plus a Reset button,
// where chartjs-plugin-zoom gave wheel/pinch zoom and xy pan. Matching that
// would mean a second chart implementation.

export function CookFileChart({ filename }: { filename: string }) {
  const [showAnnotations, setShowAnnotations] = useState(true);
  const [resetNonce, setResetNonce] = useState(0);

  const { data, isPending, error } = useQuery({
    queryKey: queryKeys.cookfileChart(filename),
    //  apiEnvelope.ts throws FileRequestError already -- no unwrap() needed,
    //  same reasoning as CookFilePage.
    queryFn: () => fetchCookFileChart(filename),
  });

  const loading = isPending;
  const failed = !loading && error != null;

  const chart = data ? toCookChartInput(data) : null;
  const annotations = data && showAnnotations ? toChartAnnotations(data.annotations) : undefined;
  //  A payload with string time labels is a pre-v1.5 archive, not an empty one,
  //  and the fix is Attempt Repair -- so the two are never reported alike.
  const legacyTimes =
    data !== undefined && data.time_labels.length > 0 && !hasNumericTimes(data.time_labels);

  return (
    <>
      <div className="pf-cf-toolbar">
        <label>
          <input
            type="checkbox"
            checked={showAnnotations}
            onChange={(e) => setShowAnnotations(e.target.checked)}
          />{" "}
          Show mode changes
        </label>
        <button type="button" className="pf-modal-btn" onClick={() => setResetNonce((n) => n + 1)}>
          Reset zoom
        </button>
        <a className="pf-modal-btn" href={cookFileExportUrl(filename, "data")} download>
          Download CSV file
        </a>
      </div>

      {loading && <p className="pf-settings-hint">Loading graph…</p>}
      {failed && (
        <div className="pf-banner pf-banner--error">Couldn't load this cook's chart data.</div>
      )}
      {chart && (
        // Remounting is how a reset happens: uPlot autoscales to whatever it is
        // built with, and shouldResetScales cannot tell a deliberate reset from
        // an untouched zoom. Same mechanism HistoryPage's Reset zoom uses.
        <HistoryChart
          key={`${filename}-${resetNonce}`}
          times={chart.times}
          series={chart.series}
          annotations={annotations}
        />
      )}
      {!loading && !failed && !chart && legacyTimes && (
        <p className="pf-settings-hint">
          This cook file's chart data is in an older format. Use Attempt Repair above to convert it.
        </p>
      )}
      {!loading && !failed && !chart && !legacyTimes && (
        <p className="pf-settings-hint">This cook file has no chart data.</p>
      )}
    </>
  );
}
