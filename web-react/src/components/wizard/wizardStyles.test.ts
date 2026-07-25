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

const MODULE_CARD = [
  "pf-module-card",
  "pf-module-details",
  "pf-module-image",
  "pf-module-name",
  "pf-module-description",
  "pf-module-notes",
  "pf-module-deps",
  "pf-module-config",
  "pf-form-actions",
];

const PROBES = ["pf-probes-table", "pf-port-form", "pf-device-form"];

const DISCOVERY = [
  "pf-discovery-panel",
  "pf-discovery-group",
  "pf-discovery-group-title",
  "pf-discovery-group-items",
];

const INSTALL_AND_MODALS = [
  "pf-install-progress",
  "pf-install-progress-status",
  "pf-install-progress-track",
  "pf-install-progress-bar",
  "pf-install-progress-bar-reduced-motion",
  "pf-install-reboot-modal",
  "pf-install-reboot-message",
  "pf-install-reboot-actions",
  "pf-wizard-modal",
  "pf-wizard-system-active-modal",
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

  it("declares a non-empty rule for every module-card class", () => {
    const declared = declaredClasses(readFileSync(WIZARD_CSS, "utf8"));
    expect(MODULE_CARD.filter((c) => !declared.has(c))).toEqual([]);
  });

  it("declares a non-empty rule for every probes-step class", () => {
    const declared = declaredClasses(readFileSync(WIZARD_CSS, "utf8"));
    expect(PROBES.filter((c) => !declared.has(c))).toEqual([]);
  });

  it("declares a non-empty rule for every discovery class", () => {
    const declared = declaredClasses(readFileSync(WIZARD_CSS, "utf8"));
    expect(DISCOVERY.filter((c) => !declared.has(c))).toEqual([]);
  });

  it("declares a non-empty rule for every install/modal class", () => {
    const declared = declaredClasses(readFileSync(WIZARD_CSS, "utf8"));
    expect(INSTALL_AND_MODALS.filter((c) => !declared.has(c))).toEqual([]);
  });

  // InstallProgress reads matchMedia ONCE, at first render, and never updates if
  // the preference changes mid-install. The class is therefore only half the
  // guarantee; the media query is the half that always holds. Both are required.
  it("honours prefers-reduced-motion in the stylesheet, not only via the class", () => {
    const css = readFileSync(WIZARD_CSS, "utf8");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    const block = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(block).toContain(".pf-install-progress-bar");
    expect(block).toContain("animation: none");
  });

  // settings.css owns `.pf-probes-card { position: relative }` (it is the
  // containing block for ConfirmAction's absolutely-positioned scrim) and this
  // plan does not edit that file. The card's APPEARANCE is added here instead,
  // scoped so it wins on specificity rather than on injection order.
  it("scopes its .pf-probes-card override under .pf-wizard", () => {
    const css = readFileSync(WIZARD_CSS, "utf8");
    expect(css).toContain(".pf-wizard .pf-probes-card");
  });
});
