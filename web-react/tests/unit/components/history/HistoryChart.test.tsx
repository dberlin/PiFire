import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";
import type uPlot from "uplot";
import { formatTooltipValue } from "../../../../src/components/history/tooltipFormat";
import { createTooltipRow } from "../../../../src/components/history/tooltipRow";
import type { HistoryAnnotation } from "../../../../src/helpers/contracts/content.gen";

// uPlot reads `plugins` only when a plot is CONSTRUCTED, so "was the annotation
// plugin installed" is not observable from the DOM -- the factory call is the
// seam. Mocking it also keeps these tests off a real canvas.
const annotationPluginMock = rs.fn(
  (_annotations: HistoryAnnotation[]) => ({ hooks: {} }) as uPlot.Plugin,
);
rs.mock("../../../../src/components/history/annotationPlugin", () => ({
  annotationPlugin: (annotations: HistoryAnnotation[]) => annotationPluginMock(annotations),
}));

const { HistoryChart } = await import("../../../../src/components/history/HistoryChart");

afterEach(cleanup);
beforeEach(() => {
  annotationPluginMock.mockClear();
});

const times = [1, 2, 3, 4, 5];
const series = [
  { label: "Grill", color: "#ff7a1a", values: [200, 210, 220, 225, 224] },
  { label: "Probe 1", color: "#4dc9ff", values: [80, 90, 100, 110, 120] },
];

describe("HistoryChart", () => {
  it("mounts a chart container", () => {
    const { container } = render(<HistoryChart times={times} series={series} />);
    expect(container.querySelector(".pf-history-chart")).toBeInTheDocument();
  });

  it("renders without throwing when there is no data yet", () => {
    expect(() => render(<HistoryChart times={[]} series={[]} />)).not.toThrow();
  });

  it("survives a data update (re-render with new points)", () => {
    const { rerender, container } = render(<HistoryChart times={times} series={series} />);
    expect(() =>
      rerender(
        <HistoryChart
          times={[...times, 6]}
          series={series.map((s) => ({ ...s, values: [...s.values, 230] }))}
        />,
      ),
    ).not.toThrow();
    expect(container.querySelector(".pf-history-chart")).toBeInTheDocument();
  });

  it("keeps showing the chart's legend for the new shape after a series is added, not an empty one", () => {
    // Regression pin for the merged rebuild/setData effect: a re-render
    // with a CHANGED series shape (a probe added mid-cook is a real case,
    // not a hypothetical) must still trigger a rebuild. Before the merge,
    // that depended on a no-deps "mirror" effect and a shape-only-deps
    // rebuild effect staying adjacent and in declaration order -- nothing
    // enforced that, and a reordering would have silently left the chart
    // showing the OLD shape (or empty).
    const { rerender, container } = render(<HistoryChart times={times} series={series} />);
    const before = container.querySelectorAll(".u-legend .u-series").length;
    expect(before).toBeGreaterThan(0);

    const withExtraProbe = [
      ...series,
      { label: "Probe 2", color: "#ffab00", values: [70, 75, 80, 85, 90] },
    ];
    rerender(<HistoryChart times={times} series={withExtraProbe} />);

    const after = container.querySelectorAll(".u-legend .u-series").length;
    expect(after).toBe(before + 1);
    expect(container.querySelector(".pf-history-chart")).toBeInTheDocument();
  });

  it("cleans up on unmount", () => {
    const { unmount, container } = render(<HistoryChart times={times} series={series} />);
    unmount();
    expect(container.querySelector(".pf-history-chart")).not.toBeInTheDocument();
  });

  it("creates the tooltip element on init and removes it from the DOM on unmount", () => {
    const { unmount } = render(<HistoryChart times={times} series={series} />);
    const tip = document.querySelector(".pf-history-tip");
    expect(tip).toBeInTheDocument();

    unmount();
    expect(document.querySelector(".pf-history-tip")).not.toBeInTheDocument();
  });
});

