import { useQuery } from "@tanstack/react-query";
import { fetchMetrics, metricsExportUrl } from "../../helpers/metrics/metricsApi";
import { queryKeys } from "../../helpers/query/keys";
import { unwrap } from "../../helpers/query/unwrap";
import { MetricCard } from "./MetricCard";
import "./metrics.css";

// Same-origin, matching every other module. Deliberately NOT `targetUrl` from
// the shell context: that value is absolute so ConnectionStatus has something
// readable to show, and fetching with it sends every request cross-origin,
// which Flask answers without CORS headers.
const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/**
 * The metrics page: one card per mode the grill has passed through.
 *
 * Read once on mount, never on a timer. A metrics row is written when a mode
 * ENDS, so between transitions there is nothing new to see -- and a mode lasts
 * minutes to hours. The dashboard is where a live reading belongs.
 *
 * Records are shown in the order the server read them (oldest first), matching
 * the CSV the export button hands over. A page and its own export disagreeing
 * about order would make the two impossible to line up.
 */
export function MetricsPage() {
  const {
    data: payload,
    isPending,
    error,
  } = useQuery({
    queryKey: queryKeys.metrics,
    //  fetchMetrics RESOLVES its failures (metricsTypes.ts documents why), so
    //  it needs unwrap() to reject before useQuery can tell the two apart.
    queryFn: () => unwrap(fetchMetrics(BASE_URL)),
  });

  if (isPending) {
    return (
      <div className="pf-metrics">
        <p>Loading metrics…</p>
      </div>
    );
  }

  if (!payload) {
    return (
      <div className="pf-metrics">
        <p className="pf-settings-error-text" role="alert">
          {error?.message ?? "The server did not answer."}
        </p>
      </div>
    );
  }

  const { metrics, units, augerrate } = payload;

  return (
    <div className="pf-metrics">
      <header className="pf-metrics-header">
        <h1 className="pf-metrics-title">Metrics</h1>
        {/* Every "Estimated Pellet Usage" on this page is augerontime x this
            number. Stating it is what makes the estimates checkable, and it
            is the setting the Flask page silently ignored. */}
        <span className="pf-metrics-rate">{`Auger rate: ${augerrate} g/s`}</span>
        {metrics.length > 0 && (
          // An anchor, not a fetch: the file stays out of JS memory and the
          // user gets the browser's own save dialog.
          <a className="pf-metrics-btn" href={metricsExportUrl(BASE_URL)} download>
            Download CSV Data
          </a>
        )}
      </header>

      {metrics.length === 0 ? (
        <div className="pf-metrics-empty">
          <h2 className="pf-metrics-empty-title">No Data</h2>
          <p className="pf-metrics-empty-note">Start the grill to begin populating metrics.</p>
        </div>
      ) : (
        <div className="pf-metrics-list">
          {metrics.map((record) => (
            <MetricCard key={String(record.id)} record={record} units={units} />
          ))}
        </div>
      )}
    </div>
  );
}
