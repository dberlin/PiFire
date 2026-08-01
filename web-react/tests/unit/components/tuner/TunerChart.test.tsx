import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { TunerChart } from "../../../../src/components/tuner/TunerChart";

const CHART = [
  { x: 0, y: 100000 },
  { x: 110, y: 20000 },
  { x: 220, y: 1000 },
];

function polylinePoints(container: HTMLElement): [number, number][] {
  const raw = container.querySelector("polyline")?.getAttribute("points") ?? "";
  return raw
    .trim()
    .split(/\s+/)
    .map((p) => {
      const [x, y] = p.split(",").map(Number);
      return [x, y] as [number, number];
    });
}

describe("TunerChart", () => {
  it("draws one polyline point per sample", () => {
    //  SVG rather than uPlot: 20 points need no library, and every coordinate
    //  is readable from the DOM here -- a canvas chart is unassertable in
    //  jsdom without a stub that would make this test meaningless.
    const { container } = render(<TunerChart chart={CHART} chartOk />);
    expect(polylinePoints(container)).toHaveLength(3);
  });

  it("puts the lowest temperature on the left and the highest on the right", () => {
    const { container } = render(<TunerChart chart={CHART} chartOk />);
    const xs = polylinePoints(container).map(([x]) => x);
    expect(xs[0]).toBeLessThan(xs[xs.length - 1]);
  });

  it("puts the highest resistance at the top", () => {
    //  SVG y grows downward, so the largest ohms value must have the SMALLEST
    //  y. Getting this backwards renders a plausible-looking inverted curve.
    const { container } = render(<TunerChart chart={CHART} chartOk />);
    const ys = polylinePoints(container).map(([, y]) => y);
    expect(ys[0]).toBeLessThan(ys[ys.length - 1]);
  });

  it("says the curve could not be drawn rather than drawing nothing", () => {
    render(<TunerChart chart={[]} chartOk={false} />);
    expect(screen.getByRole("status")).toHaveTextContent(/could not be plotted/i);
  });

  it("labels itself for assistive technology", () => {
    render(<TunerChart chart={CHART} chartOk />);
    expect(screen.getByRole("img", { name: /resistance/i })).toBeInTheDocument();
  });
});
