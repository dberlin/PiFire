import { useState } from "react";
import {
  addProfile,
  deleteLog,
  deleteProfile,
  editBrands,
  editProfile,
  editWoods,
  hopperCheck,
  loadProfile,
  type PelletActionResult,
} from "../../helpers/pellets/pelletsApi";
import type { PelletProfileFields } from "../../helpers/contracts/control.gen";
import { useShellState } from "../../helpers/shellContext";
import { CurrentLoadCard } from "./CurrentLoadCard";
import { PelletLog } from "./PelletLog";
import "./pellets.css";
import { ProfileEditor } from "./ProfileEditor";
import { VocabTable } from "./VocabTable";

// Same-origin, matching every other module. Deliberately NOT `targetUrl` from
// the shell context: that value is absolute so ConnectionStatus has something
// readable to show, and fetching with it sends every request cross-origin,
// which Flask answers without CORS headers. The notify slice already shipped
// that bug once.
const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/**
 * The pellet INVENTORY manager: current load-out, hopper level, estimated
 * usage, the brand and wood vocabularies, the profile archive and the load log.
 *
 * NOT to be confused with /settings/pellets, the pellet-LEVEL settings tab
 * (thresholds, auger rate, prime ignition), which is a different page and stays
 * where it is.
 *
 * Reads come free: the backend broadcasts the whole pellet database as
 * socket_pellet_data, which the shell subscribes to, so there is no fetch, no
 * polling and no refetch-after-write anywhere on this page.
 */
export function PelletsPage() {
  const { live, pellets } = useShellState();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Every action funnels through here: one in-flight flag, one error slot, no
  // refetch. The server republishes socket_pellet_data within ~1s of any write
  // (blueprints/mobile/socket_io.py), so the page's data updates itself.
  //
  // No optimistic UI on purpose: a second local copy of the pellet database
  // could disagree with the server about whether a duplicate brand was
  // accepted, and that is a real branch. Disabling the buttons in flight is
  // the whole feedback mechanism.
  const run = async (action: () => Promise<PelletActionResult>) => {
    setBusy(true);
    setError(null);
    const r = await action();
    setBusy(false);
    if (!r.ok) setError(r.message);
  };

  if (!pellets) {
    return (
      <div className="pf-pellets pf-pellets-empty">
        <p>Loading pellet database…</p>
      </div>
    );
  }

  return (
    <div className="pf-pellets">
      {/* Always rendered, empty or not: grid auto-placement would shift every
          card down a row the moment an action was rejected. */}
      <div className="pf-pellets-error">
        {error && (
          <p className="pf-settings-error-text" role="alert">
            {error}
          </p>
        )}
      </div>

      <CurrentLoadCard
        db={pellets}
        hopperLevel={live.hopperLevel}
        tempUnits={live.tempUnits}
        busy={busy}
        onLoadProfile={(id) => run(() => loadProfile(BASE_URL, id))}
        onHopperCheck={() => run(() => hopperCheck(BASE_URL))}
      />

      <VocabTable
        title="Brands"
        itemNoun="brand"
        values={pellets.brands}
        busy={busy}
        onAdd={(v) => run(() => editBrands(BASE_URL, { new_brand: v }))}
        onDelete={(v) => run(() => editBrands(BASE_URL, { delete_brand: v }))}
      />

      <VocabTable
        title="Wood Types"
        itemNoun="wood type"
        values={pellets.woods}
        busy={busy}
        onAdd={(v) => run(() => editWoods(BASE_URL, { new_wood: v }))}
        onDelete={(v) => run(() => editWoods(BASE_URL, { delete_wood: v }))}
      />

      <ProfileEditor
        archive={pellets.archive}
        brands={pellets.brands}
        woods={pellets.woods}
        currentId={pellets.current.pelletid}
        busy={busy}
        onAdd={(fields: PelletProfileFields, andLoad: boolean) =>
          run(() => addProfile(BASE_URL, { ...fields, add_and_load: andLoad }))
        }
        onEdit={(fields) => run(() => editProfile(BASE_URL, fields))}
        onDelete={(id) => run(() => deleteProfile(BASE_URL, id))}
      />

      <PelletLog
        log={pellets.log}
        archive={pellets.archive}
        busy={busy}
        onDelete={(key) => run(() => deleteLog(BASE_URL, key))}
      />
    </div>
  );
}
