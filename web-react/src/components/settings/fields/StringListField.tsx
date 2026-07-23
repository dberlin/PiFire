export function StringListField({
  label,
  values,
  onChange,
}: {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div className="pf-field pf-field-column">
      <span className="pf-field-label">{label}</span>
      {values.map((v, i) => (
        <div className="pf-stringlist-row" key={i}>
          <input
            className="pf-input"
            type="text"
            value={v}
            onChange={(e) => onChange(values.map((x, j) => (j === i ? e.target.value : x)))}
          />
          <button
            type="button"
            aria-label="Remove"
            onClick={() => onChange(values.filter((_, j) => j !== i))}
          >
            ✕
          </button>
        </div>
      ))}
      <button type="button" aria-label="Add" onClick={() => onChange([...values, ""])}>
        + Add
      </button>
    </div>
  );
}
