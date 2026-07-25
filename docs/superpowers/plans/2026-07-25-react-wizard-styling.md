# React Setup Wizard Styling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the React setup wizard a visual design. It is functionally complete and **entirely unstyled**: 43 of the 51 `pf-*` class names its markup carries match zero CSS rules anywhere in the repo, and there is no `wizard.css`. At 1280×720 it renders as raw HTML — the six step labels run together as `WelcomeGrill PlatformProbesDisplayDistance / HopperFinish`, buttons stack at x=0, module cards have no card treatment, and three `role="dialog"` elements have no modal chrome at all.

**Architecture:** One new stylesheet, `web-react/src/components/wizard/wizard.css`, imported from `WizardShell.tsx`. Zero markup changes to any wizard component. The stylesheet reuses `src/theme.css`'s tokens and the recipes already established in `dashboard.css` / `settings.css` / `shell.css` — no second visual language, no new palette. Built up in five sequential commits (chrome → module card → probes → discovery → install/modals), then locked behind a three-part regression net.

**Tech Stack:** React 19 + react-router 8, TS7 (`typescript7`/tsgo), rsbuild, Biome + eslint, @rstest/core, Playwright, bun.

## Why this exists

Every wizard unit test and all four specs in `tests/e2e/wizard.spec.ts` pass against the unstyled page, because every one of them asserts on **text and ARIA roles only** — `getByRole("heading", { name: "Welcome" })`, `getByRole("combobox", { name: "Module" })`, `selectOption(...)`. Not one of them touches a class name, a computed style or a bounding box. That is exactly why an entirely unstyled surface shipped without a single red test, and it is why **the existing suite is worthless as a regression net for this work** (see *Verification*).

The wizard is also the **first** screen a new user ever sees: `components/DashboardRoute.tsx` bounces a fresh install straight to `/wizard`, and `App.tsx:88-96` deliberately mounts it **outside** `AppShell` — no navbar, no timer strip, no banners — so `.pf-wizard` is the entire page. Nothing else on screen can carry the design for it.

---

## Global Constraints

Copied verbatim; these are binding.

- **Target viewport 1280×720**, fitting without page scroll the way the dashboard does. The content area may scroll for genuinely long steps, but header / step indicator / footer chrome stays put.
- **Match however `theme.css` handles light/dark.** Checked: `src/theme.css` has **no light mode at all** — no `prefers-color-scheme`, no `data-theme`, no `.light`/`.dark` class. It declares one dark palette on `:root` (`--page #0c0a09`, `--card #2c231a`, `--inset #1c1712`, `--text #f4ede2`, `--text-dim #a89a86`) plus three accent swaps selected by `:root[data-accent="ice"|"crimson"]`. **The wizard stylesheet is therefore dark-only and accent-token-driven. Adding a `prefers-color-scheme` block would introduce a second visual language and is forbidden here.**
- **bun**, never npm. **@rstest/core** (`rs.fn`/`rs.mock`) — never vitest, there is no `vi`. **Biome**: `bun run lint` must exit 0; exactly 2 pre-existing `react-refresh` warnings are expected.
- No suppressions, no `any`, no `@ts-ignore`. No `biome-ignore`, no `@ts-expect-error`, no `eslint-disable`.
- **Honor `prefers-reduced-motion`** — one of the undefined classes is literally `pf-install-progress-bar-reduced-motion`, so the component already anticipates it.
- `.test.tsx` → jsdom, `.test.ts` → node. **jsdom does no layout**: `getBoundingClientRect()` returns zeros and `getComputedStyle()` resolves nothing from a stylesheet. No unit test in this plan may claim to assert geometry.
- Gate for every task: `bun run typecheck && bun run lint && bun run test && bun run build`. Plus `bun run test:e2e` for any task touching Playwright.
- **Do not touch `web-react/src/components/settings/**` or anything under `.superpowers/`** — a concurrent settings-guards plan owns them. In particular `settings.css:342-344`'s `.pf-probes-card { position: relative }` **stays where it is**; this plan overrides its *appearance* from `wizard.css` under a `.pf-wizard` scope and never edits the file.

---

## Verified facts — checked against live code. Do not re-derive; do not guess.

### The class census — the brief's list is off by one, in both directions

Scanning **every string literal** (not just `className={…}` attributes) in `src/components/wizard/**/*.tsx`, excluding tests, yields **51 distinct `pf-*` tokens**. Eight already resolve:

| Class | Already defined at |
|---|---|
| `pf-btn` | `dashboard.css:118-136` |
| `pf-fit` | `dashboard.css:85-92` |
| `pf-field`, `pf-field-column`, `pf-field-label` | `settings.css:76-94` |
| `pf-input` | `settings.css:95-103` |
| `pf-field-hint` | `settings.css:192-196` |
| `pf-probes-card` | `settings.css:342-344` (`position: relative` only) |

**`pf-field-hint` is in the brief's undefined list but IS defined** (`settings.css:192-196`, `color: var(--text-dim); font: 500 13px "Barlow"; line-height: 1.6`). **`pf-probes-card` is missing from the brief's list and is defined, but only structurally** — it has no card appearance. The count of genuinely unstyled classes is still **43**, and this plan defines all 43.

**The attribute-only scan the brief implies would miss two of them.** `InstallProgress.tsx:81-83` builds its class in a plain `const`:

```tsx
const barClassName = prefersReducedMotion()
  ? "pf-install-progress-bar pf-install-progress-bar-reduced-motion"
  : "pf-install-progress-bar";
```

`pf-install-progress-bar` and `pf-install-progress-bar-reduced-motion` never appear inside a `className=`. **The guard test in Task 6 scans string literals, not attributes, precisely because of this** — and asserts that specific class is found, so the scanner can't silently regress to attribute-only.

### Stylesheet, not inline styles — settled by the code

There is **exactly one** `style={{…}}` in the entire wizard tree: `InstallProgress.tsx:95`, `style={{ width: `${percent}%` }}` — a data-driven width that cannot be a static rule and correctly stays inline. Every other one of the 19 wizard components is 100% class-driven. `GrillGauge.tsx`'s heavy-inline authoring is a **dashboard** idiom (`dashboard.css:1-4` says so explicitly: *"Most styling is inline (mirroring the design's inline-style authoring)"*), and it exists because that surface was transcribed from an inline-styled design mock. The wizard was not. **A stylesheet is the answer, and mixing is not on the table.**

### Import convention — component-level, and it matters for the cascade

| Stylesheet | Imported from | Call sites |
|---|---|---|
| `theme.css`, `dashboard/dashboard.css`, `settings/settings.css` | `src/main.tsx:4-6` | 1 each |
| `shell/shell.css` | `AppShell.tsx:7`, `NavBar.tsx:3`, `Banners.tsx:1`, `TimerBar.tsx:7`, `TimerModal.tsx:4` | 5 |
| `history/historyChart.css` | `HistoryChart.tsx:4` | 1 |

The two **newer** surfaces (shell, history) import from their own components; the two older ones go through `main.tsx`. **Take the newer convention: `import "./wizard.css";` in `WizardShell.tsx`.** Three reasons: (1) `WizardShell.tsx` is the module that also exports `WizardError` and `HydrateFallback`, so loading that module is exactly the condition under which any wizard class can appear; (2) the wizard is the one route outside `AppShell` and nothing else consumes its vocabulary; (3) it matches how the last two stylesheets landed.

**The consequence, which drives a hard rule below.** Injection order between a `main.tsx` import and a component-module import is a bundler-internal detail, not a contract. Therefore:

> **Every rule in `wizard.css` that overrides a rule in `settings.css` or `dashboard.css` must win on SPECIFICITY, never on source order.** In practice: scope it under `.pf-wizard` (0,2,0 beats 0,1,0). This is not stylistic — an order-dependent override is a latent bug that appears when the chunking changes.

