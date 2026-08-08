import { Field } from "./Field";

export function TextField({
  label,
  value,
  onChange,
  hint,
  error = null,
  path,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  hint?: string;
  error?: string | null;
  path?: string;
}) {
  return (
    <Field label={label} hint={hint} error={error} path={path}>
      {({ id, describedBy, invalid }) => (
        <input
          id={id}
          className="pf-input"
          type="text"
          value={value}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </Field>
  );
}
