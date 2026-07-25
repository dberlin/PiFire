import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import type { Page } from "@playwright/test";

// Landmark geometry for the dashboard's authored 1280x720 layout.
//
// The thing pinned here is the LAYOUT, measured in stage-local coordinates --
// not the on-screen pixel size at a 1280x720 browser window. Those differ
// today: the app shell puts a navbar (and sometimes a timer strip or a banner)
// above the dashboard, so useFitScale renders the stage at roughly 0.92 and
// that number moves whenever chrome appears. Dividing every measurement by the
// live scale converts screen pixels back into the authored coordinate space,
// which makes the same baseline meaningful before the reflow (scale ~0.92) and
// after it (scale exactly 1).

export interface Landmark {
  x: number;
  y: number;
  w: number;
  h: number;
  fontSize: string;
  fontWeight: string;
}

export type LandmarkMap = Record<string, Landmark>;

// Every (weight, family) pair the dashboard's landmark text actually uses.
const FACES = [
  "400 16px Barlow",
  "600 12px Barlow",
  "600 13px Barlow",
  "700 20px Barlow",
  "600 22px 'Barlow Semi Condensed'",
  "800 66px 'Barlow Semi Condensed'",
];

/**
 * Block until Barlow is genuinely rendering.
 *
 * index.html pulls Barlow from fonts.googleapis.com with `display=swap`, so
 * text paints in the fallback face the instant the page loads and only
 * re-lays-out when the webfont arrives. Measuring in that window records the
 * fallback's metrics -- which is exactly the flake this harness saw: the
 * header's text-derived widths (brand/status/clock) came out 7-25 px wider on
 * runs where the font was still in flight.
 *
 * `document.fonts.check()` alone cannot detect it: it answers `true` for a
 * family that is not in the font set AT ALL, so "loaded" and "never arrived"
 * are indistinguishable. The canvas probe below is the part that actually
 * proves the webfont is in use -- if Barlow is missing, `Barlow, monospace`
 * and bare `monospace` measure identically.
 */
async function waitForBarlow(page: Page): Promise<void> {
  await page.evaluate(async (faces: string[]) => {
    await Promise.all(faces.map((f) => document.fonts.load(f, "PiFire Grill 0123456789°")));
    await document.fonts.ready;
  }, FACES);
  await page.waitForFunction(() => {
    const ctx = document.createElement("canvas").getContext("2d");
    if (ctx === null) return false;
    const sample = "PiFire Grill 0123456789";
    ctx.font = "700 40px Barlow, monospace";
    const withBarlow = ctx.measureText(sample).width;
    ctx.font = "700 40px monospace";
    return withBarlow !== ctx.measureText(sample).width;
  });
}

export async function measureLandmarks(page: Page): Promise<LandmarkMap> {
  await waitForBarlow(page);
  return (await page.evaluate(() => {
    const stage = document.querySelector<HTMLElement>('[data-pf="stage"]');
    if (stage === null) throw new Error("no [data-pf=stage] on the page");
    const sr = stage.getBoundingClientRect();
    const s = sr.width / 1280;
    const out: Record<string, unknown> = {};
    let n = 0;
    for (const el of document.querySelectorAll<HTMLElement>("[data-pf]")) {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const key = el.dataset.pf;
      if (key === undefined) continue;
      const name = key === "probeCard" ? `probeCard${n++}` : key;
      out[name] = {
        x: Math.round((r.left - sr.left) / s),
        y: Math.round((r.top - sr.top) / s),
        w: Math.round(r.width / s),
        h: Math.round(r.height / s),
        fontSize: cs.fontSize,
        fontWeight: cs.fontWeight,
      };
    }
    return out;
  })) as LandmarkMap;
}

/** The live fit scale, for the record: 1 once the scale transform is gone. */
export async function measureStageScale(page: Page): Promise<number> {
  return page.evaluate(() => {
    const stage = document.querySelector<HTMLElement>('[data-pf="stage"]');
    if (stage === null) throw new Error("no [data-pf=stage] on the page");
    return stage.getBoundingClientRect().width / 1280;
  });
}

// Authored constants: literals in the dashboard source, so a deviation here is
// never a rounding artefact.
const EXACT: Record<string, Partial<Record<"w" | "h", number>>> = {
  stage: { w: 1280, h: 720 },
  header: { h: 58 },
  probeCol: { w: 298 },
  rightCol: { w: 300 },
  controls: { h: 82 },
  cookRow: { h: 52 },
  pills: { h: 64 },
};
const EXACT_TOL = 0.5;
// 2px at 1280 wide is 0.16% of the layout -- below the threshold at which
// flipping two screenshots shows movement. It absorbs the sub-pixel remainder
// of flex distribution and gap rounding and nothing else. A 3px shift is a
// design change and must be argued for.
const BOX_TOL = 2;

export function compareToBaseline(actual: LandmarkMap, baseline: LandmarkMap): string[] {
  const problems: string[] = [];
  for (const name of Object.keys(baseline)) {
    const a = actual[name];
    const b = baseline[name];
    if (a === undefined) {
      problems.push(`${name}: MISSING from the page`);
      continue;
    }
    for (const k of ["x", "y", "w", "h"] as const) {
      const tol = EXACT[name]?.[k as "w" | "h"] !== undefined ? EXACT_TOL : BOX_TOL;
      if (Math.abs(a[k] - b[k]) > tol) {
        problems.push(`${name}.${k}: ${b[k]} -> ${a[k]} (tolerance ${tol})`);
      }
    }
    for (const k of ["fontSize", "fontWeight"] as const) {
      if (a[k] !== b[k]) problems.push(`${name}.${k}: ${b[k]} -> ${a[k]} (must be exact)`);
    }
  }
  for (const name of Object.keys(actual)) {
    if (baseline[name] === undefined) problems.push(`${name}: NEW landmark, not in the baseline`);
  }
  return problems;
}

/** Read the committed baseline, or null on the very first (recording) run. */
export function readBaseline(path: string): LandmarkMap | null {
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, "utf8")) as LandmarkMap;
}

export function writeBaseline(path: string, map: LandmarkMap): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(map, null, 2)}\n`);
}