### `.pf-field` has three children in `PortForm` — a real layout hazard

`settings.css:76-81` defines `.pf-field` as a two-column row: `display: flex; align-items: center; justify-content: space-between; gap: 16px`. That is right for `SelectField` and `ConfigOptionField`, which put exactly a label and a control inside it.

`PortForm.tsx:47-134` puts **three** children in each one — label, control, **and a long `.pf-field-hint`** carrying the verbatim manifest description. The `type` field's hint is ~450 characters of `dangerouslySetInnerHTML` with `<strong>`/`<br>` markup. In a `space-between` row that becomes a crushed third column. **Task 3 flips `.pf-field` to a stack inside `.pf-port-form` / `.pf-device-form` only.** This is the one place where a stylesheet-only fix needs a deliberate override rather than an addition — noted here because it is the thing most likely to be missed.

`BluetoothPicker.tsx:29` and `ThermoworksPicker.tsx:36` already write `className="pf-field pf-field-column"` and are unaffected. `I2cBusPicker.tsx:50` / `UsbSerialPicker.tsx:39` put the hint outside the `.pf-field`, in a plain `.pf-field-column` wrapper, and are also unaffected.

### `.pf-btn` is a shell, not a button

`dashboard.css:118-133` gives `.pf-btn` a cursor, a 2px **colourless** border (`border-color` unset → `currentColor`), a 16px radius, flex centring, `font-weight: 700`, **`font-size: 25px`** and no padding, no background, no colour. The dashboard's `ControlButtons.tsx` supplies those inline per button. **Nothing in the wizard does.** So `.pf-btn` in the wizard today is a 25px-tall-text element with a text-coloured hairline and zero padding. The wizard defines a complete treatment under `.pf-wizard .pf-btn`, matched to `shell.css:211-223`'s `.pf-timer-btn` and `dashboard.css:230-249`'s `.pf-modal-btn` so it reads as the same object family.

### Three dialogs ship with no scrim element — and the obvious CSS fix is wrong

`WizardShell.tsx:164-176` (`pf-wizard-modal pf-wizard-system-active-modal`) and `InstallProgress.tsx:57-78` (`pf-install-reboot-modal`) declare `role="dialog" aria-modal="true"` on a **bare `<div>`**. Unlike the dashboard, which wraps `.pf-modal` in a `.pf-modal-scrim` div (`dashboard.css:165-182`), there is **no backdrop element** — and this plan changes no markup.

**Do not reach for `::before { position: fixed; inset: 0; z-index: -1 }`.** Within a stacking context, CSS painting order is: (1) the context-forming element's own background and borders, **then** (2) negative-`z-index` descendants. A `position: fixed; z-index: 20` modal forms a stacking context, so a `-1` pseudo-element would paint **above** the card's own background and tint the card itself dark. Use the spread-shadow scrim instead — `box-shadow: … , 0 0 0 100vmax rgba(0,0,0,.6)` — which paints only **outside** the border box and, being ink rather than layout, cannot create scrollbars. Task 5 does this.

### Flask reference — what to match, where React diverges

`blueprints/wizard/templates/wizard/wizard.html` is Bootstrap 4: a left `nav-pills` sidebar (Start / Platform / Probe Input / Display / Hopper Sensor / Finish) beside `card` + `card-header bg-primary` + `card-body` + `card-footer` panels, with `btn-danger` Cancel and `btn-primary` Next in a `btn-toolbar float-right`. **Do not port a single Bootstrap class.** What to carry across:

| Flask | React equivalent | Notes |
|---|---|---|
| `_macro_wizard_card.html:5-21` — a `media` object: photo on the left, `h5` friendly name + description + notes on the right | `.pf-module-details` grid, photo in column 1 spanning all rows | Same information architecture; the photo is the component-identification mechanism and must stay prominent |
| `<span class="badge badge-warning">NOTE:</span>` before the notes text (`:16`) | `.pf-module-notes::before { content: "NOTE: " }` + amber tint | `::before` content is not in `textContent`, so no testing-library or Playwright text assertion is affected |
| `_macro_wizard_card.html:23-30` — a 3-column `Setting / Options / Description` table per module | `.pf-module-deps` / `.pf-module-config` as label-over-control stacks | **Deliberate divergence.** React already renders deps as `.pf-field` rows with the description as a sibling `.pf-field-hint`; rebuilding the table would need markup changes, which this plan does not make |
| Left `nav-pills` step list | Horizontal `.pf-wizard-steps` pill strip in the header | **Deliberate divergence**, forced by the fixed 720px height: a vertical rail plus a card would not leave a usable content box |
| `wizard-finish.html:15-17` — `progress-bar progress-bar-striped progress-bar-animated` | `.pf-install-progress-bar` with an animated 28px stripe gradient | Same read; reduced-motion kills the animation, which Bootstrap's never did |
| `wizard-finish.html:22-24` — a collapsible "Show Output" textarea | **Not built.** `InstallProgress.tsx` does not render `status.output` at all | Out of scope: it would be a markup change. Record as backlog |

### Layout budget at 1280×720

Six step labels (`Welcome`, `Grill Platform`, `Probes`, `Display`, `Distance / Hopper`, `Finish`) as 13px pills with 12px side padding and 6px gaps measure roughly 515px; plus the ~130px title and the ~110px Exit button and 40px of padding, ~795px of 1280. Fits on one row with room to spare; `flex-wrap: wrap` is kept as a safety valve, and Task 7 asserts they are on one row and do not touch. Vertical budget: header ~56px + footer ~64px leaves ~600px of scrolling content.

---

## Coordination — who owns what

