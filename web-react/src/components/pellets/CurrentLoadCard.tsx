import type { PelletDbSchema, PelletProfile } from "@pifire/core/contracts/control";
import { useState } from "react";

import { formatUsage } from "../../helpers/pellets/usage";
import { Select } from "../settings/fields/Select";
import { Rating } from "./Rating";

interface Props {
  db: PelletDbSchema;
  hopperLevel: number;
  tempUnits: "F" | "C";
  busy: boolean;
  onLoadProfile(id: string): void;
  onHopperCheck(): void;
}

// Flask's thresholds, index.html:499-505.
function meterClass(level: number): string {
  if (level > 70) return "pf-pellets-meter pf-pellets-meter--ok";
  if (level > 30) return "pf-pellets-meter pf-pellets-meter--warn";
  return "pf-pellets-meter pf-pellets-meter--low";
}

export function CurrentLoadCard({
  db,
  hopperLevel,
  tempUnits,
  busy,
  onLoadProfile,
  onHopperCheck,
}: Props) {
  // May be undefined: current.pelletid can point at a profile that was
  // removed (or at nothing, on a cleared DB). The Jinja at index.html:47
  // would 500 here; this renders "No profile loaded".
  const loaded: PelletProfile | undefined = db.archive[db.current.pelletid];
  const usage = formatUsage(db.current.est_usage);
  const [picking, setPicking] = useState(false);

  const ids = Object.keys(db.archive).sort();
  const firstId = ids[0] ?? "";
  const [choice, setChoice] = useState(firstId);
  // Render-phase adjustment, NOT an effect: React Compiler is active and
  // setState-in-useEffect for derived state is banned. Same idiom as
  // SafetyTab.tsx. Re-seeds when the archive identity changes (a socket
  // tick) so a profile deleted elsewhere cannot leave a dangling selection.
  const [archiveSeen, setArchiveSeen] = useState(db.archive);
  if (db.archive !== archiveSeen) {
    setArchiveSeen(db.archive);
    if (!(choice in db.archive)) setChoice(firstId);
  }

  const primary = tempUnits === "F" ? usage.imperial : usage.metric;
  const secondary = tempUnits === "F" ? usage.metric : usage.imperial;

  return (
    <section className="pf-pellets-card pf-pellets-wide" aria-label="Current Load Out">
      <div className="pf-pellets-card-title">Current Load Out</div>
      <div className="pf-pellets-scroll">
        {loaded ? (
          <div className="pf-pellets-load">
            <div className="pf-pellets-load-name">{`${loaded.brand} ${loaded.wood}`}</div>
            <Rating value={loaded.rating} />
            <div className="pf-pellets-load-date">{db.current.date_loaded}</div>
            <p className="pf-pellets-load-comments">{loaded.comments}</p>
          </div>
        ) : (
          <div className="pf-pellets-load-name">No profile loaded</div>
        )}

        <div
          role="progressbar"
          aria-label="Hopper level"
          aria-valuenow={hopperLevel}
          aria-valuemin={0}
          aria-valuemax={100}
          className={meterClass(hopperLevel)}
        >
          <span style={{ width: `${hopperLevel}%` }} />
        </div>
        <div className="pf-pellets-level-text">{`${hopperLevel}%`}</div>

        {/* <output>, not a div: aria-label is prohibited on role generic, and
          these two readouts each need their own accessible name so a test can
          tell the primary slot from the alternate-units one. <output> is the
          semantic element for the result of a calculation, which is exactly
          what formatUsage() returns. */}
        <output className="pf-pellets-usage" aria-label="Estimated usage since reload">
          {primary}
        </output>
        <output
          className="pf-pellets-usage-alt"
          aria-label="Estimated usage since reload, alternate units"
        >
          {secondary}
        </output>
      </div>

      <div className="pf-pellets-actions">
        <button className="pf-modal-btn" disabled={busy} onClick={() => setPicking(true)}>
          Load New Pellets
        </button>
        <button className="pf-modal-btn" disabled={busy} onClick={onHopperCheck}>
          Refresh Status
        </button>
      </div>

      {/* Raw modal markup rather than ConfirmAction: this body needs a Select,
          and ConfirmAction's body is a fixed message string. The card root
          carries position:relative (.pf-pellets-card) because .pf-modal-scrim
          is position:absolute (dashboard.css:191-192). */}
      {picking && (
        <div className="pf-modal-scrim" onClick={() => setPicking(false)}>
          <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
            <div className="pf-modal-title">Load New Pellets</div>
            <Select
              label="Profile to load"
              value={choice}
              onChange={setChoice}
              options={ids.map((id) => {
                const profile = db.archive[id];
                return {
                  value: id,
                  label: profile ? `${profile.brand} ${profile.wood}` : id,
                };
              })}
            />
            <div className="pf-modal-actions">
              <button className="pf-modal-btn" onClick={() => setPicking(false)}>
                Cancel
              </button>
              <button
                className="pf-modal-btn accent"
                onClick={() => {
                  setPicking(false);
                  onLoadProfile(choice);
                }}
              >
                Load Profile
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
