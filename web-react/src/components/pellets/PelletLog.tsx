import { useState } from "react";
import type { PelletProfile } from "../../helpers/pellets/pelletTypes";
import { ConfirmAction } from "../dashboard/ConfirmAction";
import { Rating } from "./Rating";

interface Props {
  log: Record<string, string>;
  archive: Record<string, PelletProfile>;
  busy: boolean;
  onDelete(key: string): void;
}

/**
 * The pellet load log: one row per load, oldest first.
 *
 * A row's value is either a profile id or the literal "deleted" -- delete_profile
 * rewrites every log entry pointing at the removed profile to that string
 * (common/pellets_actions.py). A row can ALSO carry an id that is simply absent
 * from the archive; the Jinja at index.html:447 subscripts it and 500s, so this
 * gives both cases the same "User Deleted Profile" treatment.
 */
export function PelletLog({ log, archive, busy, onDelete }: Props) {
  // One ConfirmAction for the whole table, keyed by a pending timestamp,
  // rather than one per row -- the arrangement StringListField documents.
  const [pending, setPending] = useState<string | null>(null);

  const rows = Object.entries(log).sort(([a], [b]) => a.localeCompare(b));

  return (
    <section className="pf-pellets-card pf-pellets-wide" aria-label="Pellet Log">
      <div className="pf-pellets-card-title">Pellet Log</div>

      <div className="pf-pellets-scroll">
        <table className="pf-devices-table">
          <tbody>
            {rows.map(([timestamp, value]) => {
              const profile = value === "deleted" ? undefined : archive[value];
              return (
                <tr key={timestamp}>
                  <td>{timestamp}</td>
                  <td>{profile ? `${profile.brand} ${profile.wood}` : "User Deleted Profile"}</td>
                  <td>{profile ? <Rating value={profile.rating} /> : "-"}</td>
                  <td>
                    {profile ? (
                      <button
                        className="pf-devices-table-btn"
                        aria-label={`Delete log entry ${timestamp}`}
                        disabled={busy}
                        onClick={() => setPending(timestamp)}
                      >
                        ✕
                      </button>
                    ) : (
                      "-"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <ConfirmAction
        open={pending !== null}
        title="Delete log entry?"
        message={`The load recorded at ${pending} will be removed from the pellet log.`}
        onConfirm={() => {
          const key = pending;
          setPending(null);
          if (key !== null) onDelete(key);
        }}
        onCancel={() => setPending(null)}
      />
    </section>
  );
}