| Plan / agent | Owns | Overlap |
|---|---|---|
| Settings-guards plan (in flight, same workspace) | `web-react/src/components/settings/**`, `.superpowers/**` | **None.** This plan reads `settings.css` and overrides two of its rules from `wizard.css` under a `.pf-wizard` scope. **It never edits `settings.css`.** |
| `2026-07-25-wizard-critical-fixes.md` | `components/wizard/**`, `helpers/wizard/**`, `blueprints/api_wizard/` | **Landed** — its `.pf-probes-card { position: relative }` is already in `settings.css:342-344`. Its Task 3 Step 5 note *"the wizard has no CSS at all — not this slice's job"* is the finding this plan closes. |
| `2026-07-25-react-dashboard-slice.md` | `components/dashboard/**`, `dashboard.css`, `playwright.config.ts` | **`playwright.config.ts` is shared.** This plan does not modify it (the default project's viewport is already `1280×720` and `testDir` already picks up new specs). If the dashboard slice is mid-flight and has converted `playwright.config.ts` to a `projects` array, Task 7's spec must be added to the `app` project's `testIgnore` exclusion list rather than left to the default — check before writing. |

**Files this plan owns exclusively:** `web-react/src/components/wizard/wizard.css` (new), `web-react/src/components/wizard/wizardStyles.test.ts` (new), `web-react/tests/e2e/wizard-layout.spec.ts` (new). **Plus one line** in `web-react/src/components/wizard/WizardShell.tsx` (the import) and, if absent, one line in `web-react/.gitignore`.

**Not touched, by decision:** every wizard `.tsx` except that one import line; `settings.css`; `dashboard.css`; `theme.css`; `shell.css`; `main.tsx`.

---

## File Structure

**Create**
- `web-react/src/components/wizard/wizard.css` — the entire stylesheet. Built across Tasks 1–5.
- `web-react/src/components/wizard/wizardStyles.test.ts` — node-project guard. Grows per task (Tasks 1–5), then is replaced by the general scanner in Task 6.
- `web-react/tests/e2e/wizard-layout.spec.ts` — the 1280×720 geometry assertions + screenshot artifacts. Task 7.

**Modify**
- `web-react/src/components/wizard/WizardShell.tsx` — one line: `import "./wizard.css";`. Task 1.
- `web-react/.gitignore` — add `tests/e2e/artifacts/` if not already present (the dashboard slice may have added it). Task 7.

**Never modified by this plan**
- `web-react/src/components/settings/settings.css`, `web-react/src/components/dashboard/dashboard.css`, `web-react/src/theme.css`, `web-react/src/main.tsx`, and all 19 wizard component files other than the single import.

---

## Verification — what each part buys, and what it cannot see

Four mechanisms. **All four are required**; none of them is sufficient alone, and the plan is explicit about why.

### (1) Class-coverage guard — `wizardStyles.test.ts` (Task 6). Cheap, permanent, narrow.

Scans every string literal in `src/components/wizard/**/*.tsx` for `pf-*` tokens and asserts each has a **non-empty** rule (a selector block that declares at least one property — `.foo {}` does not count) somewhere in `src/**/*.css`; and that every wizard-owned token (`pf-wizard-*`, `pf-module-*`, `pf-install-*`, `pf-discovery-*`, `pf-port-form`, `pf-device-form`, `pf-form-actions`, `pf-probes-table`, `pf-btn-primary`) is declared **in `wizard.css` specifically**; and that `WizardShell.tsx` imports the file.

- **Buys:** exactly the defect that occurred, permanently, in milliseconds, on every `bun run test`. Catches a future component adding a class nobody styles, a rename that orphans a rule, and — via the import assertion — the "every rule exists and none of them load" failure, which is the same defect with a different cause. That import check is the both-ends-of-the-path rule applied to a CSS seam.
- **Cannot see:** whether any rule is *correct*. `.pf-module-card { padding: 0 }` passes. It says nothing about layout, contrast, overlap or appearance. It is a spelling checker, not a proof-reader.

### (2) Playwright geometry — `wizard-layout.spec.ts` at 1280×720 (Task 7). Catches collapse.

Asserts: no page scroll in either axis; header at `y ≈ 0`; footer bottom flush with 720; six step indicators on **one row** with a positive gap between every adjacent pair (this is literally the `WelcomeGrill Platform…` defect, expressed numerically); the active indicator's `background-color` differs from an inactive one's; `.pf-wizard-content` has ≥16px of padding on the left and top; `.pf-btn`'s computed `font-size ≤ 20px`, `padding-top ≥ 6px` and a non-transparent background (i.e. the bare 25px `dashboard.css` shell was actually overridden); `.pf-module-card` has non-zero padding and a `background-color` different from `--page`.

- **Buys:** every "the CSS is present but the layout collapsed" failure, in the real engine, with real font metrics and the real cascade. It is the only mechanism that proves the stylesheet actually loads and applies at runtime.
- **Cannot see:** aesthetics, colour harmony, contrast ratios, whether the type scale reads well. It also **runs against the live prototype backend** (`control.py` + gunicorn on `:5000`) under `workers: 1` — so it is not runnable in an agent worktree that lacks Chromium or the backend. **Per the standing repo rule, re-run it in the main checkout before merging.**
- **Cannot reach three surfaces at all:** the 409 "grill is active" modal needs a running grill, and the reboot dialog needs the real installer to have run. Task 7 covers their *rules* with a clearly-labelled synthetic probe (inject a `<div class="pf-wizard-modal">` and read its computed `position`/`z-index`/`background-color`), which proves the rules resolve but **not** that they look right in situ. Those three are otherwise on mechanism (4) alone. This is stated plainly rather than papered over.

### (3) Screenshots — artifacts, **not gates** (Task 7).

The spec writes `tests/e2e/artifacts/wizard-<step>-1280x720.png` for all six steps. **No `toHaveScreenshot()`.** `web-react/index.html:7-11` loads Barlow and Barlow Semi Condensed from `fonts.googleapis.com`, so glyph rendering depends on the network and on the host font stack; a pixel gate would be flaky everywhere and would fail hardest on exactly the typography it exists to protect. The sibling `2026-07-25-react-dashboard-slice.md` already ratified this reasoning for the dashboard; this plan follows it rather than re-litigating it.

- **Buys:** a reviewer artifact. Flip through six PNGs, see the whole surface, no dev server needed.
- **Cannot:** fail a build. It is evidence for a human, nothing more.

### (4) Mandatory human visual checkpoint at 1280×720 (Task 8). The only mechanism that can say "it looks right".

An explicit, non-skippable plan step with a 12-item checklist, run against `bun run dev` in a real browser window sized to exactly 1280×720. **This is the gate for appearance.** Mechanisms (1)–(3) exist to keep it from having to be repeated on every future change; they do not replace it now.

### What nothing here can verify

Colour contrast against WCAG, behaviour of the accent swaps (`data-accent="ice"|"crimson"` — no UI in the wizard sets them), rendering with Barlow unavailable (offline first-boot is a plausible real scenario for this exact screen and is **not** covered), and appearance on the 800×480 on-device panel. Record these as backlog; do not claim them.

---

## Parallelization

Isolated jj workspaces per concurrent task; **disjoint file lists are necessary but not sufficient** — two agents editing the same checkout race regardless of which files they name.

- **Wave 0 — Task 1 alone.** It creates `wizard.css` and adds the import. Everything else depends on the file existing.
- **Wave 1 — Tasks 2, 3, 4, 5: strictly sequential, no concurrency available.** All four append to the **same file**, `wizard.css`. There is no way to parallelize them without a merge conflict on every task, and CSS conflicts resolve silently-wrong (a duplicated selector is valid CSS). Run them in order: 2 → 3 → 4 → 5. Each is small; the whole wave is four commits.
- **Wave 2 — Task 6 ∥ Task 7.** Genuinely independent: Task 6 is `src/components/wizard/wizardStyles.test.ts` (node project, no browser), Task 7 is `tests/e2e/wizard-layout.spec.ts` (Chromium + live backend) plus `.gitignore`. Two isolated workspaces, no shared file. **Task 7's workspace needs Chromium and the prototype backend**; if it does not have them, the task must be run in the main checkout instead.
- **Wave 3 — Task 8 alone.** A human. Nothing runs beside it, and nothing merges before it signs off.

**Cross-plan:** this plan can run concurrently with the settings-guards plan throughout (fully disjoint files). It can run concurrently with the dashboard slice **except** that Task 7 must check `playwright.config.ts`'s current shape before adding a spec — see *Coordination*.

---

### Task 1: The stylesheet, the import, and the chrome layer

**Files:** Create `web-react/src/components/wizard/wizard.css`, `web-react/src/components/wizard/wizardStyles.test.ts`; Modify `web-react/src/components/wizard/WizardShell.tsx` (one line).

**Interfaces:** Establishes `.pf-wizard` as a `position: fixed; inset: 0` flex column with pinned header/footer and a single scrolling content region, and gives `.pf-btn` a complete wizard treatment. Every later task styles *inside* that box.

**Classes closed (17):** `pf-wizard`, `pf-wizard-header`, `pf-wizard-title`, `pf-wizard-steps`, `pf-wizard-step-indicator`, `pf-wizard-exit`, `pf-wizard-content`, `pf-wizard-step`, `pf-wizard-step-title`, `pf-wizard-step-placeholder`, `pf-wizard-step-finish`, `pf-wizard-placeholder-message`, `pf-wizard-finish-note`, `pf-wizard-finish-error`, `pf-wizard-footer`, `pf-wizard-error`, `pf-btn-primary`.

- [ ] **Step 1: Write the failing guard.** Create `web-react/src/components/wizard/wizardStyles.test.ts`:

```ts
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
```

- [ ] **Step 2: Run it, confirm it fails.** `bun run test src/components/wizard/wizardStyles.test.ts` — expect `ENOENT … wizard.css`.

- [ ] **Step 3: Create `web-react/src/components/wizard/wizard.css`** with exactly this content:

```css
/* PiFire Setup Wizard.
   The wizard is the one route mounted OUTSIDE AppShell (App.tsx:88-96) -- no
   navbar, no timer strip, no banners -- so .pf-wizard is the whole page and owns
   the viewport itself.

   Everything here is built from src/theme.css's tokens and the recipes already
   in dashboard.css / settings.css / shell.css. theme.css declares ONE dark
   palette and swaps only the accent via :root[data-accent]; there is no light
   mode anywhere in this app, so there is none here either.

   CASCADE RULE: this file is imported from WizardShell.tsx while settings.css
   and dashboard.css come in through main.tsx, and the injection order between
   those two is a bundler detail, not a contract. Every rule below that
   OVERRIDES one of theirs is therefore scoped under .pf-wizard so it wins on
   specificity (0,2,0 vs 0,1,0), never on source order. */

/* ---- root ---- */
.pf-wizard {
  position: fixed;
  inset: 0;
  display: flex;
  flex-direction: column;
  background: var(--page);
  color: var(--text);
  font-family: "Barlow", system-ui, sans-serif;
}

/* ---- header chrome: title, step strip, exit ---- */
/* flex: 0 0 auto on the header and footer plus a min-height: 0 scroller between
   them is what keeps the chrome put at 1280x720 while a long step scrolls. */
.pf-wizard-header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 10px 20px;
  background: var(--inset);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.pf-wizard-title {
  font: 700 18px "Barlow";
  color: var(--text);
  white-space: nowrap;
}
.pf-wizard-steps {
  display: flex;
  align-items: center;
  flex: 1 1 auto;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}
/* The six labels are adjacent inline <span>s in the markup
   (WizardShell.tsx:229-233): with no rule they render as one run-on string.
   Pills with their own padding and a gap are what separates them. */
.pf-wizard-step-indicator {
  padding: 5px 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: var(--pill-radius);
  color: var(--text-dim);
  font: 600 13px "Barlow";
  white-space: nowrap;
}
.pf-wizard-step-indicator.active {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  border-color: color-mix(in srgb, var(--accent) 55%, transparent);
  color: var(--text);
}
.pf-wizard-exit {
  flex: 0 0 auto;
  margin-left: auto;
}

/* ---- content: the only scrolling region ---- */
.pf-wizard-content {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 22px 28px 28px;
}
.pf-wizard-step {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 880px;
  margin: 0 auto;
}
.pf-wizard-step-title {
  margin: 0;
  font: 700 24px "Barlow";
  color: var(--text);
}
/* Body copy only. :not([class]) keeps this off .pf-wizard-finish-note and
   .pf-wizard-finish-error, which are also direct <p> children of the step and
   have their own treatment below. */
.pf-wizard-step > p:not([class]) {
  margin: 0;
  max-width: 68ch;
  color: var(--text-dim);
  font: 500 15px "Barlow";
  line-height: 1.7;
}
.pf-wizard-step > p:not([class]) strong {
  color: var(--text);
}
/* Both of these steps are short; a floor stops the footer jumping up the page
   as the user moves between a one-paragraph step and a full module card. */
.pf-wizard-step-placeholder,
.pf-wizard-step-finish {
  min-height: 220px;
}
.pf-wizard-placeholder-message {
  margin: 0;
  color: var(--text-dim);
  font: 500 15px "Barlow";
  line-height: 1.7;
}
.pf-wizard-finish-note {
  margin: 0;
  padding: 10px 14px;
  border-left: 3px solid #ffb020;
  border-radius: 0 10px 10px 0;
  background: rgba(255, 176, 32, 0.1);
  color: #ffce6a;
  font: 600 14px "Barlow";
  line-height: 1.6;
}
/* Also used for the module-switch failures in DisplayStep/DistanceStep/
   GrillPlatformStep and, at WizardShell.tsx:252, for the exit error -- which is
   a direct child of .pf-wizard, outside the padded content box. */
.pf-wizard-finish-error {
  margin: 0;
  color: #ff8b82;
  font: 600 14px "Barlow";
}
.pf-wizard > .pf-wizard-finish-error {
  padding: 10px 28px 0;
}

/* ---- footer chrome ---- */
.pf-wizard-footer {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 28px;
  background: var(--inset);
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

/* ---- buttons ---- */
/* dashboard.css:118-133's .pf-btn is a bare shell: 25px bold text, a 2px
   currentColor border, no padding, no background -- the dashboard's control
   buttons supply the rest inline, and nothing in the wizard does. This is the
   complete treatment, matched to shell.css:211-223 (.pf-timer-btn) and
   dashboard.css:230-249 (.pf-modal-btn) so it reads as the same object family.
   inline-flex (not the inherited flex) keeps a button from stretching to full
   width when it is a child of a flex column, e.g. Finish. */
.pf-wizard .pf-btn {
  display: inline-flex;
  border-width: 1px;
  border-color: rgba(255, 255, 255, 0.14);
  border-radius: 12px;
  padding: 9px 18px;
  background: #1d1813;
  color: #e8dfd1;
  font: 700 15px "Barlow";
  letter-spacing: 0.3px;
  text-decoration: none;
}
.pf-wizard .pf-btn:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  color: var(--text);
}
.pf-wizard .pf-btn:disabled {
  opacity: 0.55;
  cursor: default;
}
.pf-wizard .pf-btn-primary {
  background: var(--accent);
  border-color: transparent;
  color: #1a0f04;
}
.pf-wizard .pf-btn-primary:hover:not(:disabled) {
  background: var(--accent-1);
  color: #1a0f04;
}

/* ---- route-level error ---- */
/* WizardShell.tsx:57 renders this on .pf-fit, OUTSIDE .pf-wizard: the loader
   failed, so the shell never existed. It must NOT be scoped under .pf-wizard. */
.pf-wizard-error {
  padding: 24px;
  color: var(--text-dim);
  font: 600 16px "Barlow";
  text-align: center;
}
```

- [ ] **Step 4: Add the import.** In `web-react/src/components/wizard/WizardShell.tsx`, after the existing `import { ProbesStep } from "./steps/ProbesStep";` line, add:

```ts
import "./wizard.css";
```

  Biome's import sorter puts side-effect imports last within the group; run `bun run format` and let it place the line rather than arguing with it.

- [ ] **Step 5: Run the gate.** `bun run typecheck && bun run lint && bun run test && bun run build`. Expected: all green, `bun run lint` exits 0 with exactly the 2 pre-existing `react-refresh` warnings. **Commit.**

---

### Task 2: The module card

**Files:** Modify `web-react/src/components/wizard/wizard.css`, `web-react/src/components/wizard/wizardStyles.test.ts`.

**Interfaces:** `.pf-module-card` becomes the shared card surface for the Grill Platform, Display and Distance steps (`ModuleCard.tsx` is rendered by all three), and `.pf-module-image` / `-name` / `-description` / `-notes` are reused verbatim by `DeviceForm.tsx:23-32`.

**Classes closed (9):** `pf-module-card`, `pf-module-details`, `pf-module-image`, `pf-module-name`, `pf-module-description`, `pf-module-notes`, `pf-module-deps`, `pf-module-config`, `pf-form-actions`.

- [ ] **Step 1: Extend the guard.** In `wizardStyles.test.ts`, add below the `CHROME` array:

```ts
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
```
  and a second `it` inside the existing `describe`:

```ts
  it("declares a non-empty rule for every module-card class", () => {
    const declared = declaredClasses(readFileSync(WIZARD_CSS, "utf8"));
    expect(MODULE_CARD.filter((c) => !declared.has(c))).toEqual([]);
  });
```

- [ ] **Step 2: Run, confirm fail.** `bun run test src/components/wizard/wizardStyles.test.ts` — the new `it` fails listing all nine.

- [ ] **Step 3: Append to `wizard.css`:**

```css
/* ---- module card (ModuleCard.tsx: grill platform, display, distance) ---- */
.pf-module-card {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 18px 20px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: var(--card-radius);
  background: var(--card);
}
/* Flask's media object (_macro_wizard_card.html:5-21): vendor photo on the
   left, everything else stacked on the right. The photo is the wizard's
   component-IDENTIFICATION mechanism -- a user matches the PCB in their hand to
   it -- so it stays prominent rather than becoming a thumbnail. The children
   are a variable-length sibling list (image? name, description?, notes?, deps?,
   config?), so the image is placed explicitly and everything else is swept into
   column 2. */
.pf-module-details {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  column-gap: 18px;
  row-gap: 10px;
  align-items: start;
  padding-top: 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
.pf-module-details > :not(.pf-module-image) {
  grid-column: 2;
}
/* moduleImageUrl() returns "" for a module with no photo, and ModuleCard.tsx:116
   then renders no <img> at all. Collapse to one column so the text does not sit
   behind an 18px gutter of nothing. */
.pf-module-details:not(:has(.pf-module-image)) {
  grid-template-columns: minmax(0, 1fr);
}
.pf-module-details:not(:has(.pf-module-image)) > * {
  grid-column: 1;
}
.pf-module-image {
  grid-column: 1;
  grid-row: 1 / -1;
  width: 132px;
  height: auto;
  padding: 6px;
  border-radius: 12px;
  background: var(--inset);
}
.pf-module-name {
  margin: 0;
  color: var(--text);
  font: 700 18px "Barlow";
}
.pf-module-description {
  margin: 0;
  color: var(--text-dim);
  font: 500 14px "Barlow";
  line-height: 1.6;
}
/* Flask badged this: <span class="badge badge-warning">NOTE:</span> then the
   text in <i class="small"> (_macro_wizard_card.html:16-17). ::before content is
   not part of textContent, so no testing-library or Playwright text assertion
   sees it. */
.pf-module-notes {
  margin: 0;
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(255, 176, 32, 0.1);
  color: #ffce6a;
  font: 500 13px "Barlow";
  line-height: 1.6;
}
.pf-module-notes::before {
  content: "NOTE: ";
  font-weight: 700;
  letter-spacing: 0.5px;
}
/* Flask rendered these as a 3-column Setting/Options/Description table
   (_macro_wizard_card.html:23-30). React already emits label + control as a
   .pf-field row with the description as a sibling .pf-field-hint, so this is a
   stack of those rows -- a deliberate divergence, and one that needs no markup
   change. */
.pf-module-deps,
.pf-module-config {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding-top: 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
/* Cancel/Save row shared by PortForm.tsx:135 and DeviceForm.tsx:55. */
.pf-form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 4px;
}
```

- [ ] **Step 4: Run the gate.** `bun run typecheck && bun run lint && bun run test && bun run build`. **Commit.**

---

### Task 3: The probes step — cards, table, and the three-child field fix

**Files:** Modify `web-react/src/components/wizard/wizard.css`, `web-react/src/components/wizard/wizardStyles.test.ts`.

**Interfaces:** Gives `.pf-probes-card` its appearance without editing `settings.css`, and converts `.pf-field` from a row to a stack **inside the two add/edit dialogs only**.

**Classes closed (3):** `pf-probes-table`, `pf-port-form`, `pf-device-form`. Plus a scoped appearance layer over the existing `.pf-probes-card`.

- [ ] **Step 1: Extend the guard.** Add to `wizardStyles.test.ts`:

```ts
const PROBES = ["pf-probes-table", "pf-port-form", "pf-device-form"];
```
  and:

```ts
  it("declares a non-empty rule for every probes-step class", () => {
    const declared = declaredClasses(readFileSync(WIZARD_CSS, "utf8"));
    expect(PROBES.filter((c) => !declared.has(c))).toEqual([]);
  });

  // settings.css:342-344 owns `.pf-probes-card { position: relative }` (it is the
  // containing block for ConfirmAction's absolutely-positioned scrim) and this
  // plan does not edit that file. The card's APPEARANCE is added here instead,
  // scoped so it wins on specificity rather than on injection order.
  it("scopes its .pf-probes-card override under .pf-wizard", () => {
    const css = readFileSync(WIZARD_CSS, "utf8");
    expect(css).toContain(".pf-wizard .pf-probes-card");
  });
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Append to `wizard.css`:**

```css
/* ---- probes step: DevicesCard / PortsCard ---- */
/* settings.css:342-344 already gives .pf-probes-card `position: relative`, which
   it needs as the containing block for ConfirmAction's position:absolute scrim
   (dashboard.css:165-172). That rule stays where it is; this adds appearance
   only, scoped .pf-wizard (0,2,0) so it beats the bare class (0,1,0) on
   specificity and cannot be reordered out of effect. */
.pf-wizard .pf-probes-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px 20px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: var(--card-radius);
  background: var(--card);
}
.pf-wizard .pf-probes-card > h3 {
  margin: 0;
  color: var(--text);
  font: 700 18px "Barlow";
}
.pf-wizard .pf-probes-card > [role="alert"] {
  margin: 0;
  color: #ff8b82;
  font: 600 14px "Barlow";
}

