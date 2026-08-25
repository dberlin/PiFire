import type { ChartSeries } from "@pifire/core/history/historyAdapter";

export interface SeriesTogglesProps {
  series: ChartSeries[];
  /** Labels the user has explicitly switched on or off, overriding the
   *  server's default for that series. */
  overrides: Record<string, boolean>;
  onToggle: (label: string) => void;
}

/**
 * One chip per series, above the chart.
 *
 * Chips rather than uPlot's built-in legend: the legend is already rendered
 * and already clickable, but nothing about it signals that it is a control,
 * and a 12px text target is not usable on the touchscreen PiFire runs on.
 *
 * It is also what makes a switched-off series reachable at all. `hidden`
 * arrives from the server for two different reasons -- a probe disabled in
 * Settings, and a duty series that stays out of the way until asked for -- and
 * the adapter used to drop both, so a disabled probe's recorded history could
 * not be seen at all. These chips are where that decision now lives.
 */
export function SeriesToggles({ series, overrides, onToggle }: SeriesTogglesProps) {
  // A series with no reading anywhere in the window has nothing to toggle to.
  // The default probe map configures four probes whether or not they are
  // plugged in, so an unused one arrives as an empty dataset -- and a chip for
  // it offers to reveal data that does not exist, while spending vertical
  // space on a page that is already tight at 720p.
  const togglable = series.filter((s) => s.values.some((v) => v !== null));
  if (togglable.length === 0) return null;

  return (
    <div className="pf-chart-toggles">
      {togglable.map((s) => {
        const on = overrides[s.label] ?? s.visible;
        return (
          <button
            key={s.label}
            type="button"
            className="pf-chart-toggle"
            aria-pressed={on}
            onClick={() => onToggle(s.label)}
          >
            {/* The swatch carries the series' own stroke colour so a chip is
                identifiable at a glance against the line it controls. */}
            <span className="pf-chart-toggle-swatch" style={{ background: s.color }} />
            {s.label}
            {s.axis === "duty" && <span className="pf-chart-toggle-unit">%</span>}
          </button>
        );
      })}
    </div>
  );
}
