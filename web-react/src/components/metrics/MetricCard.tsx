import type { MetricRecord } from "@pifire/core/contracts/content";
import { metricRows, modeAccent } from "./metricFields";
import "./metrics.css";

/**
 * One metrics record, as Flask's _macro_metrics.html renders one: a mode-tinted
 * header, the Metric/Value/Converted table, and the raw record behind a
 * disclosure.
 *
 * The raw record is a <details>, not a button over conditional JSX. Flask uses
 * a Bootstrap collapse, which keeps the content in the DOM -- and so does
 * <details>, which means the browser's own find-in-page reaches a field the
 * table does not name (fanontime, the pellet levels, the brand) without the
 * user having to open every card first.
 */
export function MetricCard({ record, units }: { record: MetricRecord; units: string }) {
  const rows = metricRows(record, units);
  const heading = `${record.mode} Mode`;
  //  No `pf-` prefix on the id: cssCoverage's classesUsedIn() scans source
  //  strings for `pf-*` and would take one for a class with no rule behind it.
  const headingId = `metrics-card-${record.id}`;

  return (
    <section className={`pf-metrics-card ${modeAccent(record.mode)}`} aria-labelledby={headingId}>
      <h2 className="pf-metrics-card-title" id={headingId}>
        {heading}
      </h2>

      <table className="pf-metrics-table" aria-label={`${heading} metrics`}>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col">Value</th>
            <th scope="col">Converted</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              <td className="pf-metrics-raw-value">{row.value}</td>
              <td>{row.converted}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <details className="pf-metrics-details">
        <summary className="pf-metrics-summary">Raw Data</summary>
        <pre className="pf-metrics-json" data-testid={`metric-raw-${record.id}`}>
          {JSON.stringify(record, null, 2)}
        </pre>
      </details>
    </section>
  );
}