.pf-probes-table {
  width: 100%;
  border-collapse: collapse;
}
.pf-probes-table td {
  padding: 8px 10px 8px 0;
  vertical-align: middle;
  color: var(--text);
  font: 500 14px "Barlow";
}
.pf-probes-table tbody tr {
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
.pf-probes-table tbody tr:first-child {
  border-top: none;
}
.pf-probes-table td:last-child {
  text-align: right;
  white-space: nowrap;
}
.pf-probes-table img {
  border-radius: 8px;
  background: var(--inset);
}
/* Edit/Delete are bare <button type="button"> with no class of their own
   (PortsCard.tsx:98-103, DevicesCard.tsx:132-137). Same recipe as
   settings.css:131-144's .pf-field-column > button, so they match the Discover
   and Scan buttons the pickers already inherit from that rule. */
.pf-probes-table button {
  margin-left: 6px;
  padding: 5px 12px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 10px;
  background: var(--inset);
  color: var(--text);
  font: 600 13px "Barlow";
  cursor: pointer;
}
.pf-probes-table button:hover:not(:disabled) {
  border-color: var(--accent);
}
.pf-probes-table button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ---- add/edit dialogs ---- */
/* Both are inline panels, not overlays -- they render in the card's own flow
   (PortsCard.tsx:122-138, DevicesCard.tsx:163-182). The accent-tinted border is
   what marks them as the thing the user is currently working in. */
.pf-port-form,
.pf-device-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 18px;
  border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
  border-radius: 14px;
  background: var(--inset);
}
/* PortForm's rows carry THREE children -- label, control, and a long
   .pf-field-hint of verbatim manifest copy (PortForm.tsx:47-134; the `type`
   hint is ~450 characters of markup). settings.css:76-81's .pf-field is a
   space-between ROW, which crushes that hint into a third column. Inside these
   two dialogs a field is a stack instead. Scoped, so every other .pf-field in
   the app -- including the ones in .pf-module-deps -- stays a row. */
