import { useState } from "react";

import { ConfirmAction } from "../../dashboard/ConfirmAction";
import { Field } from "./Field";

export function StringListField({
  label,
  values,
  onChange,
  hint,
  error = null,
  path,
}: {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  hint?: string;
  error?: string | null;
  path?: string;
}) {
  // Index of the row awaiting confirmation; null when no dialog is open. One
  // <ConfirmAction> per field, not per row.
  const [pending, setPending] = useState<number | null>(null);

  // Ported from settings.js:925-941. Two rules, both dropped in the port: an
  // empty row goes without ceremony, and the LAST row is CLEARED rather than
  // removed ("keeps interface readier for use") — React would otherwise leave
  // the list at length 0.
  const removeAt = (i: number) => {
    if (values.length > 1) {
      onChange(values.filter((_, j) => j !== i));
      return;
    }
    onChange(values.map(() => ""));
  };

  const requestRemove = (i: number) => {
    if (values[i] === "") {
      removeAt(i);
      return;
    }
    setPending(i);
  };

  return (
    <>
      <Field label={label} hint={hint} error={error} path={path} className="pf-field-column">
        {({ id, describedBy, invalid }) => (
          <>
            {values.map((v, i) => (
              <div className="pf-stringlist-row" key={i}>
                <input
                  // Field's <label> points at row 0 for the click-to-focus
                  // affordance; every row (row 0 included) also carries its
                  // own aria-label because the field's one label can only
                  // name one row, and rows 1+ would otherwise have no
                  // accessible name at all.
                  id={i === 0 ? id : undefined}
                  className="pf-input"
                  type="text"
                  value={v}
                  aria-label={`${label} row ${i + 1}`}
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                  onChange={(e) => onChange(values.map((x, j) => (j === i ? e.target.value : x)))}
                />
                <button type="button" aria-label="Remove" onClick={() => requestRemove(i)}>
                  ✕
                </button>
              </div>
            ))}
            <button type="button" aria-label="Add" onClick={() => onChange([...values, ""])}>
              + Add
            </button>
          </>
        )}
      </Field>
      <ConfirmAction
        open={pending !== null}
        title="Row is not empty. Remove it?"
        message={pending === null ? undefined : `“${values[pending]}” will be removed.`}
        onConfirm={() => {
          if (pending !== null) removeAt(pending);
          setPending(null);
        }}
        onCancel={() => setPending(null)}
      />
    </>
  );
}
