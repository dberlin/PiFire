import { useState } from "react";
import { saveRecipeMetadata } from "../../helpers/files/recipeApi";
import type { RecipeMetadata, RecipeStep } from "../../helpers/files/recipeTypes";
import type { SaveStatus } from "../../helpers/settings/useSaveSettings";
import { ConfirmAction } from "../dashboard/ConfirmAction";
import { NumberField } from "../settings/fields/NumberField";
import { Select } from "../settings/fields/Select";
import { TextField } from "../settings/fields/TextField";
import { SaveBar } from "../settings/SaveBar";

// The recipe's whole-metadata form: recipes_api.py's set_metadata() takes a
// single patch of every field this editor owns, so one SaveBar saves all of
// them together -- there is no per-field save the way CookFileMeta's title
// has its own button.
//
// food_probes is the one field here that is destructive: lowering it
// truncates a trigger temperature off every program step (set_metadata
// reshapes recipe.json's trigger_temps.food to match), and there is no undo.
// Saving a lower value is intercepted by ConfirmAction naming exactly how
// many steps lose data; every other field change saves straight through.

interface Props {
  file: string;
  metadata: RecipeMetadata;
  steps: RecipeStep[];
  onChanged: () => void;
}

type Draft = {
  title: string;
  author: string;
  description: string;
  difficulty: string;
  units: string;
  prep_time: number;
  cook_time: number;
  rating: number;
  food_probes: number;
};

function readDraft(m: RecipeMetadata): Draft {
  return {
    title: m.title,
    author: m.author,
    description: m.description,
    difficulty: m.difficulty,
    units: m.units,
    prep_time: m.prep_time,
    cook_time: m.cook_time,
    rating: m.rating,
    food_probes: m.food_probes,
  };
}

const DIFFICULTY_OPTIONS = ["Easy", "Intermediate", "Hard", "Advanced"].map((v) => ({
  value: v,
  label: v,
}));

const UNITS_OPTIONS = [
  { value: "F", label: "Fahrenheit (°F)" },
  { value: "C", label: "Celsius (°C)" },
];

// Steps whose trigger_temps.food would actually be truncated by lowering
// food_probes to `next` -- the count ConfirmAction names, computed the same
// way set_metadata() reshapes each step server-side.
function affectedStepCount(steps: RecipeStep[], next: number): number {
  return steps.filter((step) => step.trigger_temps.food.length > next).length;
}

function StarPicker({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  return (
    <span className="pf-rcp-stars" role="radiogroup" aria-label="Rating">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          type="button"
          key={n}
          className={n <= value ? "pf-rcp-star pf-rcp-star--filled" : "pf-rcp-star"}
          aria-label={`${n} star${n === 1 ? "" : "s"}`}
          aria-pressed={n === value}
          onClick={() => onChange(n)}
        >
          {"★"}
        </button>
      ))}
    </span>
  );
}

export function RecipeMetaEditor({ file, metadata, steps, onChanged }: Props) {
  // Render-phase reseed, not useEffect + setState: a refetch after another
  // editor's save (e.g. the ingredients cascade) must not leave a stale draft
  // on screen for a frame. Same idiom as CookFileMeta's title/seededTitle.
  const [draft, setDraft] = useState<Draft>(() => readDraft(metadata));
  const [seeded, setSeeded] = useState(metadata);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<SaveStatus>({ kind: "idle" });
  const [confirmCount, setConfirmCount] = useState<number | null>(null);

  if (metadata !== seeded) {
    setSeeded(metadata);
    setDraft(readDraft(metadata));
    setStatus({ kind: "idle" });
  }

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const loaded = readDraft(metadata);
  const dirty = (Object.keys(draft) as (keyof Draft)[]).some((key) => draft[key] !== loaded[key]);

  const commit = () => {
    setSaving(true);
    setStatus({ kind: "idle" });
    saveRecipeMetadata(file, { ...draft })
      .then(() => {
        setStatus({ kind: "saved" });
        onChanged();
      })
      .catch((err: unknown) =>
        setStatus({ kind: "error", message: err instanceof Error ? err.message : "Save failed." }),
      )
      .finally(() => setSaving(false));
  };

  const onSave = () => {
    if (draft.food_probes < metadata.food_probes) {
      const affected = affectedStepCount(steps, draft.food_probes);
      if (affected > 0) {
        setConfirmCount(affected);
        return;
      }
    }
    commit();
  };

  return (
    <>
      <TextField label="Title" value={draft.title} onChange={(v) => set("title", v)} />
      <TextField label="Author" value={draft.author} onChange={(v) => set("author", v)} />

      <label className="pf-field pf-field-column">
        <span className="pf-field-label">Description</span>
        <textarea
          className="pf-input"
          rows={4}
          value={draft.description}
          onChange={(e) => set("description", e.target.value)}
        />
      </label>

      <NumberField
        label="Prep time"
        value={draft.prep_time}
        onChange={(v) => set("prep_time", v)}
        min={0}
        suffix="min"
      />
      <NumberField
        label="Cook time"
        value={draft.cook_time}
        onChange={(v) => set("cook_time", v)}
        min={0}
        suffix="min"
      />
      <NumberField
        label="Food probes"
        value={draft.food_probes}
        onChange={(v) => set("food_probes", v)}
        min={0}
        max={8}
        hint="Lowering this removes a trigger temperature from every program step."
      />
      <Select
        label="Difficulty"
        value={draft.difficulty}
        options={DIFFICULTY_OPTIONS}
        onChange={(v) => set("difficulty", v)}
      />
      <Select
        label="Units"
        value={draft.units}
        options={UNITS_OPTIONS}
        onChange={(v) => set("units", v)}
      />

      <div className="pf-field">
        <span className="pf-field-label">Rating</span>
        <StarPicker value={draft.rating} onChange={(n) => set("rating", n)} />
      </div>

      <SaveBar onSave={onSave} saving={saving} status={status} dirty={dirty} />

      <ConfirmAction
        open={confirmCount !== null}
        title="Lower the food probe count?"
        message={
          confirmCount === null
            ? undefined
            : `${confirmCount} program step${confirmCount === 1 ? "" : "s"} will lose a trigger temperature. This cannot be undone.`
        }
        onConfirm={() => {
          setConfirmCount(null);
          commit();
        }}
        onCancel={() => setConfirmCount(null)}
      />
    </>
  );
}