.pf-port-form .pf-field,
.pf-device-form .pf-field {
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
}
.pf-port-form .pf-input,
.pf-device-form .pf-input {
  width: 100%;
}
.pf-port-form [role="alert"],
.pf-device-form [role="alert"] {
  margin: 0;
  color: #ff8b82;
  font: 600 14px "Barlow";
}
/* DeviceForm reuses .pf-module-image (DeviceForm.tsx:24-28) inside a flex
   column, where the grid placement above is inert and align-items: stretch
   would otherwise fight the fixed width. */
.pf-device-form .pf-module-image {
  width: 96px;
  align-self: flex-start;
}
```

- [ ] **Step 4: Run the gate.** `bun run typecheck && bun run lint && bun run test && bun run build`. **Commit.**

---

### Task 4: Discovery results

**Files:** Modify `web-react/src/components/wizard/wizard.css`, `web-react/src/components/wizard/wizardStyles.test.ts`.

**Interfaces:** `.pf-discovery-group-items` is used both by `DiscoveryPanel.tsx:24` (inside `.pf-discovery-panel`) and standalone by `BluetoothPicker.tsx:43` (inside a `label.pf-field.pf-field-column`), so its rule must not depend on the panel wrapper.

**Classes closed (4):** `pf-discovery-panel`, `pf-discovery-group`, `pf-discovery-group-title`, `pf-discovery-group-items`.

- [ ] **Step 1: Extend the guard.** Add:

```ts
const DISCOVERY = [
  "pf-discovery-panel",
  "pf-discovery-group",
  "pf-discovery-group-title",
  "pf-discovery-group-items",
];
```
  and:

```ts
  it("declares a non-empty rule for every discovery class", () => {
    const declared = declaredClasses(readFileSync(WIZARD_CSS, "utf8"));
    expect(DISCOVERY.filter((c) => !declared.has(c))).toEqual([]);
  });
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Append to `wizard.css`:**

