import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "@rstest/core";
import {
  allStylesheets,
  classesUsedIn,
  declaredClasses,
} from "../../../../src/helpers/cssCoverage";

const WIZARD_DIR = join("src", "components", "wizard");
const WIZARD_CSS = join(WIZARD_DIR, "wizard.css");
const PROBES_CSS = join(WIZARD_DIR, "probes", "probes.css");

// The wizard's own vocabulary. Anything matching this must live in wizard.css,
// so a rule cannot be satisfied by accident from another surface's stylesheet --
// nor silently vanish when that surface is refactored.
const WIZARD_OWNED =
  /^pf-(wizard|module|install|discovery|port-form|device-form|form-actions|probes-table|btn-primary)/;

describe("wizard stylesheet coverage", () => {
  const used = classesUsedIn(WIZARD_DIR);
  const anywhere = declaredClasses(allStylesheets());
  // The probe-editing vocabulary lives in probes/probes.css so it can travel to
  // /settings/probes, which does not render inside .pf-wizard and does not
  // import wizard.css. Both files are wizard-owned; neither may push a rule out
  // to a stylesheet some other surface might refactor away.
  const inWizardCss = new Set([
    ...declaredClasses(readFileSync(WIZARD_CSS, "utf8")),
    ...declaredClasses(readFileSync(PROBES_CSS, "utf8")),
  ]);

  it("finds the classes it is supposed to be checking", () => {
    expect(used.size).toBeGreaterThanOrEqual(51);
    expect(used.has("pf-install-progress-bar-reduced-motion")).toBe(true);
  });

  // The comment-vs-declaration guard itself is covered once, directly against
  // declaredClasses(), in cssCoverage.test.ts -- no need to restate it here.

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

  it("scopes its .pf-probes-card override under .pf-probes-surface, not .pf-wizard", () => {
    const css = readFileSync(PROBES_CSS, "utf8");
    expect(css).toContain(".pf-probes-surface .pf-probes-card");
    expect(readFileSync(WIZARD_CSS, "utf8")).not.toContain(".pf-wizard .pf-probes-card");
  });

  it("is imported by the two cards, so it reaches every surface that renders them", () => {
    for (const file of ["DevicesCard.tsx", "PortsCard.tsx"]) {
      expect(readFileSync(join(WIZARD_DIR, "probes", file), "utf8")).toContain(
        'import "./probes.css";',
      );
    }
  });

  // The wizard's chrome buttons (Back/Next/Finish/Exit) and the probe cards'
  // buttons are the same object family and share ONE rule, which is why that
  // rule carries both scopes. Moving it to .pf-probes-surface alone -- as an
  // earlier draft of the plan directed -- would have left every wizard chrome
  // button as dashboard.css's bare 25px shell, since .pf-probes-surface is on
  // the probes step only.
  it("keeps the wizard's own chrome buttons in the shared button rule", () => {
    const css = readFileSync(PROBES_CSS, "utf8");
    expect(css).toContain(".pf-wizard .pf-btn,");
    expect(css).toContain(".pf-probes-surface .pf-btn");
  });
});
