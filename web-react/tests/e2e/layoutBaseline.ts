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
  /** Computed visual style. Absent on the legacy [data-pf] dashboard baseline,
   *  which predates it; compareToBaseline skips the check when the BASELINE
   *  omits it, so that file stays valid without regeneration. */
  styles?: Record<string, string>;
}

// What "visually identical" actually means, beyond a box in the right place.
//
// The geometry above is blind to colour, border, radius, shadow, padding, gap
// and flex direction -- which is the entire set of declarations an @apply
// rewrite touches. A `.pf-card { @apply bg-inset }` where the rule said
// var(--card) moves nothing and passes a geometry-only gate in silence. These
// are compared EXACTLY: both sides run in the same Chromium, so there is no
// rounding to absorb.
export const STYLE_PROPS = [
  "color",
  "background-color",
  "background-image",
  "border-top-width",
  "border-top-color",
  "border-top-style",
  "border-top-left-radius",
  "box-shadow",
  "opacity",
  "font-family",
  "line-height",
  "letter-spacing",
  "text-transform",
  "text-align",
  "padding-top",
  "padding-right",
  "padding-bottom",
  "padding-left",
  "margin-top",
  "margin-right",
  "margin-bottom",
  "margin-left",
  "display",
  "flex-direction",
  "flex-wrap",
  "gap",
  "justify-content",
  "align-items",
  "position",
  "z-index",
  "overflow",
  "transform",
] as const;

export type LandmarkMap = Record<string, Landmark>;