```css
/* ---- discovery results (I2C / USB-serial / Bluetooth scans) ---- */
.pf-discovery-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  background: var(--inset);
}
.pf-discovery-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pf-discovery-group-title {
  margin: 0;
  color: var(--text-dim);
  font: 600 11px "Barlow";
  letter-spacing: 2px;
  text-transform: uppercase;
}
/* Standalone in BluetoothPicker.tsx:43 as well as inside .pf-discovery-panel,
   so no wrapper is assumed. */
.pf-discovery-group-items {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
/* Each item is a bare <button type="button"> whose whole job is to be clicked
   to fill the field above it (DiscoveryPanel.tsx:26, BluetoothPicker.tsx:45).
   Pills, because the labels are short device identifiers. */
.pf-discovery-group-items button {
  padding: 6px 14px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: var(--pill-radius);
  background: var(--card);
  color: var(--text);
  font: 600 13px "Barlow";
  text-align: left;
  cursor: pointer;
}
.pf-discovery-group-items button:hover {
  border-color: var(--accent);
  color: var(--accent-1);
}
/* BluetoothPicker puts the result row directly in the field stack, with no
   .pf-discovery-panel to supply the spacing. */
.pf-wizard .pf-field-column > .pf-discovery-group-items {
  margin-top: 8px;
}
```

- [ ] **Step 4: Run the gate.** `bun run typecheck && bun run lint && bun run test && bun run build`. **Commit.**

---

### Task 5: Install progress, reduced motion, and the three modals

**Files:** Modify `web-react/src/components/wizard/wizard.css`, `web-react/src/components/wizard/wizardStyles.test.ts`.

**Interfaces:** Closes the last 10 classes. After this task the class-coverage guard in Task 6 can pass.

**Classes closed (10):** `pf-install-progress`, `pf-install-progress-status`, `pf-install-progress-track`, `pf-install-progress-bar`, `pf-install-progress-bar-reduced-motion`, `pf-install-reboot-modal`, `pf-install-reboot-message`, `pf-install-reboot-actions`, `pf-wizard-modal`, `pf-wizard-system-active-modal`.

- [ ] **Step 1: Extend the guard.** Add:

```ts
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
```
  and two `it`s — the second one is the reduced-motion contract, which a class list alone cannot express:

```ts
  it("declares a non-empty rule for every install/modal class", () => {
    const declared = declaredClasses(readFileSync(WIZARD_CSS, "utf8"));
    expect(INSTALL_AND_MODALS.filter((c) => !declared.has(c))).toEqual([]);
  });

  // InstallProgress.tsx:16-19 reads matchMedia ONCE, at first render, and never
  // updates if the preference changes mid-install. The class is therefore only
  // half the guarantee; the media query is the half that always holds. Both are
  // required.
  it("honours prefers-reduced-motion in the stylesheet, not only via the class", () => {
    const css = readFileSync(WIZARD_CSS, "utf8");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    const block = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(block).toContain(".pf-install-progress-bar");
    expect(block).toContain("animation: none");
  });
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Append to `wizard.css`:**

```css
/* ---- install progress ---- */
/* This replaces the whole step body once /finish succeeds, and WizardShell.tsx:222
   hides the footer at the same time -- there is nothing to go back to, the
   installer is running detached. */
.pf-install-progress {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 880px;
  margin: 0 auto;
  padding: 48px 0;
  text-align: center;
}
.pf-install-progress-status {
  margin: 0;
  color: var(--text);
  font: 700 22px "Barlow";
}
.pf-install-progress-track {
  height: 28px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: var(--pill-radius);
  background: var(--inset);
  overflow: hidden;
}
@keyframes pf-install-stripes {
  from {
    background-position: 0 0;
  }
  to {
    background-position: 28px 0;
  }
}
/* Flask's progress-bar-striped progress-bar-animated (wizard-finish.html:15-17),
   rebuilt on the accent token. The width comes from the one legitimate inline
   style in this whole surface (InstallProgress.tsx:95). */
.pf-install-progress-bar {
  height: 100%;
  background-color: var(--accent);
  background-image: linear-gradient(
    115deg,
    rgba(255, 255, 255, 0.22) 25%,
    transparent 25%,
    transparent 50%,
    rgba(255, 255, 255, 0.22) 50%,
    rgba(255, 255, 255, 0.22) 75%,
    transparent 75%,
    transparent
  );
  background-size: 28px 28px;
  animation: pf-install-stripes 900ms linear infinite;
  transition: width 200ms var(--ease-out-cubic);
}
/* Applied by InstallProgress.tsx:81-83 from a one-shot matchMedia read. Kept
   because it documents the component's intent and works where the media query
   is unsupported -- but the media query below is the one that always holds,
   because the component never re-reads the preference. */
.pf-install-progress-bar-reduced-motion {
  animation: none;
  transition: none;
}
@media (prefers-reduced-motion: reduce) {
  .pf-install-progress-bar {
    animation: none;
    transition: none;
  }
}

/* ---- modals ---- */
/* Three role="dialog" aria-modal="true" elements ship as bare <div>s with NO
   backdrop element of their own (WizardShell.tsx:164-176,
   InstallProgress.tsx:57-78) -- unlike the dashboard, which wraps .pf-modal in a
   .pf-modal-scrim div (dashboard.css:165-182). This plan changes no markup, so
   each dialog paints its own scrim.
   NOT with ::before { z-index: -1 }: a position:fixed element with a z-index
   forms a stacking context, and CSS paints negative-z descendants AFTER that
   element's own background -- the scrim would tint the card itself. A spread
   box-shadow paints strictly OUTSIDE the border box, and being ink rather than
   layout it cannot create a scrollbar. */
.pf-wizard-modal,
.pf-install-reboot-modal {
  position: fixed;
  top: 50%;
  left: 50%;
  z-index: 20;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  min-width: 320px;
  max-width: 480px;
  padding: 22px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: var(--card-radius);
  background: var(--card);
  box-shadow:
    0 24px 60px rgba(0, 0, 0, 0.55),
    0 0 0 100vmax rgba(0, 0, 0, 0.6);
  text-align: center;
}
.pf-wizard-modal > p,
.pf-install-reboot-message {
  margin: 0;
  max-width: 34ch;
  color: var(--text);
  font: 500 15px "Barlow";
  line-height: 1.6;
}
.pf-install-reboot-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 12px;
}
/* The only one of the three that appears over a step the user can still act on
   (a 409 from /finish while the grill is running). A red edge marks it as a
   block, not a note. */
