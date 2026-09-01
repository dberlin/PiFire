import { describe, expect, it } from "@rstest/core";

import { computeTooltipPosition } from "../../../../src/components/history/tooltipPosition";

describe("computeTooltipPosition", () => {
  it("places the tooltip to the right of and below the cursor by default", () => {
    const pos = computeTooltipPosition(100, 100, 150, 60, 800, 400);
    expect(pos).toEqual({ left: 112, top: 90 });
  });

  it("flips horizontally near the right edge so the tooltip stays on screen", () => {
    const pos = computeTooltipPosition(780, 100, 150, 60, 800, 400);
    expect(pos.left).toBe(780 - 150 - 6);
  });

  it("flips vertically near the bottom edge", () => {
    const pos = computeTooltipPosition(100, 390, 150, 60, 800, 400);
    // rawTop = 380; 380 + 60 = 440 > containerHeight (400) -> flip up.
    expect(pos.top).toBe(390 - 60 - 10);
  });

  it("keeps the existing top floor of 4 near the top edge", () => {
    const pos = computeTooltipPosition(50, 0, 100, 40, 400, 200);
    expect(pos.top).toBe(4);
  });

  it("stays fully inside the container when the tooltip fits below the cursor near the bottom", () => {
    const pos = computeTooltipPosition(50, 150, 100, 40, 400, 200);
    expect(pos.top + 40).toBeLessThanOrEqual(200);
  });

  it("MINOR-3 regression: clamps to the bottom edge when the flipped-up position alone would still overflow", () => {
    // cursorTop can land beyond the container's bottom edge in practice
    // (rounding, a fast pointer move right at the edge). The pre-fix
    // formula only floor-clamped (Math.max(4, flippedTop)) with no matching
    // bound against the container's bottom edge, so a positive flippedTop
    // was used as-is even when flippedTop + tipHeight overflowed.
    const pos = computeTooltipPosition(50, 215, 100, 50, 400, 200);
    expect(pos.top).toBe(150); // 200 - 50: flush with the bottom edge
    expect(pos.top + 50).toBeLessThanOrEqual(200);
  });

  it("MINOR-3 regression: a tooltip taller than the whole plot doesn't push top negative or throw", () => {
    // Many probes + a small `height` prop: tipHeight > containerHeight.
    // Overflow can't be fully avoided (the content is literally too tall),
    // but the result must still be a sane, non-negative top pinned as high
    // as possible, not an unbounded value.
    expect(() => computeTooltipPosition(50, 50, 100, 250, 400, 200)).not.toThrow();
    const pos = computeTooltipPosition(50, 50, 100, 250, 400, 200);
    expect(pos.top).toBe(4);
  });
});
