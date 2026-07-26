// Ported from blueprints/pellets/routes.py:207-211. Same constants, same
// thresholds (`pounds > 1`, `grams < 1000`), same two-decimal rounding.
//
// Deliberate divergence: Python's str(round(1.0, 2)) is "1.0"; this prints
// "1". Reimplementing CPython float repr in TS to win a trailing zero is not
// worth it -- usage.test.ts pins the difference so it stays a decision.
function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

export function formatUsage(grams: number): { imperial: string; metric: string } {
  const pounds = round2(grams * 0.00220462);
  const ounces = round2(grams * 0.03527392);
  return {
    imperial: pounds > 1 ? `${pounds} lbs` : `${ounces} ozs`,
    metric: grams < 1000 ? `${round2(grams)} g` : `${round2(grams / 1000)} kg`,
  };
}
