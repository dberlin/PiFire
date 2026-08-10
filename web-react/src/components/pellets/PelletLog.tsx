import { useState } from "react";
import type { PelletDbSchema } from "../../helpers/contracts/control.gen";
import { ConfirmAction } from "../dashboard/ConfirmAction";
import { Rating } from "./Rating";

interface Props {
  log: PelletDbSchema["log"];
  archive: PelletDbSchema["archive"];
  busy: boolean;
  onDelete(key: string): void;
}

/** A log key is epoch milliseconds as a decimal string. */
function formatKey(key: string): string {
  return new Date(Number(key)).toLocaleString();
}

/**
 * The pellet load log: one row per load, oldest first.
 *
 * A row is either a load that still has its profile, a tombstone left by
 * delete_profile, or an id that is simply absent from the archive. The last two
 * get the same "User Deleted Profile" treatment, because neither has anything
 * to show and both are ordinary states rather than errors.
 */
export function PelletLog({ log, archive, busy, onDelete }: Props) {
  // One ConfirmAction for the whole table, keyed by a pending log key, rather
  // than one per row -- the arrangement StringListField documents.
  const [pending, setPending] = useState<string | null>(null);

  // Numeric, not lexicographic: a twelve-digit stamp sorts after a
  // thirteen-digit one as text.
  const rows = Object.entries(log).sort(([a], [b]) => Number(a) - Number(b));

  return (
    <section className="pf-pellets-card pf-pellets-wide" aria-label="Pellet Log">
      <div className="pf-pellets-card-title">Pellet Log</div>

      <div className="pf-pellets-scroll">
        <table className="pf-devices-table">
          <tbody>
            {rows.map(([key, entry]) => {
              if (!entry) return null;
              const profile = entry.pelletid === null ? undefined : archive[entry.pelletid];
              const when = formatKey(key);
              return (
                <tr key={key}>
                  <td>{when}</td>
                  <td>{profile ? `${profile.brand} ${profile.wood}` : "User Deleted Profile"}</td>
                  <td>{profile ? <Rating value={profile.rating} /> : "-"}</td>
                  <td>
                    {profile ? (
                      <button
                        className="pf-devices-table-btn"
                        aria-label={`Delete log entry ${when}`}
                        disabled={busy}
                        onClick={() => setPending(key)}
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
        message={`The load recorded at ${pending === null ? "" : formatKey(pending)} will be removed from the pellet log.`}
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