.pf-wizard-system-active-modal {
  border-color: color-mix(in srgb, #ff5a4d 55%, transparent);
}
```

- [ ] **Step 4: Run the gate.** `bun run typecheck && bun run lint && bun run test && bun run build`. **Commit.**

---

### Task 6: Replace the hand-maintained lists with the real guard

**Files:** Modify `web-react/src/components/wizard/wizardStyles.test.ts`.

**Interfaces:** This is the permanent regression net for the defect that occurred. Tasks 1–5's five hard-coded arrays go away; nothing has to be maintained by hand again.

- [ ] **Step 1: Rewrite `wizardStyles.test.ts` in full:**

```ts
import { readFileSync, readdirSync, statSync } from "node:fs";
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
// attribute. InstallProgress.tsx:81-83 builds its bar class in a plain `const`,
// so an attribute-only scan would miss pf-install-progress-bar and
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
```

- [ ] **Step 2: Run it and confirm it passes.** `bun run test src/components/wizard/wizardStyles.test.ts` — expected `7 passed`.

- [ ] **Step 3: Prove it can fail.** Temporarily delete the `.pf-module-notes { … }` block from `wizard.css`, re-run, and confirm the failure names `pf-module-notes` in **two** tests. Restore the block. **A guard that has never been seen red is not a guard.**

- [ ] **Step 4: Run the gate.** `bun run typecheck && bun run lint && bun run test && bun run build`. **Commit.**

---

### Task 7: Playwright geometry at 1280×720, plus screenshot artifacts

**Files:** Create `web-react/tests/e2e/wizard-layout.spec.ts`; Modify `web-react/.gitignore` (one line, if absent).

**Prerequisite:** the prototype backend (`control.py` + gunicorn on `:5000`) and Chromium. `playwright.config.ts` already pins `viewport: { width: 1280, height: 720 }` and `workers: 1`, and `testDir: "./tests/e2e"` picks the new file up with no config change — **but check first** whether the concurrent dashboard slice has converted that file to a `projects` array (see *Coordination*).

- [ ] **Step 1: Ignore the artifacts.** In `web-react/.gitignore`, if not already present:

```
# Playwright review artifacts (screenshots are for humans, not gates)
tests/e2e/artifacts/
```

- [ ] **Step 2: Write `web-react/tests/e2e/wizard-layout.spec.ts`:**

```ts
import { expect, test } from "@playwright/test";

// Requires the prototype backend running (control.py + gunicorn on :5000).
//
// These are LAYOUT assertions. The rest of the wizard suite asserts on text and
// ARIA roles only, which is exactly why an entirely unstyled wizard shipped with
// a green suite. Everything here fails on the unstyled page.
//
// Screenshots are written as review artifacts. There is deliberately no
// toHaveScreenshot() gate: index.html:7-11 loads Barlow from fonts.googleapis.com,
// so glyph rendering is network- and host-dependent and a pixel gate would be
// flaky on exactly the typography it exists to protect.

test("wizard chrome is styled and fits 1280x720 without page scroll", async ({ page }) => {
  await page.goto("/wizard");
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();

  // 1. No page scroll in either axis. .pf-wizard is position:fixed inset:0 and
  //    only .pf-wizard-content may scroll.
  const overflow = await page.evaluate(() => ({
    y: document.documentElement.scrollHeight - document.documentElement.clientHeight,
    x: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  expect(overflow.y).toBeLessThanOrEqual(0);
  expect(overflow.x).toBeLessThanOrEqual(0);

  // 2. Chrome is pinned: header at the top, footer flush with the bottom edge.
  const header = await page.locator(".pf-wizard-header").boundingBox();
  const footer = await page.locator(".pf-wizard-footer").boundingBox();
  expect(header).not.toBeNull();
  expect(footer).not.toBeNull();
  if (header === null || footer === null) return;
  expect(header.y).toBeLessThanOrEqual(1);
  expect(header.height).toBeGreaterThan(28);
  expect(Math.abs(footer.y + footer.height - 720)).toBeLessThanOrEqual(1);

  // 3. The step indicators. This is literally the shipped defect, in numbers:
  //    six adjacent inline spans rendered as "WelcomeGrill PlatformProbes...".
  const pills = page.locator(".pf-wizard-step-indicator");
  await expect(pills).toHaveCount(6);
  const boxes = await pills.evaluateAll((els) =>
    els.map((el) => {
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width };
    }),
  );
  for (let i = 1; i < boxes.length; i++) {
    // A positive gap, not merely non-overlapping.
    expect(boxes[i].x).toBeGreaterThan(boxes[i - 1].x + boxes[i - 1].w);
    // All on one row.
    expect(Math.abs(boxes[i].y - boxes[0].y)).toBeLessThanOrEqual(1);
  }
  const activeBg = await pills.nth(0).evaluate((el) => getComputedStyle(el).backgroundColor);
  const idleBg = await pills.nth(1).evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(activeBg).not.toBe(idleBg);

  // 4. The content box has real padding. The unstyled page had none.
  const pad = await page.locator(".pf-wizard-content").evaluate((el) => {
    const cs = getComputedStyle(el);
    return { left: Number.parseFloat(cs.paddingLeft), top: Number.parseFloat(cs.paddingTop) };
  });
  expect(pad.left).toBeGreaterThanOrEqual(16);
  expect(pad.top).toBeGreaterThanOrEqual(16);

  // 5. .pf-btn actually got a wizard treatment. dashboard.css:118-133 leaves it
  //    at 25px with no padding and no background; all three must have changed.
  const btn = await page.getByRole("button", { name: "Next" }).evaluate((el) => {
    const cs = getComputedStyle(el);
    return {
      fontSize: Number.parseFloat(cs.fontSize),
      padTop: Number.parseFloat(cs.paddingTop),
      bg: cs.backgroundColor,
    };
  });
  expect(btn.fontSize).toBeLessThanOrEqual(20);
  expect(btn.padTop).toBeGreaterThanOrEqual(6);
  expect(btn.bg).not.toBe("rgba(0, 0, 0, 0)");

  await page.screenshot({ path: "tests/e2e/artifacts/wizard-welcome-1280x720.png" });
});

test("module cards and probe cards read as cards, and every step is captured", async ({ page }) => {
  await page.goto("/wizard");
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();

  const pageBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);

  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Grill Platform" })).toBeVisible();

  const card = await page.locator(".pf-module-card").first().evaluate((el) => {
    const cs = getComputedStyle(el);
    return {
      padTop: Number.parseFloat(cs.paddingTop),
      padLeft: Number.parseFloat(cs.paddingLeft),
      radius: Number.parseFloat(cs.borderTopLeftRadius),
      bg: cs.backgroundColor,
    };
  });
  expect(card.padTop).toBeGreaterThanOrEqual(12);
  expect(card.padLeft).toBeGreaterThanOrEqual(12);
  expect(card.radius).toBeGreaterThanOrEqual(8);
  expect(card.bg).not.toBe(pageBg);
  expect(card.bg).not.toBe("rgba(0, 0, 0, 0)");
  await page.screenshot({ path: "tests/e2e/artifacts/wizard-grillplatform-1280x720.png" });

  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Probes" })).toBeVisible();
  const probesBg = await page
    .locator(".pf-probes-card")
    .first()
    .evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(probesBg).not.toBe(pageBg);
  expect(probesBg).not.toBe("rgba(0, 0, 0, 0)");
  await page.screenshot({ path: "tests/e2e/artifacts/wizard-probes-1280x720.png" });

  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Display" })).toBeVisible();
  await page.screenshot({ path: "tests/e2e/artifacts/wizard-display-1280x720.png" });

  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Distance / Hopper" })).toBeVisible();
  await page.screenshot({ path: "tests/e2e/artifacts/wizard-distance-1280x720.png" });

  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Finish" })).toBeVisible();
  await page.screenshot({ path: "tests/e2e/artifacts/wizard-finish-1280x720.png" });
  // Do NOT click Finish -- it fires the real installer.

  // Restore: stepping forward flushed a draft (WizardShell.tsx:80). Leave the
  // backend as found, exactly as tests/e2e/wizard.spec.ts does.
  const clear = await page.request.post("/api/wizard/draft", { data: { clear: true } });
  expect(clear.ok()).toBeTruthy();
});

// The three role="dialog" elements cannot be reached from a test run: the 409
// dialog needs a RUNNING grill and the reboot dialog needs the real installer to
// have finished. This is a synthetic probe -- it proves the rules resolve and are
// not empty, and it proves NOTHING about how they look in situ. Those three
// surfaces are on the human checkpoint (Task 8).
test("modal rules resolve to an overlay (synthetic probe, not an integration test)", async ({
  page,
}) => {
  await page.goto("/wizard");
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();

  const style = await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.className = "pf-wizard-modal pf-wizard-system-active-modal";
    document.querySelector(".pf-wizard-content")?.appendChild(probe);
    const cs = getComputedStyle(probe);
    const out = {
      position: cs.position,
      zIndex: cs.zIndex,
      background: cs.backgroundColor,
      boxShadow: cs.boxShadow,
    };
    probe.remove();
    return out;
  });
  expect(style.position).toBe("fixed");
  expect(Number(style.zIndex)).toBeGreaterThan(0);
  expect(style.background).not.toBe("rgba(0, 0, 0, 0)");
  // The spread-shadow scrim; see wizard.css's modal comment for why it is not a
  // ::before pseudo-element.
  expect(style.boxShadow).toContain("px");
});
```

- [ ] **Step 3: Run it.** `bun run test:e2e tests/e2e/wizard-layout.spec.ts` — expected `3 passed`. If Chromium or the backend is unavailable in this workspace, **stop and run this task in the main checkout** rather than reporting a skip as a pass.

- [ ] **Step 4: Prove it can fail.** Temporarily comment out the `.pf-wizard-step-indicator` rule in `wizard.css` and re-run: the first test must fail on the positive-gap assertion. Restore.

- [ ] **Step 5: Run the whole e2e suite** — `bun run test:e2e` — to confirm the new file has not disturbed the serialized shared-instance ordering. Then `bun run typecheck && bun run lint && bun run test && bun run build`. **Commit.**

---

### Task 8: Human visual checkpoint at 1280×720 — MANDATORY

**Files:** none. This task writes no code.

**This is the gate for appearance. It cannot be delegated to a test and it cannot be skipped.** Tasks 6 and 7 exist so that this does not have to be repeated on every future change; they do not substitute for it now.

- [ ] **Step 1: Serve it.** `cd web-react && bun run dev`, with the prototype backend up. Open `http://localhost:5173/wizard` in a browser window sized to **exactly 1280×720** (DevTools device toolbar → Responsive → 1280 × 720, zoom 100%).

