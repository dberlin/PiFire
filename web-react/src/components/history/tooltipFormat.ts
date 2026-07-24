/**
 * Formats a single series value for the cursor tooltip. Pure, and split out
 * of HistoryChart.tsx (rather than exported alongside the component) so it
 * stays testable in isolation without tripping react-refresh's
 * only-export-components rule on the component file.
 */
export function formatTooltipValue(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(1)}°`;
}
