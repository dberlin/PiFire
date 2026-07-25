import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "@rstest/core";

const WIZARD_DIR = join("src", "components", "wizard");
const WIZARD_CSS = join(WIZARD_DIR, "wizard.css");

// A class "has a rule" only when a selector mentioning it is followed by a
// declaration block that declares at least one property. `.foo {}` does not
// count -- an empty rule is the original defect wearing a hat.
//
// The regex matches innermost blocks first, so rules nested inside @media are
// captured too (the outer @media prelude never matches, because its body
// contains braces).
export function declaredClasses(css: string): Set<string> {
  const out = new Set<string>();
  for (const [, selector, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!body.includes(":")) continue;
    for (const hit of selector.matchAll(/\.(pf-[a-z0-9]+(?:-[a-z0-9]+)*)/g)) out.add(hit[1]);
  }
  return out;
}

const CHROME = [
  "pf-wizard",
  "pf-wizard-header",
  "pf-wizard-title",
  "pf-wizard-steps",
  "pf-wizard-step-indicator",
  "pf-wizard-exit",
  "pf-wizard-content",
  "pf-wizard-step",
  "pf-wizard-step-title",
  "pf-wizard-step-placeholder",
  "pf-wizard-step-finish",
  "pf-wizard-placeholder-message",
  "pf-wizard-finish-note",
  "pf-wizard-finish-error",
  "pf-wizard-footer",
  "pf-wizard-error",
  "pf-btn-primary",
];

describe("wizard stylesheet — chrome layer", () => {
  it("is imported by WizardShell.tsx", () => {
    const src = readFileSync(join(WIZARD_DIR, "WizardShell.tsx"), "utf8");
    expect(src).toContain('import "./wizard.css";');
  });

  it("declares a non-empty rule for every chrome class", () => {
    const declared = declaredClasses(readFileSync(WIZARD_CSS, "utf8"));
    expect(CHROME.filter((c) => !declared.has(c))).toEqual([]);
  });
});
