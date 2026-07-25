import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "@rstest/core";

const WIZARD_DIR = join("src", "components", "wizard");
const WIZARD_CSS = join(WIZARD_DIR, "wizard.css");

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

// Every pf-* token in ANY string literal, not just in a className={...}
// attribute. InstallProgress builds its bar class in a plain `const`, so an
// attribute-only scan would miss pf-install-progress-bar and
// pf-install-progress-bar-reduced-motion -- two of the classes this file exists
// to protect. The "finds what it is checking" test below pins that.
function classesUsed(): Set<string> {
  const found = new Set<string>();
  for (const file of walk(WIZARD_DIR)) {
    if (!file.endsWith(".tsx") || file.endsWith(".test.tsx")) continue;
    const src = readFileSync(file, "utf8");
    for (const [, dq, tpl, sq] of src.matchAll(/"([^"\n]*)"|`([^`\n]*)`|'([^'\n]*)'/g)) {
      for (const hit of (dq ?? tpl ?? sq ?? "").matchAll(/\bpf-[a-z0-9]+(?:-[a-z0-9]+)*/g)) {
        found.add(hit[0]);
      }
    }
  }
  return found;
}

// A class "has a rule" only when a selector mentioning it is followed by a
// declaration block that declares at least one property. `.foo {}` does not
// count -- an empty rule is the original defect wearing a hat. The regex matches
// innermost blocks first, so rules nested inside @media are captured (the outer
// @media prelude never matches: its body contains braces).
function declaredClasses(css: string): Set<string> {
  const out = new Set<string>();
  for (const [, selector, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!body.includes(":")) continue;
    for (const hit of selector.matchAll(/\.(pf-[a-z0-9]+(?:-[a-z0-9]+)*)/g)) out.add(hit[1]);
  }
  return out;
}

// The wizard's own vocabulary. Anything matching this must live in wizard.css,
// so a rule cannot be satisfied by accident from another surface's stylesheet --
// nor silently vanish when that surface is refactored.
const WIZARD_OWNED =
  /^pf-(wizard|module|install|discovery|port-form|device-form|form-actions|probes-table|btn-primary)/;

describe("wizard stylesheet coverage", () => {
  const used = classesUsed();
  const allCss = walk("src")
    .filter((f) => f.endsWith(".css"))
    .map((f) => readFileSync(f, "utf8"))
    .join("\n");
  const anywhere = declaredClasses(allCss);
  const inWizardCss = declaredClasses(readFileSync(WIZARD_CSS, "utf8"));

  it("finds the classes it is supposed to be checking", () => {
    expect(used.size).toBeGreaterThanOrEqual(51);
    expect(used.has("pf-install-progress-bar-reduced-motion")).toBe(true);
  });

  it("has a non-empty CSS rule for every pf-* class the wizard uses", () => {
    expect([...used].filter((c) => !anywhere.has(c)).sort()).toEqual([]);
  });

  it("declares every wizard-owned class in wizard.css itself", () => {
    expect([...used].filter((c) => WIZARD_OWNED.test(c) && !inWizardCss.has(c)).sort()).toEqual([]);
  });

  // Rules that exist but never load are rules that do not exist. This is the
  // other end of the same data path as the assertions above.
  it("is imported by WizardShell.tsx", () => {
    expect(readFileSync(join(WIZARD_DIR, "WizardShell.tsx"), "utf8")).toContain(
      'import "./wizard.css";',
    );
  });

  it("honours prefers-reduced-motion in the stylesheet, not only via the class", () => {
    const css = readFileSync(WIZARD_CSS, "utf8");
    const at = css.indexOf("@media (prefers-reduced-motion: reduce)");
    expect(at).toBeGreaterThan(-1);
    const block = css.slice(at);
    expect(block).toContain(".pf-install-progress-bar");
    expect(block).toContain("animation: none");
  });

  it("scopes its .pf-probes-card override under .pf-wizard", () => {
    expect(readFileSync(WIZARD_CSS, "utf8")).toContain(".pf-wizard .pf-probes-card");
  });
});
