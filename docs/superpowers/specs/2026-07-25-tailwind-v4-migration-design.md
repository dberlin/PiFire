# Tailwind v4 Migration — Design

**Status:** approved design, implementation blocked (see [Sequencing](#sequencing)).
**Date:** 2026-07-25

## Goal

Move `web-react/`'s six hand-written stylesheets onto Tailwind CSS v4, via the
Rsbuild integration, **without changing how anything looks** — except where the
current appearance is clearly broken.

## What exists today

Six stylesheets, 2,603 lines:

| File | Lines | Notes |
|---|---:|---|
| `src/components/dashboard/dashboard.css` | 1,149 | 71 `--pf-*` custom properties of its own |
| `src/components/wizard/wizard.css` | 624 | written 2026-07-25 |
| `src/components/settings/settings.css` | 344 | |
| `src/components/shell/shell.css` | 315 | |
| `src/theme.css` | 110 | the design tokens + reset |
| `src/components/history/historyChart.css` | 61 | |

`theme.css` holds the palette as CSS custom properties on `:root` — `--page`,
`--card`, `--inset`, `--text`, `--text-dim`, `--card-radius`, `--pill-radius`,
`--anim-ms`, `--ease-out-cubic`, and a four-property accent group (`--accent`,
`--accent-1`, `--accent-2`, `--glow`). The accent group is redefined under
`:root[data-accent="ice"]` and `:root[data-accent="crimson"]`, which is how the
theme switcher works. The tokens are "ported verbatim from
`display/qml/Theme.qml`" — they are shared with the Qt UI, so their *values* are
not ours to change.

Class naming is a `pf-*` convention, applied through `className` string literals
and, in a few places, template-literal construction (`pf-badge-${mode}`,
`pf-banner--${kind}`).

## Approach: Tailwind owns the tokens, `pf-*` names survive

Tailwind v4's `@theme` block becomes the single definition of the design tokens.
Component rules keep their existing semantic class names and are re-authored in
terms of Tailwind utilities via `@apply`. JSX is not rewritten.

```css
/* src/theme.css */
@import "tailwindcss";

@theme {
  --color-page: #0c0a09;
  --color-card: #2c231a;
  --color-inset: #1c1712;
  --color-text: #f4ede2;
  --color-text-dim: #a89a86;
  --radius-card: 18px;
  --radius-pill: 999px;
}
```

```css
/* src/components/dashboard/dashboard.css */
.pf-card {
  @apply bg-card rounded-card p-4 flex flex-col gap-3;
}
```

**Why this rather than a full utility rewrite.** The hard requirement is that
nothing changes visually. Rewriting ~257 class sites into utility strings moves
every rule and every selector at once, and the project's tests query by class
name — so a full rewrite breaks the test suite and the visual gate
simultaneously, leaving nothing trustworthy to check the result against. The
token bridge changes one layer at a time with the DOM held fixed, which is what
makes a before/after comparison meaningful at all.

**Why not "Tailwind for new code only".** Two styling systems indefinitely, and
the visual-identity gate would have nothing to prove. Rejected.

**Accepted cost.** The end state is not "pure Tailwind": a stylesheet layer
remains. That is deliberate. It is also reversible — once every rule is
expressed as `@apply` utilities, converting a component to inline utilities is a
mechanical, component-at-a-time change that a later slice can take on with the
gate already in place.

## Integration

```sh
bun add -d @rsbuild/plugin-tailwindcss tailwindcss
```

```ts
// rsbuild.config.ts
import { pluginTailwindcss } from "@rsbuild/plugin-tailwindcss";
export default defineConfig({ plugins: [pluginReact(...), pluginTailwindcss()] });
```

**There is no PostCSS decision to make.** Rsbuild transforms CSS with Lightning
CSS by default, through Rspack's built-in `lightningcss-loader`, and it does not
register `postcss-loader` at all unless the project has a `postcss.config.*` or
sets `tools.postcss`. This repo has neither. Tailwind v4 also uses Lightning CSS
internally. So the `@tailwindcss/postcss` route would not be "the other option"
— it would *introduce* a PostCSS pass into a pipeline that currently has none,
for no benefit. Use `@rsbuild/plugin-tailwindcss`, which builds on
`@tailwindcss/webpack`.

A consequence worth stating, because it removes work people expect to do:
Lightning CSS already reads browserslist, adds vendor prefixes, and downlevels
modern syntax including nesting. **Do not add autoprefixer, postcss-preset-env,
postcss-nesting, or cssnano** as part of this migration. Minification comes from
`LightningCssMinimizerRspackPlugin`.

Two upstream caveats apply:

- **v4 requires Cascade Layers** in target browsers. The repo pins **no**
  browserslist — no `.browserslistrc`, no `browserslist` key in `package.json` —
  so Lightning CSS is working from Rsbuild's default target set. Establish what
  that resolves to *before* starting, and consider pinning it explicitly as part
  of this work: an unpinned target list means the CSS output can change under
  you when a dependency updates, which is precisely the kind of silent drift the
  visual-identity gate would then report as a regression with no cause in the
  diff.
- **v4 cannot be used with Sass/Less/Stylus.** The repo is plain CSS. No action.

## The visual-identity gate

This is the deliverable that makes the migration safe, and most of the work.

### Extend what exists — do not invent a new mechanism

`tests/e2e/layoutBaseline.ts` and `tests/e2e/dashboard-layout-1280x720.json`
already implement exactly this idea for the dashboard: for each named landmark,
record `{x, y, w, h, fontSize, fontWeight}`; compare with `BOX_TOL = 2`px, plus
an `EXACT` table (`EXACT_TOL = 0.5`) for dimensions that must not move at all.

It is deliberately **not** a `toHaveScreenshot()` gate, and the migration must
not "upgrade" it into one. `index.html` loads Barlow from
`fonts.googleapis.com`, so pixels depend on the network and the host font stack;
masking the volatile regions would mask exactly the typography the gate exists
to protect. The committed PNG is a human artifact, not an assertion.

### Coverage

Baselines for every page, at both viewports — 1280×720 (the desktop regression
target) and 390×844 (the phone target the reflow slice introduces):

```
tests/e2e/baselines/
  dashboard-{1280x720,390x844}.json
  shell-{1280x720,390x844}.json          navbar, timer bar, banners
  settings-<tab>-{1280x720,390x844}.json   12 tabs
  wizard-<step>-{1280x720,390x844}.json     7 steps
  history-{1280x720,390x844}.json
```

Capture is a one-shot script run **against the pre-migration tree**, committed,
then asserted against after each migration step. `writeBaseline` already exists
for this; the capture path must be explicit (a flag or separate script), never
an automatic "update if different", or the gate silently rewrites itself into
agreement with whatever it is supposed to be catching.

### Determinism

Every baseline page must be captured through the same discipline the dashboard
fidelity spec already uses, or the gate will be flaky rather than wrong:

- Run against the **demo server** where possible — demo mode opens no socket, so
  the shared stateful PiFire instance cannot race the measurement.
- `page.clock.install()` + `pauseAt()` **before** navigating, so the shared
  interval in `helpers/clock.ts`, `useClock`, and elapsed-time rendering are all
  pinned.
- Wait for fonts: `document.fonts.ready` for the exact `(weight, family)` pairs
  the landmarks use — `layoutBaseline.ts` already enumerates these in `FACES`.
- `animations: "disabled"` on any screenshot artifact.

Pages that cannot run under demo mode (the wizard's later steps, settings tabs
that read live config) need a seeded `PIFIRE_DB_PATH` fixture so the DOM shape
is identical on every machine.

### What counts as "clearly broken" and may change

The requirement is visual identity *except* where the before is clearly broken.
Any intended difference must be listed explicitly in the implementation plan,
with a before/after artifact, and the corresponding baseline entry updated in
the same commit as the change that causes it. A baseline update that is not
accompanied by a stated, reviewed reason is a defect, not a result.

## Risks specific to this repo

**The class-coverage guard interacts with `@apply`.** A test asserts that every
`pf-*` class used in JSX is declared in CSS. It was recently found to be
silently disarmed — its regex treated CSS *comment* text as selector text, so a
class counted as declared because prose mentioned it, and a real deletion went
undetected. That has been fixed. The migration must re-verify the guard still
holds when rules are authored with `@apply`, and must not weaken it: this guard
is the only thing standing between "the stylesheet compiles" and "the classes
the app actually uses exist".

**Biome lints CSS.** `bun run lint` runs Biome over `.css`, and it enforces
rules like `noDescendingSpecificity`. Confirm early that Biome's CSS parser
accepts `@theme`, `@apply`, and `@import "tailwindcss"` without error; if it
does not, decide deliberately between a scoped Biome override and a different
lint arrangement, and record which. Do not discover this at the end.

**Tokens are shared with the Qt UI.** `theme.css` is a verbatim port of
`display/qml/Theme.qml`. Moving them into `@theme` must preserve the values
exactly; a drift here changes the Qt dashboard's appearance by implication, not
just the web one.

**The accent switcher uses attribute selectors on `:root`.** `@theme` defines
tokens once; the `[data-accent="ice"]` / `[data-accent="crimson"]` overrides
must keep working. Verify all three accents against baselines, not just the
default Ember.

**Dynamic class names.** `pf-badge-${mode}` and `pf-banner--${kind}` are
constructed at runtime. Tailwind's content scanner cannot see them, which is
harmless while they remain hand-written rules, but becomes a silently-missing
style the moment anyone converts them to utilities. Note it where those rules
live.

## Sequencing

**Implementation is blocked until the wizard-styling and dashboard-reflow slices
merge.** Those two are rewriting `wizard.css` and `dashboard.css` — 1,773 of the
2,603 lines — right now. Migrating underneath them would collide with both, and
baselines captured today would freeze geometry that is about to change on
purpose.

Order once unblocked:

1. Both slices merge; branch green.
2. Capture baselines against the merged tree. This is the reference.
3. Wire up the plugin; `@import "tailwindcss"` with no other change. Assert
   baselines unchanged — this step proves the toolchain is inert on its own.
   Tailwind's preflight reset is the thing most likely to move something here,
   and `theme.css` already carries its own reset (`* { box-sizing: border-box }`,
   `html, body, #root { height: 100%; margin: 0 }`). If baselines move at this
   step, reconcile the two resets deliberately; do not proceed with a diff you
   cannot explain.
4. Move `theme.css` tokens into `@theme`. Assert baselines, all three accents.
5. Convert stylesheets one at a time, smallest first (`historyChart` → `shell` →
   `settings` → `wizard` → `dashboard`), asserting baselines after each.

Step 3 matters more than it looks: if adding Tailwind changes anything before a
single rule is rewritten — a reset, a layer order, a specificity shift — that
must be found and understood in isolation, not diagnosed later while a thousand
lines are also in motion.

## Out of scope

- Converting `pf-*` rules to inline utilities in JSX.
- Changing any token value, spacing scale, or type scale.
- Dark/light theming beyond the existing three accents. The app is dark-only by
  design.
- The Flask/Jinja UI's stylesheets. This is `web-react/` only.
