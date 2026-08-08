import { useEffect, useState } from "react";
import { clampToBounds } from "../../helpers/settings/bounds";
import { useSettingsFieldErrors } from "../../helpers/settings/fieldErrorContext";

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

/**
 * The strictly-monotonic window a single boundary may occupy.
 *
 * The list must stay sorted: neither PwmSettings._check_profiles nor
 * SmartStart._check_profile_count checks ordering, so an inverted
 * temp_range_list SAVES successfully and then
 * controller/runtime/logic/smartstart.py:8-12 silently makes every profile
 * after the inversion unreachable. Flask guards this in settings.js:195-244.
 *
 * Either end may be undefined: the first boundary's floor and the last one's
 * ceiling come from the optional boundaryMin/boundaryMax props.
 */
function boundaryLimits(
  index: number,
  boundaries: number[],
  boundaryMin: number | undefined,
  boundaryMax: number | undefined,
): { lower: number | undefined; upper: number | undefined } {
  return {
    lower: index > 0 ? boundaries[index - 1] + 1 : boundaryMin,
    upper: index < boundaries.length - 1 ? boundaries[index + 1] - 1 : boundaryMax,
  };
}

function limitsText(lower: number | undefined, upper: number | undefined): string {
  if (lower !== undefined && upper !== undefined) return `between ${lower} and ${upper}`;
  if (lower !== undefined) return `at least ${lower}`;
  if (upper !== undefined) return `at most ${upper}`;
  return "a number";
}

export function RangeProfileTable({
  boundaries,
  profiles,
  columns,
  rangeHeader,
  unit,
  onChange,
  boundaryMin,
  boundaryMax,
  disabled = false,
  path,
}: {
  boundaries: number[];
  profiles: Record<string, number>[];
  columns: RangeProfileColumn[];
  rangeHeader: string;
  unit: string;
  onChange: (boundaries: number[], profiles: Record<string, number>[]) => void;
  boundaryMin?: number;
  boundaryMax?: number;
  /** Render read-only. The rows still show their values: the settings they
   *  describe are unreachable, not unset. */
  disabled?: boolean;
  /** Dotted settings path of the cross-field check covering this table's
   *  array pair (e.g. "startup.smartstart") -- NOT a single cell's path.
   *  A per-cell bound violation carries its own deeper path and is left to
   *  the save bar; this only claims the whole-table check. */
  path?: string;
}) {
  const ctx = useSettingsFieldErrors();
  // Claim on MOUNT, matching Field: the claimed set describes what has a slot
  // on screen, not this save's outcome.
  useEffect(() => {
    if (!path || !ctx) return;
    return ctx.claim(path);
  }, [path, ctx]);
  const tableError =
    path && ctx ? (ctx.errors.find((e) => e.path === path)?.message ?? null) : null;
  const canRemove = !disabled && profiles.length > 2;
  // An out-of-order keystroke is held here instead of being pushed up, so
  // `onChange` is never called with an unsorted array while multi-digit entry
  // still works ("9" on its way to "95" is momentarily out of range).
  const [draft, setDraft] = useState<{ index: number; text: string } | null>(null);
  const [boundaryError, setBoundaryError] = useState<string | null>(null);

  const emitBoundary = (rowIndex: number, value: number) => {
    const next = boundaries.slice();
    next[rowIndex] = value;
    onChange(next, profiles);
  };

  const handleBoundaryChange = (rowIndex: number, raw: string) => {
    const { lower, upper } = boundaryLimits(rowIndex, boundaries, boundaryMin, boundaryMax);
    const typed = Number(raw);
    if (raw !== "" && !Number.isNaN(typed) && clampToBounds(typed, lower, upper) === typed) {
      setDraft(null);
      setBoundaryError(null);
      emitBoundary(rowIndex, typed);
      return;
    }
    setDraft({ index: rowIndex, text: raw });
    setBoundaryError(`Range boundary ${rowIndex + 1} must be ${limitsText(lower, upper)}.`);
  };

  // Clamp when the edit is FINISHED, matching NumberField: clamping on change
  // would make a bounded field untypeable.
  const handleBoundaryBlur = (rowIndex: number) => {
    if (draft?.index !== rowIndex) return;
    const { lower, upper } = boundaryLimits(rowIndex, boundaries, boundaryMin, boundaryMax);
    const typed = Number(draft.text);
    const clamped = clampToBounds(
      draft.text === "" || Number.isNaN(typed) ? boundaries[rowIndex] : typed,
      lower,
      upper,
    );
    setDraft(null);
    setBoundaryError(null);
    if (clamped !== boundaries[rowIndex]) emitBoundary(rowIndex, clamped);
  };

  const handleCellChange = (rowIndex: number, column: RangeProfileColumn, raw: string) => {
    const val = clampToBounds(Number(raw), column.min, column.max);
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
                {rowIndex < boundaries.length &&
                  (() => {
                    const { lower, upper } = boundaryLimits(
                      rowIndex,
                      boundaries,
                      boundaryMin,
                      boundaryMax,
                    );
                    return (
                      <input
                        type="number"
                        className="pf-input pf-rpt-boundary-input"
                        aria-label={`Boundary ${rowIndex + 1}`}
                        min={lower}
                        max={upper}
                        disabled={disabled}
                        value={draft?.index === rowIndex ? draft.text : boundaries[rowIndex]}
                        onChange={(e) => handleBoundaryChange(rowIndex, e.target.value)}
                        onBlur={() => handleBoundaryBlur(rowIndex)}
                      />
                    );
                  })()}
              </td>
              {columns.map((col) => (
                <td key={col.key} className="pf-rpt-cell">
                  <input
                    type="number"
                    className="pf-input"
                    aria-label={`${col.label} row ${rowIndex + 1}`}
                    min={col.min}
                    max={col.max}
                    disabled={disabled}
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
      {boundaryError && (
        <p className="pf-settings-error-text" role="alert">
          {boundaryError}
        </p>
      )}
      {tableError && (
        <span className="pf-field-error" role="alert">
          {tableError}
        </span>
      )}
      <button type="button" className="pf-rpt-add" disabled={disabled} onClick={handleAdd}>
        + Add
      </button>
    </div>
  );
}
