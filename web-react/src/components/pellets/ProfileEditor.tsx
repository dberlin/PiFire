import { useState } from "react";
import type {
  EditPelletProfileData,
  PelletDbSchema,
  PelletProfileFields,
} from "../../helpers/contracts/control.gen";
import { ConfirmAction } from "../dashboard/ConfirmAction";
import { Select } from "../settings/fields/Select";

interface Props {
  archive: PelletDbSchema["archive"];
  brands: string[];
  woods: string[];
  currentId: string;
  busy: boolean;
  onAdd(fields: PelletProfileFields, andLoad: boolean): void;
  onEdit(fields: EditPelletProfileData): void;
  onDelete(id: string): void;
}

// Flask's default comment text, index.html:295.
const DEFAULT_COMMENTS = "Enter comments here.";

// Rating options 5..1 with star runs, matching index.html:272-288 (5 selected).
const RATING_OPTIONS = [5, 4, 3, 2, 1].map((n) => ({
  value: String(n),
  label: "★".repeat(n),
}));

const sortValues = (values: string[]) => [...values].sort((a, b) => a.localeCompare(b));

/** One draft per profile id, derived from the archive. Module-level and pure
    so it can be used both to initialise state and to re-seed it during render. */
function seed(archive: PelletDbSchema["archive"]): Record<string, PelletProfileFields> {
  const drafts: Record<string, PelletProfileFields> = {};
  for (const [id, p] of Object.entries(archive)) {
    if (!p) continue;
    drafts[id] = {
      brand_name: p.brand,
      wood_type: p.wood,
      rating: p.rating,
      comments: p.comments,
    };
  }
  return drafts;
}

