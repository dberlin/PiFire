import { formatTooltipValue } from "./tooltipFormat";

/**
 * Builds one cursor-tooltip row as real DOM nodes (not an HTML string).
 *
 * Exists to close two defects that lived inline in `tooltipPlugin()`'s
 * `setCursor` hook:
 *
 *  - uPlot normalizes every series' `stroke` option to a FUNCTION during
 *    init (`s.stroke = fnOrSelf(s.stroke || null)` in uPlot.esm.js, where
 *    `fnOrSelf` wraps any non-function value as `() => v`). Reading
 *    `u.series[i].stroke` back out and coercing it with `String(...)`
 *    therefore never yields a color -- it yields the wrapper function's
 *    source text (e.g. `"() => v"`), which then landed, invalid, inside a
 *    `style="background:...` attribute. The fix is to never read the color
 *    back out of uPlot's internals: the caller already owns the authoritative
 *    color (`ChartSeries.color` / the derived `SeriesShape`), so it's passed
 *    in here directly.
 *  - `label` is a probe name, which is user-controlled (set in Settings, and
 *    round-trips through the wizard and the settings API) and was being
 *    interpolated unescaped into `innerHTML`. A probe named e.g.
 *    `<img src=x onerror=...>` would execute script in the page. `label` is
 *    set via `Text` node / `textContent` here, never through `innerHTML`, so
 *    HTML in a probe name always renders as literal text.
 *
 * Split out of HistoryChart.tsx (the same way `tooltipFormat.ts` is) so it
 * stays unit-testable without tripping react-refresh's
 * only-export-components rule on the component file.
 */
export function createTooltipRow(
  label: string,
  color: string,
  value: number | null,
  axis: "temp" | "duty" = "temp",
): HTMLDivElement {
  const row = document.createElement("div");
  row.className = "r";

  const swatch = document.createElement("i");
  swatch.style.background = color;
  row.appendChild(swatch);

  row.appendChild(document.createTextNode(label));

  const b = document.createElement("b");
  b.textContent = formatTooltipValue(value, axis);
  row.appendChild(b);

  return row;
}
