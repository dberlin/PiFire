import { describe, expect, it } from "@rstest/core";
import {
  classesUsedIn,
  declaredAnimations,
  declaredClasses,
  stripComments,
} from "../../../src/helpers/cssCoverage";

describe("declaredClasses", () => {
  it("counts an ordinary declaration", () => {
    expect(declaredClasses(".pf-real { color: red; }").has("pf-real")).toBe(true);
  });

  // THE @apply CASE. An @apply body has no colon in it, and the pre-extraction
  // guard required one -- so every rule this migration converts would have
  // stopped counting as declared and the guard would have gone green on an
  // empty stylesheet.
  it("counts a rule whose whole body is @apply", () => {
    expect(declaredClasses(".pf-card { @apply bg-card rounded-card p-4; }").has("pf-card")).toBe(
      true,
    );
  });

  it("counts a rule mixing @apply with raw declarations", () => {
    const css =
      ".pf-mix { @apply flex; background: color-mix(in srgb, var(--accent) 16%, transparent); }";
    expect(declaredClasses(css).has("pf-mix")).toBe(true);
  });

  // Guards the guard, three ways. Every assertion above is only as good as this
  // being able to say "no".
  it("does not count an empty rule", () => {
    expect(declaredClasses(".pf-hollow { }").has("pf-hollow")).toBe(false);
  });

  it("does not count a class that is only NAMED IN A COMMENT", () => {
    const css = "/* .pf-ghost is explained here */\n.pf-real { color: red; }";
    expect(declaredClasses(css).has("pf-real")).toBe(true);
    expect(declaredClasses(css).has("pf-ghost")).toBe(false);
  });

  it("does not count a class named only inside an @apply argument list", () => {
    // `@apply pf-thing` would be a Tailwind utility reference, not a rule that
    // declares .pf-thing. If this returned true, deleting .pf-thing's own rule
    // would leave the guard green.
    expect(declaredClasses(".pf-host { @apply pf-thing; }").has("pf-thing")).toBe(false);
    expect(declaredClasses(".pf-host { @apply pf-thing; }").has("pf-host")).toBe(true);
  });

  it("reaches rules nested inside @media", () => {
    const css = "@media (max-width: 719px) { .pf-phone { @apply p-[8px]; } }";
    expect(declaredClasses(css).has("pf-phone")).toBe(true);
  });

  it("ignores an @reference line", () => {
    const css = '@reference "../../theme.css";\n.pf-x { @apply flex; }';
    expect(declaredClasses(css).has("pf-x")).toBe(true);
  });
});

describe("stripComments", () => {
  it("removes block comments and keeps the rules around them", () => {
    expect(stripComments("/* a */ .b { c: d; } /* e */").trim()).toBe(".b { c: d; }");
  });
});

describe("declaredAnimations", () => {
  it("finds a keyframes name", () => {
    expect(declaredAnimations("@keyframes pf-spin { to { rotate: 360deg; } }").has("pf-spin")).toBe(
      true,
    );
  });

  it("does not invent one from a comment", () => {
    expect(declaredAnimations("/* @keyframes pf-ghost */").has("pf-ghost")).toBe(false);
  });
});

describe("classesUsedIn", () => {
  it("finds classes in plain, template and single-quoted strings", () => {
    const used = classesUsedIn("src/components/wizard");
    // Built in a plain `const`, not a className attribute -- an attribute-only
    // scan would miss it (InstallProgress.tsx).
    expect(used.has("pf-install-progress-bar-reduced-motion")).toBe(true);
    expect(used.has("pf-wizard-header")).toBe(true);
  });

  // ProbeCard and SystemStatus pass "--pf-bar-color" / "--pf-out-dot" through
  // inline style objects. A `\b` match treats the second dash as a boundary and
  // reports the variable as an undeclared CLASS -- fifteen of them, on a tree
  // where nothing is wrong.
  it("does not mistake a --pf-* custom property for a class", () => {
    const used = classesUsedIn("src/components/dashboard");
    expect(used.has("pf-bar-color")).toBe(false);
    expect(used.has("pf-out-dot")).toBe(false);
    expect(used.has("pf-dash-header")).toBe(true);
  });

  // SystemStatus animates with `pf-augerFeed`, declared as @keyframes in
  // dashboard.css. A lowercase-only token regex truncates it to "pf-auger" and
  // then reports THAT as having no rule -- a scanner that rewrites the name it
  // is checking, which is worse than one that finds nothing.
  it("does not truncate a camelCase name at the capital", () => {
    const used = classesUsedIn("src/components/dashboard");
    expect(used.has("pf-augerFeed")).toBe(true);
    expect(used.has("pf-auger")).toBe(false);
  });

  it("skips test files", () => {
    // wizardStyles.test.ts is full of pf-* names in string literals; counting
    // them would let a test keep a deleted class alive.
    expect(classesUsedIn("src/components/wizard").has("pf-ghost")).toBe(false);
  });
});
