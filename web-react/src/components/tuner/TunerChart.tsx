import type { Coefficients } from "../../helpers/contracts/operations.gen";

import "./tuner.css";

const WIDTH = 320;
const HEIGHT = 200;
const PAD = 8;

/**
 * The Temp (x) vs Tr (y) curve calc_shh_chart returns.
 *
 * An inline SVG polyline, not uPlot: twenty points need no charting library,
 * and -- unlike a canvas -- every plotted coordinate stays readable from the
 * DOM, so the y inversion below is provable in a unit test rather than hidden.
 *
 * SVG's y axis grows downward, so the LARGEST resistance must map to the
 * SMALLEST y to sit at the top of the box. Plotting y directly renders a
 * plausible-looking but upside-down curve, which is why that inversion has its
 * own test.
 */
export function TunerChart({ chart, chartOk }: { chart: Coefficients["chart"]; chartOk: boolean }) {
  if (!chartOk || chart.length === 0) {
    return (
      <p className="pf-tuner-chart-empty" role="status">
        The curve could not be plotted from these coefficients.
      </p>
    );
  }

  const xs = chart.map((p) => p.x);
  const ys = chart.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;

  const plotW = WIDTH - 2 * PAD;
  const plotH = HEIGHT - 2 * PAD;

  const points = chart
    .map((p) => {
      const px = PAD + ((p.x - minX) / spanX) * plotW;
      //  Inverted: the largest ohms value gets the smallest y, so the curve
      //  reads high-resistance-at-top like every thermistor chart.
      const py = PAD + ((maxY - p.y) / spanY) * plotH;
      return `${px.toFixed(1)},${py.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      className="pf-tuner-chart"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label="Resistance against temperature"
      preserveAspectRatio="none"
    >
      <polyline className="pf-tuner-chart-line" points={points} fill="none" />
    </svg>
  );
}