export function ProfileEditor({
  archive,
  brands,
  woods,
  currentId,
  busy,
  onAdd,
  onEdit,
  onDelete,
}: Props) {
  const sortedBrands = sortValues(brands);
  const sortedWoods = sortValues(woods);

  // Local edits per profile id. Re-seeded during render when the archive
  // identity changes (a socket tick), NOT in an effect: React Compiler is
  // active and setState-in-useEffect for derived state is banned. Same idiom
  // as SafetyTab.tsx / SetpointEntry.tsx.
  //
  // Reseeding on every socket tick discards an in-progress edit. That is
  // correct here and is why `busy` exists: the tick that follows a save
  // carries the saved values. Do NOT add an "is dirty" exception without
  // treating it as the design change it is.
  const [drafts, setDrafts] = useState<Record<string, PelletProfileFields>>(() => seed(archive));
  const [archiveSeen, setArchiveSeen] = useState(archive);
  if (archive !== archiveSeen) {
    setArchiveSeen(archive);
    setDrafts(seed(archive));
  }

  // A SET, not a single id: Flask gives each profile its own independent
  // collapse (index.html:310-384), so several can be open at once and an edit
  // in one must not disturb another's draft.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const [adding, setAdding] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [newProfile, setNewProfile] = useState<PelletProfileFields>(() => ({
    brand_name: sortValues(brands)[0] ?? "",
    wood_type: sortValues(woods)[0] ?? "",
    rating: 5,
    comments: DEFAULT_COMMENTS,
  }));

  // INVARIANT: the add form's brand and wood always name a member of the
  // current vocabulary, or "" when there is nothing to name. The form holds its
  // own draft, so a socket tick that adds or removes a brand would otherwise
  // leave it pointing at a value with no matching <option> -- and a <select> in
  // that state DISPLAYS its first option while its React state keeps the stale
  // one, so the form shows one brand and posts another (or posts "" when the
  // list had been empty). pellets_add_profile takes brand_name verbatim
  // (common/pellets_actions.py:110-111), so the mismatch is written to the
  // archive with nothing to catch it.
  //
  // Render-phase adjustment, NOT an effect: React Compiler is active and
  // setState-in-useEffect for derived state is banned. Same idiom as
  // CurrentLoadCard's `choice`. Conditioned on membership rather than on the
  // props changing identity, so a deliberate choice survives the ~1s socket
  // republish that hands over a fresh array with identical contents.
  const anchoredBrand = brands.includes(newProfile.brand_name)
    ? newProfile.brand_name
    : (sortedBrands[0] ?? "");
  const anchoredWood = woods.includes(newProfile.wood_type)
    ? newProfile.wood_type
    : (sortedWoods[0] ?? "");
  if (anchoredBrand !== newProfile.brand_name || anchoredWood !== newProfile.wood_type) {
    setNewProfile((p) => ({ ...p, brand_name: anchoredBrand, wood_type: anchoredWood }));
  }

  const ids = Object.keys(archive).sort();
  const pendingProfile = pending === null ? undefined : archive[pending];

  const setDraft = (id: string, patch: Partial<PelletProfileFields>) =>
    setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));

  const fieldset = (
    nameFor: string,
    value: PelletProfileFields,
    update: (patch: Partial<PelletProfileFields>) => void,
  ) => (
    <div className="pf-pellets-profile-form">
      <Select
        label={`Brand for ${nameFor}`}
        value={value.brand_name}
        onChange={(v) => update({ brand_name: v })}
        options={sortedBrands.map((b) => ({ value: b, label: b }))}
      />
      <Select
        label={`Wood for ${nameFor}`}
        value={value.wood_type}
        onChange={(v) => update({ wood_type: v })}
        options={sortedWoods.map((w) => ({ value: w, label: w }))}
      />
      <Select
        label={`Rating for ${nameFor}`}
        value={String(value.rating)}
        onChange={(v) => update({ rating: Number(v) })}
        options={RATING_OPTIONS}
      />
      {/* An explicit per-profile label: a bare "Comments" repeats on every
          expanded profile AND on the add form, and would collide. */}
      <textarea
        className="pf-input"
        aria-label={`Comments for ${nameFor}`}
        value={value.comments}
        onChange={(e) => update({ comments: e.target.value })}
      />
    </div>
  );

  return (
    <section className="pf-pellets-card pf-pellets-wide" aria-label="Pellet Profiles">
      <div className="pf-pellets-card-title">Pellet Profiles</div>

      <div className="pf-pellets-scroll">
        <button className="pf-modal-btn accent" onClick={() => setAdding((a) => !a)}>
          Add Profile
        </button>
        {adding && (
          <div className="pf-pellets-profile">
            {fieldset("new profile", newProfile, (patch) =>
              setNewProfile((p) => ({ ...p, ...patch })),
            )}
            <div className="pf-pellets-profile-actions">
              <button
                className="pf-modal-btn accent"
                disabled={busy}
                onClick={() => onAdd(newProfile, false)}
              >
                Add
              </button>
              <button
                className="pf-modal-btn"
                disabled={busy}
                onClick={() => onAdd(newProfile, true)}
              >
                Add &amp; Load
              </button>
            </div>
          </div>
        )}

        {ids.map((id) => {
          const profile = archive[id];
          if (!profile) return null;
          const label = `${profile.brand} ${profile.wood}`;
          const draft = drafts[id];
          const isCurrent = id === currentId;
          return (
            <div className="pf-pellets-profile" key={id}>
              <button
                className="pf-pellets-profile-head"
                onClick={() =>
                  setExpanded((open) => {
                    const next = new Set(open);
                    if (!next.delete(id)) next.add(id);
                    return next;
                  })
                }
              >
                {label}
              </button>
              {expanded.has(id) && draft && (
                <>
                  {fieldset(label, draft, (patch) => setDraft(id, patch))}
                  <div className="pf-pellets-profile-actions">
                    <button
                      className="pf-modal-btn accent"
                      disabled={busy}
                      onClick={() => onEdit({ profile: id, ...draft })}
                    >
                      {`Save ${label}`}
                    </button>
                    <button
                      className="pf-modal-btn danger"
                      disabled={busy || isCurrent}
                      title={isCurrent ? "A loaded profile cannot be deleted" : undefined}
                      onClick={() => setPending(id)}
                    >
                      {`Delete ${label}`}
                    </button>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>

      <ConfirmAction
        open={pending !== null}
        title={
          pendingProfile
            ? `Delete ${pendingProfile.brand} ${pendingProfile.wood}?`
            : "Delete profile?"
        }
        // The cascade is why ConfirmAction has a `message` prop at all:
        // delete_profile rewrites every log entry pointing at this profile to
        // a tombstone (common/pellets_actions.py).
        message="Every entry in the pellet log that refers to this profile will be marked “User Deleted Profile”. This cannot be undone."
        onConfirm={() => {
          const id = pending;
          setPending(null);
          if (id !== null) onDelete(id);
        }}
        onCancel={() => setPending(null)}
      />
    </section>
  );
}
