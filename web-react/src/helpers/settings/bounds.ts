/**
 * Clamp a number into an optional `[min, max]` range.
 *
 * Lives here rather than beside a component because
 * `react-refresh/only-export-components` forbids non-component exports from a
 * component module.
 *
 * NaN is returned unchanged on purpose: an empty `<input type="number">`
 * parses to NaN, and inventing a bound for it would silently rewrite a field
 * the user is still clearing. The caller decides what an unparseable value
 * means.
 */
export function clampToBounds(value: number, min?: number, max?: number): number {
  if (Number.isNaN(value)) return value;
  let v = value;
  if (min !== undefined && v < min) v = min;
  if (max !== undefined && v > max) v = max;
  return v;
}