- [ ] **Step 2: Walk all six steps and check every line. Every box must be ticked before this plan is done.**

  1. **Welcome** — no page scrollbar; header, step strip and footer all visible without scrolling.
  2. **Step strip** — six separate pills on one row; the current one is accent-tinted and legible; the label text does not run together.
  3. **Header** — "Setup Wizard" left, pills centre, "Exit Setup" hard right, all vertically centred.
  4. **Footer** — "Back" left, "Next" right, flush with the bottom edge; "Back" is visibly disabled on the Welcome step.
  5. **Buttons** — Back / Next / Exit Setup are pill-ish buttons with padding and a fill, not 25px bare text with a hairline. "Next" is accent-filled; "Back" is not.
  6. **Grill Platform** — the module card is a distinct raised surface against the page; the vendor photo is at the left, large enough to identify the PCB; name, description and the amber NOTE block are stacked beside it.
  7. **Grill Platform, no-photo module** — pick a module with no image and confirm the text does not sit behind an empty 18px gutter.
  8. **Probes** — Devices and Ports render as two separate cards; the tables have row separators and right-aligned Edit/Delete buttons that look like buttons.
  9. **Probes → Add Probe** — the add form is a visibly distinct inset panel; **each field is a stack, and the long "Probe types are as follows…" hint wraps to full width instead of being crushed into a column.**
  10. **Probes → Delete** — the `ConfirmAction` scrim covers **the card**, not the whole page and not a stray corner of it.
  11. **Display** — pick a module with config options; `.pf-module-config` is separated from `.pf-module-deps` by its rule, and the Screen Rotation control lines up with the other fields.
  12. **A picker with a Discover button** (I2C or USB serial) — click Discover; the results panel is an inset block with uppercase group titles and pill buttons.

- [ ] **Step 3: Check the two states the e2e run cannot reach.** With DevTools, add the classes by hand to a live element (`$0.className = "pf-wizard-modal pf-wizard-system-active-modal"` on a `<div>` you insert into `.pf-wizard-content`, and likewise `pf-install-reboot-modal`) and confirm: centred, carded, and the rest of the page dimmed behind it by the spread-shadow scrim — **with the card itself NOT dimmed**. That last clause is the specific failure the `::before` approach would have produced.

- [ ] **Step 4: Check reduced motion.** DevTools → Rendering → *Emulate CSS media feature prefers-reduced-motion: reduce*, then reload and trigger the finish step's progress bar (or apply `.pf-install-progress` / `-track` / `-bar` by hand). The stripe animation must be still.

- [ ] **Step 5: Record the sign-off.** Note in the final commit message: viewport confirmed 1280×720, all twelve checklist items plus Steps 3 and 4 passed, and name anything deferred. **If any item fails, it is a bug in this plan's CSS — fix it and re-run Steps 1–4 before signing off.**

---

## Self-Review

**Coverage:** all 43 unstyled classes are assigned to exactly one task — Task 1 (17), Task 2 (9), Task 3 (3), Task 4 (4), Task 5 (10) = 43. Task 6's automatic scan then proves the assignment was complete rather than trusting the arithmetic.

**Placeholder scan:** none. Every task contains the literal CSS it writes, the literal test code, and the exact command with its expected output. No step says "similar to Task N" or "add appropriate styling".

**Markup changes:** exactly one line, the stylesheet import. Every other requirement is met with CSS. The three that *looked* like they would need markup — the crushed three-child `.pf-field` in `PortForm`, the scrimless modals, and the variable-length `.pf-module-details` child list — are each solved with a scoped rule, and each one is documented in *Verified facts* with the reason the obvious approach fails.

**Not in scope, deliberately:** the "Show Output" install log (`wizard-finish.html:22-24`) — a markup addition; a light theme — `theme.css` has none and adding one is a whole-app decision; the 800×480 on-device panel; a self-hosted Barlow (audit item M3, which would also unlock pixel screenshots and is worth its own plan); and any edit to `settings.css` or `dashboard.css`.

**Could not verify:** (a) that the six step pills fit on one row **with the real Barlow metrics** — the ~795px estimate is from character counts, not a rendered measurement, which is precisely why Task 7 asserts it numerically and `flex-wrap: wrap` is present as a safety valve; (b) the appearance of the three modals in situ — unreachable from a test run for the reasons given, hence Task 8 Step 3; (c) whether `.pf-module-details:not(:has(.pf-module-image))` is ever exercised, i.e. whether any module in `wizard_manifest.json` ships without an image — the rule is defensive, and Task 8 checklist item 7 asks a human to find one.
