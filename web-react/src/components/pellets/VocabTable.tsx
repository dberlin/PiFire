import { useState } from "react";

import { ConfirmAction } from "../dashboard/ConfirmAction";
import { TextField } from "../settings/fields/TextField";

interface Props {
  title: "Brands" | "Wood Types";
  itemNoun: "brand" | "wood type";
  values: string[];
  busy: boolean;
  onAdd(value: string): void;
  onDelete(value: string): void;
}

// Flask's exact wording, blueprints/pellets/routes.py:62-63 and :86-87. Note
// it is "wood", not "wood type", in the woods sentence -- transcribed, not
// derived from itemNoun, because the two do not follow the same pattern.
function duplicateMessage(itemNoun: Props["itemNoun"], value: string): string {
  return itemNoun === "brand"
    ? `${value} already in pellet brands list.`
    : `${value} already in pellet wood list.`;
}

/**
 * One component, two instances: the brand list and the wood list.
 *
 * `StringListField` is deliberately NOT reused. It edits a local array and
 * hands the WHOLE array to a settings save -- precisely the whole-collection
 * write this page must never perform (see helpers/pellets/pelletsApi.ts) --
 * and its "clear the last row instead of removing it" rule is wrong here: a
 * brand list of one can legally go to zero.
 *
 * The duplicate check lives here rather than on the server: Flask treats a
 * duplicate as an error (routes.py:62-63) but the shared socketio/REST handler
 * silently no-ops, and changing the handler would flip the existing socketio
 * tests. So the behaviour is preserved and only its LOCATION moves.
 */
export function VocabTable({ title, itemNoun, values, busy, onAdd, onDelete }: Props) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  // One ConfirmAction for the whole table, keyed by a pending value, rather
  // than one per row -- the arrangement StringListField documents.
  const [pending, setPending] = useState<string | null>(null);

  const sorted = [...values].sort((a, b) => a.localeCompare(b));

  const submit = () => {
    const value = draft.trim();
    if (!value) return;
    if (values.includes(value)) {
      setError(duplicateMessage(itemNoun, value));
      return;
    }
    setError(null);
    setDraft("");
    onAdd(value);
  };

  return (
    <section className="pf-pellets-card" aria-label={title}>
      <div className="pf-pellets-card-title">{title}</div>

      <div className="pf-pellets-scroll">
        <table className="pf-devices-table">
          <tbody>
            {sorted.map((value) => (
              <tr key={value}>
                <td>{value}</td>
                <td>
                  <button
                    className="pf-devices-table-btn"
                    aria-label={`Delete ${value}`}
                    disabled={busy}
                    onClick={() => setPending(value)}
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <TextField label={`New ${itemNoun}`} value={draft} onChange={setDraft} />
      {error && (
        <p className="pf-settings-error-text" role="alert">
          {error}
        </p>
      )}
      <button className="pf-modal-btn accent" disabled={busy} onClick={submit}>
        {`Add ${itemNoun}`}
      </button>

      <ConfirmAction
        open={pending !== null}
        title={`Delete ${pending ?? ""}?`}
        message={`“${pending}” will be removed from the ${itemNoun} list.`}
        onConfirm={() => {
          const value = pending;
          setPending(null);
          if (value !== null) onDelete(value);
        }}
        onCancel={() => setPending(null)}
      />
    </section>
  );
}
