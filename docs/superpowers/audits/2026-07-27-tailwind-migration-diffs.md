# Tailwind v4 migration — accepted visual differences

Closes Task 15 of `plans/2026-07-26-tailwind-v4-migration.md`. That task names
this file `2026-07-26-tailwind-migration-diffs.md`; the walkthrough happened on
2026-07-27, so it carries that date instead. Nothing else moved.

The migration's requirement was visual identity except where the "before" was
clearly broken. Every difference below was accepted deliberately: the first
group by the ruling of 2026-07-26 that took screenshots over statistics, the
second by a human walkthrough on 2026-07-27.

## What the gate could not see, and what the walkthrough found

Task 15 exists because a green gate is not the deliverable. It earned its place:
the walkthrough found **four defects that every gate had passed**, because
`layoutBaseline.ts` measures named landmarks and none of these moved a landmark.

| # | Surface | Before | After | Why this is not a regression |
|---|---|---|---|---|
| 1 | Work Mode, Startup | A field carrying a `hint` spread `justify-between` across **three** items, so its control sat in the row's centre — PMode (`0–9`), and Startup's two `0 = disabled` fields | Hint takes a row of its own, starting at the control it describes | The centred control was never intended; the hint was a fourth part of a three-part row with nowhere to go |
| 2 | Work Mode, Pellets, Safety | The unit sat inside `.pf-field-control`, so the **unit's** right edge aligned to the container, not the input's. Work Mode measured control edges at `[893, 889, 895, 891]` and unit edges at `[901, 903]` | One control right edge and one unit left edge per viewport — 828/844 desktop, 278/294 phone, toggles and selects included | Ragged by accident, not by design |
| 3 | Startup (SmartStart) | `.pf-rpt-range` applied `display: flex` to a `<td>`, dropping that column out of the table's column sizing; a per-row label (`< 60°` / `60 – 79°` / `≥ 90°`) then set where each boundary input began | A real table cell again, label in a fixed column: every row's boundary input at 344, first data cell at 484 | The other columns already aligned because `.pf-rpt-cell` forces `display: table-cell` back — this column had been patched around rather than fixed |
| 4 | General | Theme listed Light/Dark and wrote `globals.page_theme`, which nothing in React reads and for which there is no light palette | Theme picks Ember/Ice/Crimson and writes `display.config.<module>.accent_theme` | An inert control is worse than no control; Qt has offered these three accents all along |

Verification for 1–3 was measurement, not inspection: every `.pf-field` on all
twelve tabs at 1280×720 and 390×844, reporting each control's right edge and
each unit's left edge. The phone viewport needed its own track sizes — the
desktop ones left labels about 70px.

## Accepted from the preflight adoption

`08d299bc` adopted Tailwind's preflight for real, after `88efe5da` had
neutralised it behind an `@layer base` shim of `revert` declarations. The shim
was rejected on the ruling that a revert protecting nothing but a computed
string is not worth having.

Five surfaces needed real fixes rather than acceptance: cook-file thumbnails,
the Platform tab's hardware list, device-table buttons, comment thumbnails, and
link/button `text-decoration`. Everything else moved 5–30px and was left.

| Surface | Difference | Why this is not a regression |
|---|---|---|
| All | Type and spacing now declared by the app's own rules rather than inherited from the UA | The app was relying on UA defaults it never chose; preflight makes the choice explicit |
| Dashboard gauge | Arc's `drop-shadow` removed (`0071f55d`) | React-only invention. `Gauge.qml` draws no shadow on the arc; its only glow is the pulsing disc `.pf-dash-gauge-glow` already implements — and the rule's own comment admitted it |

The before/after evidence for this group is the review artifact built on
2026-07-27 (shim vs no-shim, every surface, both viewports), approved with "this
looks fine": <https://claude.ai/code/artifact/78b5e640-a42a-44a2-9078-ea2dd230ce0a>

**No tracked PNGs accompany these rows.** The plan asks for before/after images
under `audits/img/`; the "before" trees no longer exist to capture from, and the
surviving screenshots live in that artifact. Re-deriving them would mean
rebuilding two abandoned trees. Recorded as a gap rather than filled with images
that would not be the real "before".

## Deviations from the plan's own rules

Both concern the baselines, and both follow from adopting preflight — a decision
taken after the plan was written, which invalidated the premise the rules rested
on.

1. **"Never recapture" is void.** Task 4 forbade `baseline:capture` in any later
   task, because the migration was to change nothing. Preflight changed the
   rendering deliberately, so the reference described a tree that no longer
   existed: 47 failed / 58 passed, every failure a comparison rather than a
   defect. **Recaptured 2026-07-27, after the sign-off** — that order is the
   point, since recapturing first would have baked in the four defects the
   walkthrough went on to find. `bun run test:e2e:fidelity` is now **105 passed,
   0 failed**.

   A recapture writes whatever it finds, so a selector that had stopped matching
   would shrink a baseline rather than fail one. Verified instead of assumed:
   all 47 files carry the same landmark set as before with none lost, no
   landmark became zero-sized, insertions equal deletions exactly (6809/6809, so
   every change is a value rather than a line), `pellets-fingerprint.json` is
   unchanged so the pellets gate ran rather than skipped, and `reflow`/`panel`
   are outside the capture set yet still pass — the green is not
   self-confirming.
2. **Baselines were not updated in the commit that moved them.** Task 15 Step 4
   requires the baseline entry to land with the change that causes it, so a
   reviewed reason cannot be separated from its result. `08d299bc` (preflight)
   and `bf2800d7` (field alignment) both moved geometry without touching a
   baseline. Unfixable now without rewriting history; recorded instead.

Task 15 Step 2 does pass on its own terms: against the pre-Tailwind reference
`43f4f74a`, the nine changed files are **all additions** — `chrome-*`,
`pellets-*`, `cookfile-*`, captured later for surfaces that had no gate at all.
**Not one pre-existing baseline was modified during the migration**, which was
the audit's real question.

## Still open

- ~~`/settings/probes` has **no baseline**~~ — CLOSED 2026-07-27. Twelve
  settings tabs now have twelve settings baselines. The plan's Step 3 says "all
  11 tabs" and undercounted because ProbesTab shipped after the reference was
  captured. The new spec is written out rather than added to `SETTINGS_TABS`
  because it needs two things the generated specs cannot express: its own
  fixture (its route loader is the only settings child with one, and `stubApi`
  does not cover `/api/probe_modules`), and the probes vocabulary —
  `probes.css` scopes `.pf-probes-card` and `.pf-btn` under
  `.pf-probes-surface`, and while the wizard baselines cover the unscoped forms,
  nothing had ever measured the scoped ones. Captured with `-g "settings-probes"`
  so the other 47 files could not move, and they did not: the diff is two new
  files, pure additions.
- `history-*.json` and `cookfile-*.json` measure the developer's real cook files
  through the demo server's `/api` proxy — the baselines that would not
  reproduce on another machine.
- The 390×844 General tab's 52px hint column forces a 187px row against 36–38px
  neighbours. Pre-existing, deliberately not fixed during a conversion.