// Every (weight, family) pair index.html's <link> actually requests: Barlow
// 400;500;600;700 and Barlow Semi Condensed 500;600;700;800.
//
// The size in a font shorthand does not select a face -- family, weight and
// style do -- so a single nominal 16px covers every size the app renders at,
// and loading a superset of any one page's faces is harmless: it can make the
// wait longer, never the measurement different. The previous list was the
// dashboard's six exact pairs, which would have under-waited on the settings
// and wizard surfaces this harness now also measures.
const FACES = [
  "400 16px Barlow",
  "500 16px Barlow",
  "600 16px Barlow",
  "700 16px Barlow",
  "500 16px 'Barlow Semi Condensed'",
  "600 16px 'Barlow Semi Condensed'",
  "700 16px 'Barlow Semi Condensed'",
  "800 16px 'Barlow Semi Condensed'",
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
export async function waitForBarlow(page: Page): Promise<void> {
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

export type ExactTable = Record<string, Partial<Record<"w" | "h", number>>>;

// Authored constants: literals in the dashboard source, so a deviation here is
// never a rounding artefact. Keyed by data-pf name, which is why it is the
// DASHBOARD table specifically -- a selector-keyed page passes its own via
// PageSpec.exact, and must not inherit these by accident (".pf-nav" has no
// business being held to the stage's 1280px).
export const DASHBOARD_EXACT: ExactTable = {
  stage: { w: 1280, h: 720 },
  header: { h: 58 },
  probeCol: { w: 298 },
  rightCol: { w: 300 },
  controls: { h: 82 },
  cookRow: { h: 52 },
  pills: { h: 64 },
};
export const EXACT_TOL = 0.5;
// 2px at 1280 wide is 0.16% of the layout -- below the threshold at which
// flipping two screenshots shows movement. It absorbs the sub-pixel remainder
// of flex distribution and gap rounding and nothing else. A 3px shift is a
// design change and must be argued for.
export const BOX_TOL = 2;

/** Does `name` -- a baseline key, which is a selector or `selector#i` -- name
 *  an entry declared text-sized by its PageSpec? */
function isTextSized(name: string, textSized: string[]): boolean {
  return textSized.some((sel) => name === sel || name.startsWith(`${sel}#`));
}

export function compareToBaseline(
  actual: LandmarkMap,
  baseline: LandmarkMap,
  exact: ExactTable = DASHBOARD_EXACT,
  textSized: string[] = [],
): string[] {
  const problems: string[] = [];
  for (const name of Object.keys(baseline)) {
    const a = actual[name];
    const b = baseline[name];
    if (a === undefined) {
      problems.push(`${name}: MISSING from the page`);
      continue;
    }
    // A text-sized entry keeps every other assertion -- where it starts, its
    // face and weight, and all of STYLE_PROPS. Only the two numbers that are
    // its string's own extent go uncompared. See PageSpec.textSized.
    const boxKeys = isTextSized(name, textSized)
      ? (["x", "y"] as const)
      : (["x", "y", "w", "h"] as const);
    for (const k of boxKeys) {
      const tol = exact[name]?.[k as "w" | "h"] !== undefined ? EXACT_TOL : BOX_TOL;
      if (Math.abs(a[k] - b[k]) > tol) {
        problems.push(`${name}.${k}: ${b[k]} -> ${a[k]} (tolerance ${tol})`);
      }
    }
    for (const k of ["fontSize", "fontWeight"] as const) {
      if (a[k] !== b[k]) problems.push(`${name}.${k}: ${b[k]} -> ${a[k]} (must be exact)`);
    }
    // Driven off the BASELINE's keys, not STYLE_PROPS: a baseline recorded
    // before a property was added to STYLE_PROPS stays comparable on the
    // properties it does carry, instead of failing on every landmark at once.
    for (const [prop, want] of Object.entries(b.styles ?? {})) {
      const got = a.styles?.[prop];
      if (got !== want) problems.push(`${name} { ${prop}: ${want} } -> ${got} (must be exact)`);
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

/** One measurable surface. tests/e2e/pageSpecs.ts holds the catalogue. */
export interface PageSpec {
  /** Baseline file stem, e.g. "settings-general". */
  name: string;
  /** Route to open, relative to the project's baseURL. */
  path: string;
  /** Playwright selector -- MAY use the text engines -- that must be visible
   *  before anything is measured. */
  ready: string;
  /** PLAIN CSS. The measurement origin; every landmark is recorded relative to
   *  this box, so a page that scrolls does not shift its whole baseline. */
  root: string;
  /** PLAIN CSS. Each match becomes one baseline entry. */
  landmarks: string[];
  /** Playwright selectors clicked in order, after `ready`, before measuring.
   *  How the wizard reaches step N. */
  clicks?: string[];
  /** Fixture routes only THIS spec needs, installed on top of stubApi()'s
   *  shared set. The cook-file specs use it: /history is measured by two specs
   *  and the older one's baseline is immutable, so their listing fixture must
   *  not reach it. */
  stubs?: (page: Page) => Promise<void>;
  /** Dimensions that must not move at all, keyed by baseline entry name. */
  exact?: ExactTable;
  /** Landmarks whose w/h are the extent of their own TEXT, where that text is
   *  live data rather than layout. Their boxes are still recorded, and their
   *  position, face, weight and every computed style are still compared -- only
   *  w and h go uncompared, because on any machine but the one that captured
   *  the reference they measure the data, not the stylesheet.
   *
   *  Use this only where the box has no blast radius, verified rather than
   *  assumed: measure a landmark below it with the text at both extents and
   *  confirm it does not move. If it does, the wrap IS layout and belongs in
   *  the gate. */
  textSized?: string[];
}

export async function measureSelectors(page: Page, spec: PageSpec): Promise<LandmarkMap> {
  await waitForBarlow(page);
  // Belt and braces: .pf-module-image boxes the wizard's vendor photos to a
  // fixed 132px square with object-fit: contain, so intrinsic size cannot move
  // the layout -- but a half-decoded image can still delay paint.
  await page.waitForFunction(() => [...document.images].every((i) => i.complete));
  return (await page.evaluate(
    ({ root, landmarks, props }) => {
      const rootEl = document.querySelector<HTMLElement>(root);
      if (rootEl === null) throw new Error(`no ${root} on the page`);
      const rr = rootEl.getBoundingClientRect();
      const out: Record<string, unknown> = {};
      for (const sel of landmarks) {
        const els = [...document.querySelectorAll<HTMLElement>(sel)];
        els.forEach((el, i) => {
          const r = el.getBoundingClientRect();
          const cs = getComputedStyle(el);
          out[els.length === 1 ? sel : `${sel}#${i}`] = {
            x: Math.round(r.left - rr.left),
            y: Math.round(r.top - rr.top),
            w: Math.round(r.width),
            h: Math.round(r.height),
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            styles: Object.fromEntries(props.map((p) => [p, cs.getPropertyValue(p)])),
          };
        });
      }
      return out;
    },
    { root: spec.root, landmarks: spec.landmarks, props: [...STYLE_PROPS] },
  )) as LandmarkMap;
}

export interface StyleProbe {
  /** Baseline key. */
  name: string;
  /** The class list to put on the probe element. */
  className: string;
}
export type StyleMap = Record<string, Record<string, string>>;

/**
 * Computed style of a class list, read off a detached element.
 *
 * Some chrome never renders under a fixed fixture: Banners returns null when
 * there are no errors or warnings, TimerBar renders only while a timer is
 * visible, and the wizard's three role="dialog" surfaces need a running grill
 * or a finished installer. wizard-layout.spec.ts already probes rules this way.
 * It proves the rules resolve, and to what; it proves nothing about how they
 * look in situ.
 */
export async function measureProbes(
  page: Page,
  host: string,
  probes: StyleProbe[],
): Promise<StyleMap> {
  return (await page.evaluate(
    ({ host, probes, props }) => {
      const parent = document.querySelector(host);
      if (parent === null) throw new Error(`no ${host} to host the probes`);
      const out: Record<string, Record<string, string>> = {};
      for (const p of probes) {
        const el = document.createElement("div");
        el.className = p.className;
        parent.appendChild(el);
        const cs = getComputedStyle(el);
        out[p.name] = Object.fromEntries(props.map((k) => [k, cs.getPropertyValue(k)]));
        el.remove();
      }
      return out;
    },
    { host, probes, props: [...STYLE_PROPS] },
  )) as StyleMap;
}

export function compareStyles(actual: StyleMap, baseline: StyleMap): string[] {
  const problems: string[] = [];
  for (const [name, want] of Object.entries(baseline)) {
    const got = actual[name];
    if (got === undefined) {
      problems.push(`${name}: MISSING probe`);
      continue;
    }
    for (const [prop, v] of Object.entries(want)) {
      if (got[prop] !== v) problems.push(`${name} { ${prop}: ${v} } -> ${got[prop]}`);
    }
  }
  for (const name of Object.keys(actual)) {
    if (baseline[name] === undefined) problems.push(`${name}: NEW probe, not in the baseline`);
  }
  return problems;
}

/** Baselines are captured deliberately or not at all.
 *
 *  A gate that updates itself when it disagrees with the page is not a gate --
 *  it silently rewrites itself into agreement with whatever it was supposed to
 *  be catching. So capture is one env var on one script (`bun run
 *  baseline:capture`), and a missing baseline is a hard failure everywhere
 *  else, never a quiet recording run. */
export const CAPTURING = process.env.PF_CAPTURE === "1";

export function baselinePath(name: string, viewport: { width: number; height: number }): string {
  return `tests/e2e/baselines/${name}-${viewport.width}x${viewport.height}.json`;
}

export function requireBaseline(path: string): LandmarkMap {
  const b = readBaseline(path);
  if (b === null) {
    throw new Error(
      `${path} is missing. Run \`bun run baseline:capture\` against the tree you want ` +
        `to make the reference, review the diff by hand, and commit it. Do not capture ` +
        `to make a red gate green.`,
    );
  }
  return b;
}

export function writeStyleBaseline(path: string, map: StyleMap): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(map, null, 2)}\n`);
}

export function readStyleBaseline(path: string): StyleMap | null {
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, "utf8")) as StyleMap;
}
