import { describe, expect, it } from "@rstest/core";
import type uPlot from "uplot";
import { tooltipPlugin } from "./tooltipPlugin";

describe("tooltipPlugin", () => {
  it("drives real DOM through init + setCursor: swatch color comes from config, an HTML-looking label renders as literal text with no element created from it", () => {
    // Regression pin for MINOR-4: the three createTooltipRow tests in
    // HistoryChart.test.tsx pin that helper in isolation, but nothing
    // confirmed tooltipPlugin's setCursor hook actually calls it with the
    // right label/color/value. A regression reverting setCursor to inline
    // innerHTML string-building would go undetected while those stayed
    // green. This drives a stub uPlot-shaped object -- not a real uPlot
    // instance -- through the plugin's own hooks.init/hooks.setCursor.
    const seriesShape = [
      { label: "Grill", color: "#ff7a1a" },
      { label: '<img src=x onerror="window.__pwned = true">', color: "#4dc9ff" },
    ];
    const plugin = tooltipPlugin(seriesShape);

    const over = document.createElement("div");
    // jsdom has no layout engine, so clientWidth/clientHeight (and the
    // offsetWidth/offsetHeight the plugin reads off its own tooltip
    // element) are always 0 here. That's fine: this test pins the DOM
    // *content* setCursor builds, not the measured flip/clamp placement
    // math (covered separately, with real dimensions, in
    // tooltipPosition.test.ts).
    const stubU = {
      over,
      cursor: { idx: 1, left: 20, top: 20 },
      data: [
        [1000, 2000, 3000],
        [200, 210, 220],
        [80, 90, 100],
      ],
    } as unknown as uPlot;

    const init = plugin.hooks.init;
    if (typeof init !== "function")
      throw new Error("expected tooltipPlugin's init hook to be a function");
    init(stubU, {} as unknown as uPlot.Options, [[1000, 2000, 3000]]);

    const tip = over.querySelector(".pf-history-tip");
    expect(tip).not.toBeNull();

    const setCursor = plugin.hooks.setCursor;
    if (typeof setCursor !== "function") {
      throw new Error("expected tooltipPlugin's setCursor hook to be a function");
    }
    setCursor(stubU);

    const rows = tip?.querySelectorAll(".r") ?? [];
    expect(rows.length).toBe(2);

    const swatches = tip?.querySelectorAll(".r i") ?? [];
    expect((swatches[0] as HTMLElement).style.background).toBe("rgb(255, 122, 26)");
    expect((swatches[1] as HTMLElement).style.background).toBe("rgb(77, 201, 255)");

    // The second series' label contains HTML. It must show up as literal
    // text, and must never have been parsed into a real element -- that's
    // exactly what an innerHTML-string regression would do.
    expect(tip?.textContent).toContain(seriesShape[1].label);
    expect(tip?.querySelector("img")).toBeNull();
  });

  it("hides the tooltip and removes it from the DOM on destroy", () => {
    const plugin = tooltipPlugin([{ label: "Grill", color: "#ff7a1a" }]);
    const over = document.createElement("div");
    const stubU = { over } as unknown as uPlot;

    const init = plugin.hooks.init;
    if (typeof init !== "function")
      throw new Error("expected tooltipPlugin's init hook to be a function");
    init(stubU, {} as unknown as uPlot.Options, [[1000]]);
    expect(over.querySelector(".pf-history-tip")).not.toBeNull();

    const destroy = plugin.hooks.destroy;
    if (typeof destroy !== "function")
      throw new Error("expected tooltipPlugin's destroy hook to be a function");
    destroy(stubU);
    expect(over.querySelector(".pf-history-tip")).toBeNull();
  });

  it("hides the tooltip instead of rendering rows when the cursor leaves the plot (idx is null)", () => {
    const plugin = tooltipPlugin([{ label: "Grill", color: "#ff7a1a" }]);
    const over = document.createElement("div");
    const stubU = {
      over,
      cursor: { idx: null, left: -1, top: 0 },
      data: [[1000], [200]],
    } as unknown as uPlot;

    const init = plugin.hooks.init;
    if (typeof init !== "function")
      throw new Error("expected tooltipPlugin's init hook to be a function");
    init(stubU, {} as unknown as uPlot.Options, [[1000]]);

    const setCursor = plugin.hooks.setCursor;
    if (typeof setCursor !== "function") {
      throw new Error("expected tooltipPlugin's setCursor hook to be a function");
    }
    setCursor(stubU);

    const tip = over.querySelector(".pf-history-tip") as HTMLElement | null;
    expect(tip?.style.display).toBe("none");
  });
});
