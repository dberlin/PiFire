import type {
  CookFileDetail,
  CookFileEvent,
  CookFileTotals,
} from "../../helpers/contracts/content.gen";
import { cookFileExportUrl } from "../../helpers/files/cookfileApi";

// The events table, its totals row, and a per-event disclosure.
//
// Every cell is a STORED string -- starttime_c, endtime_c, augerontime_c,
// estusage_i/estusage_m -- computed at cook time by process_metrics. Nothing
// here recomputes or reformats them: the CSV export reads the same rows, and a
// second formatter would silently disagree with it.
//
// The units come from the COOK FILE, not the app. cookfile/index.html branches
// on metadata['units'] for both the per-row usage column and the totals row, so
// a cook recorded in Fahrenheit keeps showing pounds/ounces after the user
// switches the app to Celsius -- the numbers in the archive were never
// converted.

interface Props {
  filename: string;
  events: CookFileEvent[];
  /** May legitimately be {}: the endpoint reports no totals for a cook with
   * fewer than two events, because prepare_event_totals indexes events[-2]
   * unconditionally. The Flask page 500s on such a file; this one renders the
   * rows it has and omits the totals. */
  totals: CookFileDetail["event_totals"];
  /** metadata.units -- "F" or "C". */
  units: string;
}

// Already shown in the row, or an epoch the row's `_c` string supersedes.
const ROW_FIELDS = new Set([
  "id",
  "mode",
  "starttime",
  "starttime_c",
  "endtime",
  "endtime_c",
  "augerontime",
  "augerontime_c",
  "estusage_i",
  "estusage_m",
  "pellet_level_start",
  "pellet_level_end",
]);

function usage(event: CookFileEvent, units: string): string {
  return units === "F" ? event.estusage_i : event.estusage_m;
}

function hasTotals(totals: Props["totals"]): totals is CookFileTotals {
  return Object.keys(totals).length > 0;
}

export function EventsTable({ filename, events, totals, units }: Props) {
  if (events.length === 0) {
    return <p className="pf-settings-hint">This cook file has no recorded events.</p>;
  }

  return (
    <>
      <table className="pf-cf-table">
        <thead>
          <tr>
            <th>Mode</th>
            <th>Begin</th>
            <th>End</th>
            <th>Auger time</th>
            <th>Est. pellet use</th>
            <th>Pellet level start</th>
            <th>Pellet level end</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id}>
              <td>{event.mode}</td>
              <td>{event.starttime_c}</td>
              <td>{event.endtime_c}</td>
              <td>{event.augerontime_c}</td>
              <td>{usage(event, units)}</td>
              <td>{event.pellet_level_start}</td>
              <td>{event.pellet_level_end}</td>
              <td>
                {/* A native disclosure rather than a modal: no focus trap to
                    get wrong, and it prints. The Flask page has nine bespoke
                    mode-specific modals; a generic key/value list is the
                    deliberate simplification for a diagnostic panel. */}
                <details>
                  <summary>{`Details for ${event.mode} at ${event.starttime_c}`}</summary>
                  <dl className="pf-cf-dl">
                    {Object.entries(event as unknown as Record<string, unknown>)
                      .filter(([key]) => !ROW_FIELDS.has(key))
                      .map(([key, value]) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd>{String(value)}</dd>
                        </div>
                      ))}
                  </dl>
                </details>
              </td>
            </tr>
          ))}
        </tbody>
        {hasTotals(totals) && (
          <tfoot>
            <tr>
              <th>Totals</th>
              <td colSpan={2}>{totals.cooktime}</td>
              <td>{totals.augerontime}</td>
              <td>{units === "F" ? totals.estusage_i : totals.estusage_m}</td>
              <td>{totals.pellet_level_start}</td>
              <td>{totals.pellet_level_end}</td>
              <td> </td>
            </tr>
          </tfoot>
        )}
      </table>

      <div className="pf-settings-actions">
        <a
          className="pf-modal-btn pf-modal-btn-link"
          href={cookFileExportUrl(filename, "events")}
          download
        >
          Download events CSV
        </a>
      </div>
    </>
  );
}
