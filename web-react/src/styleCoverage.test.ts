import { readFileSync } from "node:fs";
import { describe, expect, it } from "@rstest/core";
import {
  allStylesheets,
  classesUsedIn,
  declaredAnimations,
  declaredClasses,
} from "./helpers/cssCoverage";

// The repo-wide half of the class-coverage guard.
//
// src/components/wizard/wizardStyles.test.ts has covered the wizard directory
// for a while; nothing covered the other five surfaces, so a dashboard, shell,
// settings, history or pellets class could be deleted from CSS with a green
// suite. This is the only thing standing between "the stylesheet compiles" and
// "the classes the app actually uses exist", and the Tailwind migration rewrites
// every one of those stylesheets.

const SURFACES = [
  "src/components/dashboard",
  "src/components/shell",
  "src/components/settings",
  "src/components/history",
  "src/components/pellets",
  "src/components/wizard",
];

// Names written into a .tsx and styled by nothing, each because the element
// genuinely needs no box of its own -- not because a rule is missing. The list
// is asserted for EXACT equality below, so styling one, or introducing a new
// bare name, fails here and has to be argued for either way.
//
//   pf-settings-tab       PlatformTab.tsx:41  -- wrapper div, no box of its own
//   pf-section-note       PlatformTab.tsx:43  -- the section body supplies the
//                         gap, and the note takes the body's own type
//   pf-rpt-range-label    RangeProfileTable.tsx:155 -- span, unstyled
//   pf-pellets-load       CurrentLoadCard.tsx:59 -- wrapper div. Note that
//                         .pf-pellets-load-name IS styled; the bare name is not.
const UNSTYLED = ["pf-pellets-load", "pf-rpt-range-label", "pf-section-note", "pf-settings-tab"];

describe("every pf-* class the app uses has a rule", () => {
  // Animation names count as declared: SystemStatus and GrillGauge name
  // keyframes in inline style strings, and deleting the @keyframes block breaks
  // them exactly as deleting a rule breaks a class.
  const css = allStylesheets();
  const declared = new Set([...declaredClasses(css), ...declaredAnimations(css)]);

  it("finds the classes it is supposed to be checking", () => {
    const used = new Set(SURFACES.flatMap((d) => [...classesUsedIn(d)]));
    // Measured on the pre-migration tree: 238 distinct pf-* classes are
    // declared across the seven stylesheets. A floor, not an equality -- new
    // components add classes and that must not turn this red.
    expect(used.size).toBeGreaterThanOrEqual(150);
    expect(declared.size).toBeGreaterThanOrEqual(200);
  });

  for (const dir of SURFACES) {
    it(`${dir} declares every class it uses`, () => {
      const missing = [...classesUsedIn(dir)]
        .filter((c) => !declared.has(c) && !UNSTYLED.includes(c))
        .sort();
      expect(missing, `no CSS rule for: ${missing.join(", ")}`).toEqual([]);
    });
  }

  // THE MIGRATION'S FAILURE MODE, exercised against the real stylesheets rather
  // than a two-line fixture.
  //
  // Tasks 9-14 rewrite every rule body in these seven files into `@apply`
  // utilities. An @apply-only body contains no colon, and the guard this engine
  // was extracted from counted a rule as a declaration only when its body did.
  // So on the converted tree `declared` collapses to nothing -- and the guard
  // does not go usefully red either: a class survives in `declared` for as long
  // as ANY unconverted rule still names it in a compound selector, so the
  // failure is partial, arrives file by file, and looks like noise rather than
  // like a broken gate. Simulating the whole conversion here means the guard is
  // tested against the shape it will actually have to survive, before a single
  // stylesheet is touched.
  //
  // The rewrite only touches innermost blocks (`[^{}]*`), so @media and
  // @supports preludes are left alone, and an already-empty body stays empty --
  // otherwise this would also erase "an empty rule declares nothing".
  it("still sees every class when every rule body becomes @apply", () => {
    const converted = css.replace(/\{([^{}]*)\}/g, (whole, body: string) =>
      body.includes(":") ? "{ @apply flex; }" : whole,
    );
    expect(converted).toContain("@apply flex;");
    const after = declaredClasses(converted);
    const lost = [...declaredClasses(css)].filter((c) => !after.has(c)).sort();
    expect(lost, `stopped counting as declared after @apply: ${lost.join(", ")}`).toEqual([]);
  });

  // Without this the allowlist above rots: a name could be styled, or dropped
  // from the JSX entirely, and go on excusing nothing.
  it("has no stale entry in the unstyled allowlist", () => {
    const used = new Set(SURFACES.flatMap((d) => [...classesUsedIn(d)]));
    const stale = UNSTYLED.filter((c) => !used.has(c) || declared.has(c)).sort();
    expect(stale, `no longer unstyled-and-used: ${stale.join(", ")}`).toEqual([]);
  });

  // The other end of the same data path: rules that exist but never load are
  // rules that do not exist.
  it("keeps every stylesheet imported", () => {
    const imports = [
      ["src/main.tsx", "./theme.css"],
      ["src/main.tsx", "./components/dashboard/dashboard.css"],
      ["src/main.tsx", "./components/settings/settings.css"],
      ["src/components/shell/AppShell.tsx", "./shell.css"],
      ["src/components/wizard/WizardShell.tsx", "./wizard.css"],
      ["src/components/history/HistoryChart.tsx", "./historyChart.css"],
      ["src/components/pellets/PelletsPage.tsx", "./pellets.css"],
    ];
    for (const [file, spec] of imports) {
      expect(readFileSync(file, "utf8"), `${file} no longer imports ${spec}`).toContain(
        `import "${spec}";`,
      );
    }
  });
});
