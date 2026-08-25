/**
 * Formats a single series value for the cursor tooltip. Pure, and split out
 * of HistoryChart.tsx (rather than exported alongside the component) so it
 * stays testable in isolation without tripping react-refresh's
 * only-export-components rule on the component file.
 *
 * The unit follows the series' axis: duty is a percentage and a temperature is
 * degrees, and the two are on the same tooltip at the same time. Reading
 * "20.0" beside "225.0" with the same degree mark makes the auger look like a
 * frozen probe.
 */
export function formatTooltipValue(value: number | null, axis: "temp" | "duty" = "temp"): string {
  if (value == null) return "—";
  return axis === "duty" ? `${Math.round(value)}%` : `${value.toFixed(1)}°`;
}
