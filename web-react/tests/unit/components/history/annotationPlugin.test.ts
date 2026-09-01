import type { HistoryAnnotation } from "@pifire/core/contracts/content";
import { describe, expect, rs, test } from "@rstest/core";
import type uPlot from "uplot";

import { annotationPlugin } from "../../../../src/components/history/annotationPlugin";

// jsdom has no canvas, so the plot is a hand-built stand-in: a ctx of spies, a
// bbox, and a valToPos that maps data-space x straight onto canvas x. That is
// the whole surface the draw hook touches.
function fakePlot(valToPos: (v: number) => number) {
  const ctx = {
    save: rs.fn(),
    restore: rs.fn(),
    beginPath: rs.fn(),
    rect: rs.fn(),
    clip: rs.fn(),
    moveTo: rs.fn(),
    lineTo: rs.fn(),
    stroke: rs.fn(),
    translate: rs.fn(),
    fillText: rs.fn(),
    strokeStyle: "",
    fillStyle: "",
    font: "",
    lineWidth: 0,
    textBaseline: "",
  };
  const plot = {
    ctx,
    bbox: { left: 100, top: 10, width: 400, height: 200 },
    valToPos,
  } as unknown as uPlot;
  return { plot, ctx };
}

function annotation(overrides: Partial<HistoryAnnotation> = {}): HistoryAnnotation {
  return {
    type: "line",
    xMin: 1000,
    xMax: 1000,
    borderColor: "#abc",
    label: { content: "Smoke" },
    ...overrides,
  };
}

function draw(plugin: uPlot.Plugin, plot: uPlot) {
  const hook = plugin.hooks.draw as (u: uPlot) => void;
  hook(plot);
}

describe("annotationPlugin", () => {
  test("strokes one rule per in-range annotation", () => {
    const { plot, ctx } = fakePlot(() => 250);
    draw(annotationPlugin([annotation(), annotation({ xMin: 2000 })]), plot);
    expect(ctx.stroke.mock.calls).toHaveLength(2);
  });

  test("skips an annotation outside the current zoom window", () => {
    //  valToPos maps it left of bbox.left, i.e. scrolled off the plot.
    const { plot, ctx } = fakePlot(() => 5);
    draw(annotationPlugin([annotation()]), plot);
    expect(ctx.stroke.mock.calls).toHaveLength(0);
    expect(ctx.fillText.mock.calls).toHaveLength(0);
  });

  test("draws the caption when the annotation carries one", () => {
    const { plot, ctx } = fakePlot(() => 250);
    draw(annotationPlugin([annotation()]), plot);
    expect(ctx.fillText.mock.calls[0][0]).toBe("Smoke");
  });

  test("skips the caption when there is none", () => {
    const { plot, ctx } = fakePlot(() => 250);
    draw(annotationPlugin([annotation({ label: undefined })]), plot);
    expect(ctx.stroke.mock.calls).toHaveLength(1);
    expect(ctx.fillText.mock.calls).toHaveLength(0);
  });

  test("clips to the plotting area so a marker cannot paint over the axes", () => {
    const { plot, ctx } = fakePlot(() => 250);
    draw(annotationPlugin([annotation()]), plot);
    expect(ctx.rect.mock.calls[0]).toEqual([100, 10, 400, 200]);
    expect(ctx.clip.mock.calls).toHaveLength(1);
  });

  test("leaves the canvas state balanced", () => {
    const { plot, ctx } = fakePlot(() => 250);
    draw(annotationPlugin([annotation(), annotation({ label: undefined })]), plot);
    expect(ctx.save.mock.calls.length).toBe(ctx.restore.mock.calls.length);
  });

  test("an empty list draws nothing but still balances save/restore", () => {
    const { plot, ctx } = fakePlot(() => 250);
    draw(annotationPlugin([]), plot);
    expect(ctx.stroke.mock.calls).toHaveLength(0);
    expect(ctx.save.mock.calls.length).toBe(ctx.restore.mock.calls.length);
  });

  test("reads x from the plot's scale, so markers follow a drag-zoom", () => {
    const valToPos = rs.fn(() => 250);
    const { plot } = fakePlot(valToPos as unknown as (v: number) => number);
    draw(annotationPlugin([annotation({ xMin: 1784942370.612 })]), plot);
    expect(valToPos.mock.calls[0]).toEqual([1784942370.612, "x", true]);
  });
});