describe("createTooltipRow", () => {
  it("colors the swatch with the configured probe color, not a stringified stroke function", () => {
    // Regression pin: uPlot normalizes every series' `stroke` option to a
    // function at init time, so reading it back with `String(s.stroke)` (the
    // old implementation) yields the function's source text -- e.g.
    // "() => v" -- not a color. The swatch must come from the color the
    // caller passed in, and must never contain JS source text.
    const row = createTooltipRow("Probe 1", "#4dc9ff", 100);
    const swatch = row.querySelector("i");
    expect(swatch).not.toBeNull();
    expect(swatch?.style.background).toBe("rgb(77, 201, 255)");
    expect(swatch?.style.background).not.toContain("=>");
    expect(swatch?.style.background).not.toContain("function");
  });

  it("renders a probe label containing HTML as literal text, not markup", () => {
    // Regression pin: probe labels are user-controlled (set in Settings) and
    // must never be interpreted as HTML when shown in the tooltip.
    const maliciousLabel = '<img src=x onerror="window.__pwned = true">';
    const row = createTooltipRow(maliciousLabel, "#ff7a1a", 224.36);
    expect(row.textContent).toContain(maliciousLabel);
    expect(row.querySelector("img")).toBeNull();
  });

  it("keeps the DOM structure historyChart.css targets", () => {
    const row = createTooltipRow("Grill", "#ff7a1a", 200);
    expect(row.className).toBe("r");
    expect(row.querySelector("i")).not.toBeNull();
    expect(row.querySelector("b")).not.toBeNull();
    expect(row.querySelector("b")?.textContent).toBe("200.0°");
  });
});

describe("formatTooltipValue", () => {
  it("renders an em-dash placeholder for a null value (a probe that dropped out)", () => {
    expect(formatTooltipValue(null)).toBe("—");
  });

  it("renders a numeric value rounded to one decimal with a degree sign", () => {
    expect(formatTooltipValue(224.36)).toBe("224.4°");
  });

  it("installs no annotation plugin when the prop is absent -- /history is unchanged", () => {
    render(<HistoryChart times={times} series={series} />);
    expect(annotationPluginMock).not.toHaveBeenCalled();
  });

  it("installs the annotation plugin when annotations are supplied", () => {
    const annotations: HistoryAnnotation[] = [
      { type: "line", xMin: 2, xMax: 2, borderColor: "#abc", label: { content: "Smoke" } },
    ];
    render(<HistoryChart times={times} series={series} annotations={annotations} />);
    expect(annotationPluginMock.mock.calls[0][0]).toEqual(annotations);
  });

  it("rebuilds the plot when the annotations change", () => {
    // The rebuild is the point: the plugin list is frozen at construction, so
    // toggling markers off has to destroy and rebuild, not just setData.
    const first: HistoryAnnotation[] = [{ type: "line", xMin: 2, xMax: 2, borderColor: "#abc" }];
    const second: HistoryAnnotation[] = [{ type: "line", xMin: 3, xMax: 3, borderColor: "#def" }];
    const { rerender } = render(<HistoryChart times={times} series={series} annotations={first} />);
    expect(annotationPluginMock.mock.calls).toHaveLength(1);

    rerender(<HistoryChart times={times} series={series} annotations={second} />);
    expect(annotationPluginMock.mock.calls).toHaveLength(2);
    expect(annotationPluginMock.mock.calls[1][0]).toEqual(second);
  });

  it("does not rebuild on a plain data tick with unchanged annotations", () => {
    const annotations: HistoryAnnotation[] = [
      { type: "line", xMin: 2, xMax: 2, borderColor: "#abc" },
    ];
    const { rerender } = render(
      <HistoryChart times={times} series={series} annotations={annotations} />,
    );
    expect(annotationPluginMock.mock.calls).toHaveLength(1);

    rerender(
      <HistoryChart
        times={[...times, 6]}
        series={series.map((s) => ({ ...s, values: [...s.values, 230] }))}
        annotations={[{ type: "line", xMin: 2, xMax: 2, borderColor: "#abc" }]}
      />,
    );
    expect(annotationPluginMock.mock.calls).toHaveLength(1);
  });
});
