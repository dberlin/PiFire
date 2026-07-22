export interface RangeProfileColumn {
  key: string;
  label: string;
  suffix?: string;
  min?: number;
  max?: number;
}

function rangeLabel(rowIndex: number, boundaries: number[]): string {
  const n = boundaries.length;
  if (n === 0) return "All";
  if (rowIndex === 0) return `< ${boundaries[0]}°`;
  if (rowIndex === n) return `≥ ${boundaries[n - 1]}°`;
  return `${boundaries[rowIndex - 1]} – ${boundaries[rowIndex] - 1}°`;
}

function clamp(value: number, min: number | undefined, max: number | undefined): number {
  let v = value;
  if (min !== undefined && v < min) v = min;
  if (max !== undefined && v > max) v = max;
  return v;
}

export function RangeProfileTable({
  boundaries,
  profiles,
  columns,
  rangeHeader,
  unit,
  onChange,
}: {
  boundaries: number[];
  profiles: Record<string, number>[];
  columns: RangeProfileColumn[];
  rangeHeader: string;
  unit: string;
  onChange: (boundaries: number[], profiles: Record<string, number>[]) => void;
}) {
  const canRemove = profiles.length > 2;

  const handleBoundaryChange = (rowIndex: number, raw: string) => {
    const next = boundaries.slice();
    next[rowIndex] = Number(raw);
    onChange(next, profiles);
  };

  const handleCellChange = (rowIndex: number, column: RangeProfileColumn, raw: string) => {
    const val = clamp(Number(raw), column.min, column.max);
    const next = profiles.map((row, i) => (i === rowIndex ? { ...row, [column.key]: val } : row));
    onChange(boundaries, next);
  };

  const handleAdd = () => {
    const lastBoundary = boundaries.length > 0 ? boundaries[boundaries.length - 1] : 0;
    const nextBoundaries = [...boundaries, lastBoundary + 10];
    const lastProfile = profiles.length > 0 ? profiles[profiles.length - 1] : {};
    const nextProfiles = [...profiles, { ...lastProfile }];
    onChange(nextBoundaries, nextProfiles);
  };

  const handleRemove = (rowIndex: number) => {
    if (!canRemove) return;
    const boundaryIndex = Math.min(rowIndex, boundaries.length - 1);
    const nextBoundaries = boundaries.filter((_, i) => i !== boundaryIndex);
    const nextProfiles = profiles.filter((_, i) => i !== rowIndex);
    onChange(nextBoundaries, nextProfiles);
  };

  return (
    <div className="pf-rpt-wrap">
      <table className="pf-rpt">
        <thead>
          <tr>
            <th>
              {rangeHeader} ({unit})
            </th>
            {columns.map((col) => (
              <th key={col.key}>
                {col.label}
                {col.suffix ? ` (${col.suffix})` : ""}
              </th>
            ))}
            <th className="pf-rpt-remove-col" />
          </tr>
        </thead>
        <tbody>
          {profiles.map((row, rowIndex) => (
            <tr key={rowIndex}>
              <td className="pf-rpt-range">
                <span className="pf-rpt-range-label">{rangeLabel(rowIndex, boundaries)}</span>
                {rowIndex < boundaries.length && (
                  <input
                    type="number"
                    className="pf-input pf-rpt-boundary-input"
                    aria-label={`Boundary ${rowIndex + 1}`}
                    value={boundaries[rowIndex]}
                    onChange={(e) => handleBoundaryChange(rowIndex, e.target.value)}
                  />
                )}
              </td>
              {columns.map((col) => (
                <td key={col.key} className="pf-rpt-cell">
                  <input
                    type="number"
                    className="pf-input"
                    aria-label={`${col.label} row ${rowIndex + 1}`}
                    min={col.min}
                    max={col.max}
                    value={row[col.key] ?? 0}
                    onChange={(e) => handleCellChange(rowIndex, col, e.target.value)}
                  />
                  {col.suffix && <span className="pf-field-suffix">{col.suffix}</span>}
                </td>
              ))}
              <td className="pf-rpt-remove-col">
                <button
                  type="button"
                  className="pf-rpt-remove"
                  aria-label={`Remove row ${rowIndex + 1}`}
                  disabled={!canRemove}
                  onClick={() => handleRemove(rowIndex)}
                >
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" className="pf-rpt-add" onClick={handleAdd}>
        + Add
      </button>
    </div>
  );
}
