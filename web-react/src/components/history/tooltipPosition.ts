export interface TooltipPosition {
  left: number;
  top: number;
}

/**
 * Pure placement math for HistoryChart's cursor-tracking tooltip.
 *
 * Split out of tooltipPlugin's `setCursor` hook so the flip/clamp
 * arithmetic is directly unit-testable: jsdom has no layout engine, so
 * `element.offsetWidth`/`offsetHeight` (and `clientWidth`/`clientHeight`)
 * are always 0 in tests. That means the *measured* code path -- the real
 * bug surface for a tooltip that overflows the plot's edges -- can never be
 * exercised by rendering the component in jsdom; only the `|| 150` / `|| 0`
 * fallbacks are reachable that way. Extracting the math lets a test pass
 * realistic tip/container dimensions directly, independent of jsdom.
 *
 * Flips horizontally near the right edge and vertically near the bottom
 * edge (measure-then-flip, same pattern both axes), and clamps the final
 * top to stay within [4, containerHeight - tipHeight] so a tooltip taller
 * than the plot (many probes, a small `height` prop) can't be placed so
 * the flipped-up position still overflows the bottom edge.
 */
export function computeTooltipPosition(
  cursorLeft: number,
  cursorTop: number,
  tipWidth: number,
  tipHeight: number,
  containerWidth: number,
  containerHeight: number,
): TooltipPosition {
  const flipX = cursorLeft > containerWidth - tipWidth - 8;
  const left = flipX ? cursorLeft - tipWidth - 6 : cursorLeft + 12;

  const rawTop = cursorTop - 10;
  const flipY = rawTop + tipHeight > containerHeight;
  const flippedTop = flipY ? cursorTop - tipHeight - 10 : rawTop;

  const topFloor = 4;
  // If the tooltip is taller than the container, containerHeight - tipHeight
  // goes negative; floor it at topFloor too, so the bottom bound never pulls
  // the result below the top bound.
  const bottomBound = Math.max(topFloor, containerHeight - tipHeight);
  const top = Math.min(Math.max(topFloor, flippedTop), bottomBound);

  return { left, top };
}
