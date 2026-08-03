import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

/** Every file under `dir`, recursively. */
export function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

/**
 * Comments are prose, not selectors.
 *
 * Stripping them is load-bearing: the `selector` capture in declaredClasses is
 * "everything since the previous brace", which includes any comment sitting
 * above a rule -- and these stylesheets' comments routinely name the very class
 * they introduce. Without this, a class counted as declared because a COMMENT
 * mentioned it, so deleting its rule left the guard green. Found by mutation:
 * removing both .pf-module-notes blocks did not turn the wizard guard red until
 * comments were stripped.
 */
export function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/**
 * The classes for which `css` contains a non-empty rule.
 *
 * "Non-empty" means the body declares something. `.foo {}` does not count -- an
 * empty rule is the original defect wearing a hat. Two ways to declare:
 *
 *   - an ordinary `prop: value` pair, detected by a colon;
 *   - an `@apply` at-rule, which has NO colon in it.
 *
 * That second clause is why this function exists as a module. The wizard guard
 * used to require a colon, so `.pf-card { @apply bg-card; }` -- the shape every
 * rule takes after the Tailwind v4 migration -- would have counted as
 * undeclared, and the guard would have reported a totally empty stylesheet as
 * fully covered.
 *
 * Only the SELECTOR is scanned for class names, never the body: `@apply
 * pf-thing` references a utility, it does not declare `.pf-thing`.
 *
 * The regex matches innermost blocks first, so rules nested inside @media are
 * captured (the outer @media prelude never matches: its body contains braces).
 */
export function declaredClasses(css: string): Set<string> {
  const out = new Set<string>();
  for (const [, selector, body] of stripComments(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!body.includes(":") && !body.includes("@apply")) continue;
    for (const hit of selector.matchAll(/\.(pf-[a-z0-9]+(?:-[a-z0-9]+)*)/g)) out.add(hit[1]);
  }
  return out;
}

/**
 * The classes whose non-empty rules reach a nested `<button>`.
 *
 * Preflight resets every button to transparent, borderless, inherited text, so
 * a button no rule reaches does not render as an unstyled button -- it renders
 * as a run of prose that happens to be clickable. The `button` term has to be
 * present in the selector rather than inferred from the class name: a class can
 * style its own box without styling the buttons inside it, and that is exactly
 * the gap this reports.
 */
export function classesStylingButtons(css: string): Set<string> {
  const out = new Set<string>();
  for (const [, selector, body] of stripComments(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!body.includes(":") && !body.includes("@apply")) continue;
    // Per comma-separated part: `.a > button, .b .c` styles buttons under .a
    // only, and crediting .b and .c from the same rule would report a class as
    // covered because a SIBLING selector happened to mention a button.
    for (const part of selector.split(",")) {
      if (!/(?<![\w-])button(?![\w-])/.test(part)) continue;
      for (const hit of part.matchAll(/\.(pf-[a-z0-9]+(?:-[a-z0-9]+)*)/g)) out.add(hit[1]);
    }
  }
  return out;
}

/**
 * The animation names `css` defines.
 *
 * Components name keyframes in inline style strings -- SystemStatus sets
 * `animation: "pf-spin 0.85s linear infinite"`, GrillGauge `"pf-glow 3.2s ..."`
 * -- so a pf-* token in a .tsx is not necessarily a class. Deleting the
 * @keyframes block breaks the animation just as surely as deleting a rule
 * breaks a class, so these count as declared rather than being excused.
 */
export function declaredAnimations(css: string): Set<string> {
  const out = new Set<string>();
  for (const hit of stripComments(css).matchAll(/@keyframes\s+([a-zA-Z][\w-]*)/g)) out.add(hit[1]);
  return out;
}

/**
 * Every pf-* token in ANY string literal under `dir`, not just in a className
 * attribute. InstallProgress builds its bar class in a plain `const`, so an
 * attribute-only scan would miss pf-install-progress-bar and
 * pf-install-progress-bar-reduced-motion.
 *
 * The lookbehind excludes `--pf-*`: ProbeCard and SystemStatus pass custom
 * properties ("--pf-bar-color", "--pf-out-dot") through inline style objects,
 * and a bare `\b` sits between the second dash and the `p`, so a word-boundary
 * match reported fifteen CSS variables as undeclared classes.
 *
 * Uppercase letters are matched even though no class uses them, because
 * SystemStatus names the keyframes `pf-augerFeed`: a lowercase-only token
 * regex TRUNCATES that to `pf-auger`, then reports the truncation as missing
 * while the real name sits declared in dashboard.css. A scanner that silently
 * rewrites the name it is checking is worse than one that finds nothing.
 *
 * Test files are excluded: they are full of pf-* names, and counting them would
 * let a test keep a deleted class alive.
 */
export function classesUsedIn(dir: string): Set<string> {
  const found = new Set<string>();
  for (const file of walk(dir)) {
    if (!file.endsWith(".tsx") || file.endsWith(".test.tsx")) continue;
    const src = readFileSync(file, "utf8");
    for (const [, dq, tpl, sq] of src.matchAll(/"([^"\n]*)"|`([^`\n]*)`|'([^'\n]*)'/g)) {
      for (const hit of (dq ?? tpl ?? sq ?? "").matchAll(
        /(?<![\w-])pf-[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*/g,
      )) {
        found.add(hit[0]);
      }
    }
  }
  return found;
}

/** Every stylesheet under `root`, concatenated. */
export function allStylesheets(root = "src"): string {
  return walk(root)
    .filter((f) => f.endsWith(".css"))
    .map((f) => readFileSync(f, "utf8"))
    .join("\n");
}
