# React Settings Guards Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the client-side guards that the schema-driven React settings port dropped —
Flask's Jinja `{% if %}` show/hide conditionals and its `min=`/`max=`/`step=` input bounds — so
React stops offering controls that do not apply to the user's hardware and stops accepting values
the backend will refuse.

**Architecture:** Bounds enforcement lands **once on the shared `NumberField` primitive**, not on N
call sites; hardware gating lands as one pure predicate read from `settings.platform.dc_fan`; the
per-tab work is then attribute changes plus three real conditional structures (Startup, PWM
cross-field, table monotonicity).

**Tech Stack:** React 19 + react-router, TS7, rsbuild, Biome, @rstest/core, Playwright, bun.

## Why this exists

This is audit Slice 4 (`docs/superpowers/audits/2026-07-25-audit-triage.md:158-178`), covering
findings I5, I6, I15, I18, M16 and M15 of
`docs/superpowers/audits/2026-07-25-react-vs-flask-ui-divergences.md`. The audit's own pattern
note 4 (`:642-646`) names the mechanism: *"A conditional in Jinja is invisible to a schema-driven
form generator."* A port that renders every field in the schema drops the UI logic that decides
**whether** to render a field and **what range** it may hold.

All six findings were re-derived against live code for this plan. **Five hold. One holds only in
half, and the other half is a recorded design decision — dropped, with evidence (see I18 below).**
The sweep also turned up bounds the audit missed and two orphaned findings the triage assigned to
no slice at all.

---

## Global Constraints

- Test runner is **@rstest/core** (`rs.fn`, `rs.mock`) — **`vi` does NOT exist.**
  `.test.tsx` → jsdom, `.test.ts` → node. Follow `PwmTab.test.tsx:1-21` verbatim for the mock shape.
- **bun**, never npm.
- **No suppressions**: no `biome-ignore`, no `@ts-expect-error`, no `eslint-disable`.
- **No `setState` in `useEffect` for derived state** (React Compiler). Render-phase adjustment only
  — see `settings/tabs/SafetyTab.tsx:36-41`, `dashboard/SetpointEntry.tsx`. Every tab in this plan
  already uses that `prev`-compare idiom; preserve it byte-for-byte.
- `react-refresh/only-export-components`: non-components go in their own module. The pure bounds
  helper in Task 1 therefore lives in `helpers/settings/`, not next to a component.
- Gate: `bun run typecheck && bun run lint && bun run test && bun run build && bun run gen:types:check`.
- Reuse the existing `pf-*` class vocabulary (`components/settings/settings.css`,
  `components/dashboard/dashboard.css` — both are globally imported at `src/main.tsx:5-6`).
  Do not introduce a second visual language.
- **Do not touch `src/components/App.tsx`.** The app-shell plan restructures it
  (`plans/2026-07-24-react-app-shell.md:97`). Every gating decision here is made *inside* a
  component, never by adding or removing a route.

## HARD PRECONDITION — the save-failure plan lands first, in full

`docs/superpowers/plans/2026-07-25-react-save-failure-surfacing.md` Task 3 edits all nine saving
tabs: it deletes each tab's local `const [saved, setSaved] = useState(false)`, rewrites
`setSaved(await save(...))` to a bare `await save(...)`, and replaces the six-line
`pf-settings-actions` block with `<SaveBar onSave={onSave} saving={saving} status={status} />`.
That plan says so itself at `:378-380`, naming *this* slice's I5 and I15 as the work it must not
run beside.

Consequences the implementer must internalise:

1. **Start from a tree where every tab already renders `<SaveBar>`.** Do not re-add `saved`, do not
   re-introduce the inline actions block. Every code snippet below assumes the migrated shape.
2. **Do not build a second error channel.** Task 3's `min >= max` guard surfaces through the tab,
   not through a new banner. Where this plan needs to *block* a save, it does so before calling
   `save()` and reports through the same affordance.
3. If the save-failure plan has not landed, **stop and say so** rather than working around it.
   Fixing these guards while rejections are still invisible means the next rejection is invisible
   too — which is the whole reason the triage sequenced them this way
   (`audit-triage.md:166-168`).

---

## Verified facts (checked against live code — do not re-derive, do not guess)

### THE STRUCTURAL FACT — React's `min`/`max` are advisory; Flask's are enforced

**Flask.** Every settings pane is a real form with a real submit button, e.g.
`blueprints/settings/templates/settings/index.html:584` (`<form name="pwm" action="/settings/pwm"
method="POST">`) and `:762` (`<button type="submit" … onclick="return validateDutyCycle()">`). The
browser runs constraint validation on submit, so a `min="5" max="240"` input **blocks the POST**.

**React.** There is no `<form>` anywhere in the settings tree. `SettingsShell.tsx:39-41` renders
`<Outlet>` inside `<main className="pf-settings-content">`; `fields/Section.tsx` renders a
`<section>`; every tab's Save is a plain `<button>` with an `onClick`. And
`fields/NumberField.tsx:29` is:

```tsx
onChange={(e) => onChange(Number(e.target.value))}
```

— unconditional. So `min`/`max` on `NumberField` today affect only the spinner arrows and
`:invalid` styling. **Nothing stops a user typing 500 into a field marked `max={9}`.**

**Therefore a literal attribute port of Flask's bounds produces a *weaker* guard than Flask has.**
This is why Task 1 exists and why it comes first: the bounds must be enforced on the primitive
before any of the tabs are worth annotating.

### Bounds authority — Flask vs. `common/settings_schema.py`

`write_settings()` is hard-strict, so the schema is the authority on what will be *rejected*. Flask's
attributes are the authority on what is *sensible*. They are different jobs, and most of Flask's
bounds have no schema counterpart at all. Full comparison for every field in this slice:

| Field | Flask | Schema | React today | Verdict |
|---|---|---|---|---|
| `cycle_data.PMode` | `index.html:343` min 0 max 9 | `settings_schema.py:87` — **no bound** | `WorkModeTab.tsx:127-132` min 0, no max | Flask right. UI-only bound; cannot cause a rejection. Note `SmartStartProfile.p_mode` **is** schema-bound `ge=0, le=9` (`:324`), so 0-9 is the house rule |
| `cycle_data.LidOpenThreshold` | `:502` min 1 max 80 step 1 | `:91` — none | `:150-155` min 0, no max | Flask right |
| `cycle_data.LidOpenPauseTime` | `:511` min 10 max 1000 step 1 | `:92` — none | `:156-162` min 0, no max | Flask right. **Audit missed this one** |
| `smoke_plus.duty_cycle` | `:421` min 20 max 100 | `:78` — none | `WorkModeTab.tsx:204-211` min 20 max 100 | **Already correct.** No change |
| `smoke_plus.on_time`/`off_time`/`min_temp`/`max_temp` | `:430,438,446,454` min 1 | none | min 0 | Cosmetic (min 0 vs 1) |
| `keep_warm.temp` | `:559` min 1 | `:67` — none | `WorkModeTab.tsx:220-226` min 0 | Cosmetic |
| `pelletlevel.warning_time` | `:1325` min 5 max 240 | `:60` — none | `PelletsTab.tsx:78-84` min 0, no max | Flask right |
| `pelletlevel.warning_level` | `:1333` min 0 max 100 | none | min 0 max 100 | **Already correct** |
| `pelletlevel.full` | `:1354` min 0 max 100 | none | `PelletsTab.tsx:95-101` min 0, no max | Flask right. **Audit missed** |
| `pelletlevel.empty` | `:1362` min 1 max 100 | none | `PelletsTab.tsx:88-94` min 0, no max | Flask right. **Audit missed** |
| `safety.minstartuptemp` / `maxstartuptemp` / `maxtemp` | `:1262,1269,1276` min 1 | `:47-49` — none | `SafetyTab.tsx:52-69` **no min at all** | Flask right; React currently accepts negative grill temperatures. **Audit missed** |
| `safety.reigniteretries` | `:1286` min 0 max 10 | `:50` — none | `SafetyTab.tsx:70-75` min 0, no max | Flask right. **Audit missed** |
| `startup.prime_on_startup` | `:854` min 0 max 200 | `:361` **`ge=0, le=200`** | `StartupTab.tsx:161-166` min 0, no max — **but clamped to 0 out-of-range at `:91-92`**, matching Flask `routes.py:522-526` | Both agree. Only the visible `max` is missing |
| `startup.pwm_duty_cycle` | `:866` min 0 max 100 | cross-section, `SettingsSchema._check_startup_pwm_duty_cycle` `:591-599` | `StartupTab.tsx:167-174` min 0 max 100, **clamped into `[pwm.min,pwm.max]` at `:94-100`** | **Already correct.** This is the pattern PwmTab must mirror in the other direction |
| `startup.start_to_mode.primary_setpoint` | `:819` min `safety.maxstartuptemp` max `safety.maxtemp` | `:351` — none | `StartupTab.tsx:209-215` min 0, no max | Flask right; UI-only, dynamic |
| `history_page.minutes` | `:1109` min 1 | `:551` — none | `HistoryTab.tsx:127-132` min 0 | Flask right — **but HistoryTab is owned by Slice 5. Hand it over, do not edit** |
| `history_page.datapoints` | `:1116` min 10 | `:553` — none | `HistoryTab.tsx:133-138` min 0 | Same — hand to Slice 5 |
| `notify_services.wled.notify_duration` | `:2003` min 0 max 3600 | `:498` `ge=0`, **no upper** | `NotificationsTab.tsx:314-319` min 0, no max | Flask's 3600 is UI-only; safe to add |
| **`smartstart.profiles[*].augerontime`** | `:944` / `:990` max **1000**, plus JS validation 1-1000 at `settings.js:227-231, 355-359` | `:323` **`ge=1, le=60`** | `StartupTab.tsx:14` max **60** | **DISAGREEMENT — see the decision below. Do not change either side in this plan.** |
| `smartstart.profiles[*].startuptime` | `:938` 30-1200 | `:322` `ge=30, le=1200` | `StartupTab.tsx:13` 30-1200 | All three agree |
| `smartstart.temp_range_list[*]` | `:932`/`:978` min 0 max 200 | none | `RangeProfileTable.tsx:92-98` **no bounds at all** | Flask right |
| `pwm.min_duty_cycle` / `max_duty_cycle` | `:735,743` min 1 max 100 | `:292-293` — none | `PwmTab.tsx:101-116` min 0 max 100 | Cosmetic (min 0 vs 1) |
| controller `option_step` | `_macro_settings.html:51` passes `option['option_step']` | n/a (controller metadata) | `ControllerTab.tsx:138-148` passes `option_min`/`option_max` but **not** `option_step` | Flask right; `controller/controllers.json` really does declare steps down to `1e-10` |

### THE ONE DISAGREEMENT — `augerontime`, and why it is circular

- Flask HTML: `index.html:944` and `:990` → `min="1" max="1000"`.
- Flask JS: `settings.js:227-231` (edit) and `:355-359` (add) → reject outside `[1, 1000]`.
- React: `StartupTab.tsx:14` → `{ key: "augerontime", …, min: 1, max: 60 }`.
- Schema: `settings_schema.py:322-324`, immediately under the comment
  *"Clamp source: web-react/src/components/settings/tabs/StartupTab.tsx:13-15"* →
  `augerontime: int = Field(ge=1, le=60)`.
- The pinning test repeats the provenance: `tests/unit/common/test_settings_schema.py:544-546`,
  *"Clamp source: web-react StartupTab.tsx RangeProfileTable columns (startuptime 30-1200 /
  augerontime 1-60 / p_mode 0-9)."*

So the schema's `le=60` was transcribed **from the React tab**, and the React tab's 60 has no Flask
provenance — `startuptime` and `p_mode` in the same triple match Flask exactly, and only
`augerontime` does not. The React value looks like an unsourced invention that the schema then
ratified, which is precisely the circular-authority case the brief asked to be surfaced.

**Consumption, for whoever decides:** `controller/runtime/logic/smartstart.py:16-20` uses
`augerontime` as the auger ON seconds of the smoke cycle, with
`off_time = SmokeOffCycleTime + p_mode*10` and `cycle_ratio = on/(on+off)`. At the default
`SmokeOffCycleTime = 45` (`settings_schema.py:86`), an `augerontime` of 1000 s means a ~95 % auger
duty cycle for the whole startup — physically a hopper dump. 60 s is the defensible number;
1000 s is what Flask has shipped.

**This is a decision, not an implementation detail. Two options, both real:**

- **(A) Keep 60.** Ratify the current schema, and *correct Flask* (`index.html:944, 990`,
  `settings.js:227-231, 355-359`) down to 60 so the two front-ends agree. Cost: one Python-side
  comment fix to remove the circular citation, four Flask edits, and an admission that Flask's
  shipped bound is being narrowed — an existing user with `augerontime > 60` **already cannot save
  any settings at all**, since `validate_settings_tree` walks the whole merged tree.
- **(B) Restore 1000.** Change `settings_schema.py:323` to `le=1000`, update
  `tests/unit/common/test_settings_schema.py:544-552`, and raise `StartupTab.tsx:14` to
  `max: 1000`. Cost: re-opens a foot-gun Flask never guarded server-side.

Option (A) is the recommendation — but **whoever runs this plan must get a human answer first and
record it**, because either direction touches Python, and this plan is otherwise pure frontend.
Until then, `StartupTab.tsx:14` stays at 60: it matches the schema, so it is the only value that
cannot produce a rejection.

### `platform.dc_fan` — the gating source is already in scope (I5)

Flask gates four places on `settings['platform']['dc_fan']`:

| What | Where |
|---|---|
| PWM Settings nav pill | `index.html:63-65` |
| Smoke-Plus fan-ramp NOTE + `sp_fan_ramp` switch + `sp_duty_cycle` input | `:405-423` |
| The entire PWM tab pane | `:581` … `:768` |
| Startup DC-fan duty-cycle NOTE + `pwm_duty_cycle` input | `:857-868` |

React shows all four unconditionally: `SettingsShell.tsx:8` (`{ path: "pwm", label: "PWM Fan" }`),
`WorkModeTab.tsx:204-216`, `StartupTab.tsx:167-174`.

**The value is already available at every site — this is an attribute-level change, not plumbing.**
`SettingsShell.tsx:19-23` destructures `settings` from `useLoaderData()`; every tab reads
`settings` from `useOutletContext()`; and `PlatformTab.tsx:20,27` already reads
`settings.platform.dc_fan` today, which is the audit's evidence that the field survives the loader
intact.

### PWM cross-field validation (I6) — and it is bigger than the audit says

Flask does **three** things React does none of:

1. Blocks submit when `min >= max` — `validateDutyCycle()` at `index.html:747-758`, wired as
   `onclick="return validateDutyCycle()"` on the submit button at `:762`.
2. Re-clamps every `pwm.profiles[*].duty_cycle` into the new range —
   `blueprints/settings/routes.py:489-490`.
3. Re-clamps **`startup.pwm_duty_cycle`**, which lives on a *different tab*, into the new range —
   `routes.py:495`.

The source comment at `routes.py:478-484` states why in terms of this exact codebase: *"Without
this, narrowing min/max alone can leave either of these outside the new bounds, which the schema
now rejects at write_settings()."*

Both are schema-enforced: `PwmSettings._check_profiles` (`settings_schema.py:303-316`) and
`SettingsSchema._check_startup_pwm_duty_cycle` (`:591-599`).

`PwmTab.tsx:78-85` does none of it — its `onSave` is a bare
`for (const [k,v] of Object.entries(pwm)) d = setPath(d, \`pwm.${k}\`, v)` and it never puts
`startup.pwm_duty_cycle` in the delta. `DUTY_COLUMNS` at `:67-75` does pass the live min/max into
the table, but `RangeProfileTable.handleCellChange` (`:47-51`) only clamps **when a cell is
edited** — changing `min_duty_cycle` never re-clamps the rows already on screen.

Note also that a `min >= max` save is *always* a rejection in practice: `_check_profiles` requires
every profile to satisfy `min <= duty <= max`, and `profiles` is never empty (it must be
`len(temp_range_list) + 1`).

**The reverse direction already exists and is the pattern to copy:** `StartupTab.tsx:94-100` clamps
`pwm_duty_cycle` into `[settings.pwm.min_duty_cycle, settings.pwm.max_duty_cycle]` before saving.
Mirror it.

### Startup tab conditional structure (I15)

| Flask | Behaviour |
|---|---|
| `index.html:812-826` | The Hold block — setpoint **and** the "Always ask for hold temperature" switch — is `display:none` unless `after_startup_mode == 'Hold'`; `settings.js:943-950` slides it in/out on change |
| `:828-841` | An **"Exit Startup @ Temperature"** switch, checked iff `startup_exit_temp > 0`; `settings.js:952-965` remembers the previous value, **restores 140 when re-enabled if it was 0**, and writes 0 when disabled |
| `:843-856` | An **"Always Prime on Startup"** switch, checked iff `prime_on_startup > 0`; `settings.js:967-980`, same shape, **default 10 g** |
| `:819` | Setpoint bounded `min="{{ safety.maxstartuptemp }}" max="{{ safety.maxtemp }}"` |

`StartupTab.tsx:146-227` renders every one of these as an always-visible field. Because 0 is a
legal-looking number, **"0 = disabled" is undiscoverable** — the audit's framing is right.

**One correction to the audit.** It calls the React fields "always-visible, unbounded numbers".
They are not entirely unbounded: `StartupTab.tsx:90-92` already clamps `prime_on_startup` out-of-
range to 0 (matching Flask `routes.py:522-526`) and `:94-100` already clamps `pwm_duty_cycle`. The
gap is **discoverability and the setpoint bounds**, not the prime clamp.

### Table monotonicity (I18) — holds, and is worse than graded

`RangeProfileTable.tsx`:
- `:39` `const canRemove = profiles.length > 2;` — the ≥2-row rule, the only rule present.
- `:41-45` `handleBoundaryChange` writes `Number(raw)` straight through: **no clamp, no ordering
  check**.
- `:47-51` `handleCellChange` clamps to the column's own `min`/`max` only.
- `:92-98` the boundary `<input type="number">` carries **no `min`/`max` attribute at all**.

Flask enforces strict ordering in both paths: `settings.js:195-244` (`saveChanges` — a middle row's
new temp must be `> temps_list[i-1]` **and** `< temps_list[i+1]`; the last row's boundary is locked)
and `settings.js:341-380` (`onAdd` — `minTemp` must exceed the previous boundary).

**The schema does NOT catch this.** `SmartStart._check_profile_count` (`settings_schema.py:339-345`)
and `PwmSettings._check_profiles` (`:303-316`) check counts and per-row bounds; **neither checks that
`temp_range_list` is sorted.** So an out-of-order list *saves successfully*, and then
`controller/runtime/logic/smartstart.py:8-12` — `for i in range(len(temp_range_list)): if
startup_temp < temp_range_list[i]: return i` — silently makes every profile after the inversion
unreachable.

**That makes I18 the only finding in this slice that is not an invisible rejection but a silent
wrong configuration**, which is strictly worse. Treat it accordingly.

**The "edits lost on tab switch" half does NOT hold as a table finding — dropped.** Every settings
tab seeds `useState` from loader data and holds edits locally until Save (`PwmTab.tsx:49`,
`StartupTab.tsx:72`, `WorkModeTab.tsx:74`, `SafetyTab.tsx:35`, …), so *all* unsaved edits on *any*
tab are lost on navigation — this is not specific to tables. And it is a **recorded decision**, cited
in the source at `PwmTab.tsx:80-82` and `StartupTab.tsx:111-112`: *"plan ruling: single Save per
tab, existing ["settings_update"] flag kept"*. Flask's immediate-persist model for these two tables
(`settings.js:76-92` → `POST /settings/smartstart`, `:416-432` → `POST /settings/pwm_duty_cycle`) is
a genuine divergence, but it is a *divergence from a decision that was made*, not a dropped guard,
and reversing it would re-open the per-tab save architecture. Out of scope. If someone wants it
revisited, that is a backlog item, not a task here.

### Delete confirmations (M15) — holds, with the destructiveness re-graded down

- **OneSignal device.** Flask: a Bootstrap modal, *"Are you sure you want to delete the {name}
  device?"*, whose Delete button is a form submit (`index.html:1651-1678`). React:
  `NotificationsTab.tsx:216-223` → `onClick={() => deleteDevice(deviceId)}`, and `deleteDevice`
  (`:90-97`) **only mutates local state** — the row is gone from the table but nothing is persisted
  until Save. So the audit's "immediate delete" is imprecise: React's is recoverable by navigating
  away. A confirm is still correct (the row cannot be re-created from the UI — devices self-register
  from the Android app), but grade it as parity, not data loss.
- **Apprise row.** Flask confirms **only when the row is non-empty** and refuses to remove the last
  row, clearing it instead (`settings.js:925-941`). React `StringListField.tsx:21-27` removes any
  row instantly, and will happily go to zero rows. The interesting half is the *conditional*
  confirm, which is the same `{% if %}`-shaped logic as everything else in this slice.

`components/dashboard/ConfirmAction.tsx` is the house affordance and is already reused from a
settings tab (`UnitsTab.tsx:5, 62-67`). Its CSS (`pf-modal-scrim` / `pf-modal-title` /
`pf-modal-actions`, `dashboard.css:165, 183, 218`) is globally imported at `main.tsx:5`, so it works
from `components/settings/` with no CSS work.

**It now also takes an optional `message` prop** (`ConfirmAction.tsx:4-7, 18`), added by the wizard
slice for cascade copy, with the source note *"`.pf-modal-title` is a bold, centred 20px headline,
so a second sentence does not belong up there."* Use `title` for the question and `message` for the
consequence; do not stuff both into `title`.

### Two findings that do NOT hold — dropped with evidence

1. **Controller `option['hidden']` rows.** `_macro_settings.html:47` renders
   `<tr {% if option['hidden'] %} hidden {% endif %}>` and `ControllerTab.tsx:127-177` has no
   equivalent — so on paper this is another dropped `{% if %}`. But **zero options in
   `controller/controllers.json` declare `hidden: true`** (checked across all 9 controllers: `pid`,
   `pid_clamping`, `pid_clamping_percent_pb`, `pid_ac`, `pid_sp`, `pid_parallel`, `fuzzy`, `ml`,
   `mpc`). Latent only — same disposition as the `numlist` option type, already accepted as a
   non-issue at `specs/2026-07-22-settings-2b2-widgets-design.md:37`. **Do not implement.**
2. **Controller `option_min`/`option_max`.** The audit's M16 implies bounds were dropped broadly;
   `ControllerTab.tsx:145-146` already passes both. **Already correct — no work.** Only
   `option_step` is genuinely missing.

### Orphans — flagged, not silently adopted

Two audit findings are assigned to **no slice at all** in the triage:

- **I16 — the prime-ignition DANGER copy** (`index.html:1403-1412`: *"Enabling the igniter will
  ignite pellets and start the firepot, even without the fan enabled"*), dropped from
  `PelletsTab.tsx:114-118`, which is a bare toggle labelled "Prime Ignition". This is
  safety-relevant text on a control that lights a fire, and it sits in a file this plan already
  opens for the pellet-level bounds. **Task 6 adopts it** — one paragraph, zero risk. Recorded here
  so it is not mistaken for scope creep.
- **M3 — the external Google Fonts dependency** (`web-react/index.html:7-11`). Nothing to do with
  guards; belongs with M1 in the chrome slice. **Not adopted. Flag it to whoever owns Slice 7.**

### Missing CSS classes (pre-existing; matters only if hints are added)

`pf-field-hint` (used at `ControllerTab.tsx:123, 125`), `pf-section-note` and `pf-kv`
(`PlatformTab.tsx`) have **no rule in any of the four stylesheets** (`theme.css`, `dashboard.css`,
`settings.css`, `historyChart.css`). They render unstyled today. Since several tasks below add hint
text, Task 1 adds a single `.pf-field-hint` rule to `settings.css` — the smallest fix that makes the
hints this plan introduces legible. Do not restyle the others; that is not this slice.

---

## File Structure

**Create**
- `web-react/src/helpers/settings/bounds.ts` + `bounds.test.ts` — pure `clampToBounds`.
- `web-react/src/helpers/settings/platform.ts` + `platform.test.ts` — pure `hasDcFan(settings)`.

**Modify**
- `web-react/src/components/settings/fields/NumberField.tsx` (+ `.test.tsx`) — enforce bounds, hint.
- `web-react/src/components/settings/settings.css` — one `.pf-field-hint` rule.
- `web-react/src/components/settings/SettingsShell.tsx` (+ `.test.tsx`) — conditional PWM nav item.
- `web-react/src/components/settings/RangeProfileTable.tsx` (+ new `.test.tsx`) — monotonic boundaries.
- `web-react/src/components/settings/fields/StringListField.tsx` (+ `.test.tsx`) — conditional confirm.
- `web-react/src/components/settings/tabs/PwmTab.tsx` (+ `.test.tsx`) — dc_fan notice, min<max guard, clamps.
- `web-react/src/components/settings/tabs/StartupTab.tsx` (+ `.test.tsx`) — conditionals, bounds, dc_fan.
- `web-react/src/components/settings/tabs/WorkModeTab.tsx` (+ `.test.tsx`) — dc_fan block, bounds.
- `web-react/src/components/settings/tabs/PelletsTab.tsx` (+ `.test.tsx`) — bounds, I16 copy.
- `web-react/src/components/settings/tabs/SafetyTab.tsx` (+ `.test.tsx`) — bounds.
- `web-react/src/components/settings/tabs/NotificationsTab.tsx` (+ `.test.tsx`) — delete confirm, WLED max.
- `web-react/src/components/settings/tabs/ControllerTab.tsx` (+ `.test.tsx`), `helpers/settings/settingsApi.ts` — `option_step`.
- `web-react/tests/e2e/settings.spec.ts` — two guard specs.

**Explicitly NOT touched**
- `src/components/App.tsx` (owned by the app-shell plan).
- `src/components/settings/tabs/HistoryTab.tsx` (owned by Slice 5 — hand over the two bounds).
- `src/helpers/settings/useSaveSettings.ts`, `src/components/settings/SaveBar.tsx` (owned by the
  save-failure plan).
- `common/settings_schema.py` and its tests (blocked on the `augerontime` decision).

---

## Coordination

| Plan | Files it owns | Overlap with this plan | Resolution |
|---|---|---|---|
| `2026-07-25-react-save-failure-surfacing.md` | `helpers/settings/useSaveSettings.ts`, new `settings/SaveBar.tsx`, **and the actions block inside all 9 tabs** | **Heavy** — 7 of the 9 tabs are edited by both | **Strictly sequential: that plan lands first, in full.** See the Hard Precondition. This plan never edits the actions block, never re-adds `saved`, never adds a second error channel |
| `2026-07-25-react-probe-notifications.md` | `helpers/notify/*`, `components/dashboard/ProbeNotifyModal.tsx`, `ProbeCard.tsx`, `Dashboard.tsx`, `helpers/dashboard/deriveView.ts`, `dashboard.css` | **None** — dashboard tree only | Fully parallel. Both may run at once in isolated jj workspaces |
| `2026-07-24-react-app-shell.md` | `components/shell/*`, `App.tsx`, `helpers/command.ts`, `useDashData`→`useLiveState` | `App.tsx` only, and only if PWM gating were done by removing a route | **Avoided by design**: Task 2 gates the nav item and renders an in-tab notice; the `/settings/pwm` route stays registered and untouched |
| Slice 5 (history backlog) | `HistoryTab.tsx`, `components/history/*` | `history_page.minutes` min 1 and `datapoints` min 10 | **Handed over.** Recorded in the table above; do not edit `HistoryTab.tsx` here |
| Slice 6 (`I13 sleep_timeout`) | General/Platform tab | `display.sleep_timeout` bounds (`index.html:1082`) | Out of scope — the field is not rendered by React at all yet |

**Should any of this fold into another plan?** One item, no more: **I6's `min >= max` guard is the
save-failure plan's chosen e2e witness** (`react-save-failure-surfacing.md:339-340, 353-362` — it
drives a PWM rejection through the UI to prove the error surfaces). Do **not** weaken or remove that
spec. Task 3 here makes that rejection *unreachable through the normal UI path*, so Task 8's e2e
must reach the schema rejection by a route the client guard does not cover (see Task 8, Step 2). The
two specs coexist; the save-failure one keeps proving the plumbing, this one proves the guard.

---

### Task 1: Make `NumberField` actually enforce its bounds

**Files:** Create `web-react/src/helpers/settings/bounds.ts` + `bounds.test.ts`; modify
`web-react/src/components/settings/fields/NumberField.tsx` +
`web-react/src/components/settings/fields/NumberField.test.tsx`; modify
`web-react/src/components/settings/settings.css`.

**Interfaces:** Produces `clampToBounds(value: number, min?: number, max?: number): number` and an
extended `NumberField` prop set: unchanged `min`/`max`/`step`/`suffix`, plus optional
`hint?: string`.

**This task is the reason the rest of the plan is small.** Every later task is then an attribute
change on a call site instead of bespoke validation logic.

**Design decision — clamp on blur, not on change.** Clamping inside `onChange` makes a bounded field
untypeable: with `min={20}`, typing "25" clamps the intermediate "2" to 20 and the user ends up with
"205". Blur is the moment the value is finished. It also matches the two clamps already in the repo
(`RangeProfileTable.tsx:47-51` clamps on a completed cell edit; `StartupTab.tsx:90-100` clamps at
save). The alternative — mark invalid and block Save — is rejected here: it needs an error channel,
and the error channel belongs to the save-failure plan.

- [ ] **Step 1: Write failing tests for `bounds.ts`** (`.test.ts` → node): value inside range is
      returned unchanged; below `min` → `min`; above `max` → `max`; `min` undefined → no lower
      clamp; `max` undefined → no upper clamp; both undefined → identity; `NaN` → returned
      unchanged (the caller decides, the helper does not invent a number).
- [ ] **Step 2: Write failing tests for `NumberField`** (`.test.tsx` → jsdom), extending the
      existing file:
      - typing an out-of-range value fires `onChange` with the **raw** value (no mid-typing clamp);
      - blurring an out-of-range value fires `onChange` with the **clamped** value;
      - blurring an in-range value fires **no extra** `onChange`;
      - a field with no `min`/`max` never clamps on blur;
      - `min`/`max`/`step` are still forwarded as DOM attributes (assert
        `toHaveAttribute("max", "9")`) — the spinner and a11y semantics must not regress;
      - `hint` renders when provided and is absent when omitted.
- [ ] **Step 3: Run, confirm both fail** — `bun run test src/helpers/settings/bounds.test.ts
      src/components/settings/fields/NumberField.test.tsx`.
- [ ] **Step 4: Implement.**
      ```ts
      // helpers/settings/bounds.ts — pure, no React.
      export function clampToBounds(value: number, min?: number, max?: number): number {
        if (Number.isNaN(value)) return value;
        let v = value;
        if (min !== undefined && v < min) v = min;
        if (max !== undefined && v > max) v = max;
        return v;
      }
      ```
      In `NumberField.tsx`, keep `:29` as-is and add a blur handler beside it:
      ```tsx
      onBlur={(e) => {
        const clamped = clampToBounds(Number(e.target.value), min, max);
        if (clamped !== Number(e.target.value)) onChange(clamped);
      }}
      ```
      Render the hint after the control, inside the existing `<label className="pf-field">`:
      `{hint && <span className="pf-field-hint">{hint}</span>}`.
      Add one rule to `settings.css` next to `.pf-settings-hint` (`:180-184`), reusing its
      colour/size tokens — no new palette.
- [ ] **Step 5: Run the whole suite, not just these two files.** Several existing tab tests type
      into number fields; a blur-clamp can change what they observe. Any failure is either a real
      behaviour change to accept deliberately or a bug in this task — **do not weaken an assertion
      to make it pass.**
- [ ] **Step 6: Commit.** Its own commit: every later task depends on this contract.

### Task 2: Gate the DC-fan-only controls on `platform.dc_fan` (I5)

**Files:** Create `web-react/src/helpers/settings/platform.ts` + `platform.test.ts`; modify
`SettingsShell.tsx` + `.test.tsx`, `PwmTab.tsx` + `.test.tsx`, `WorkModeTab.tsx` + `.test.tsx`,
`StartupTab.tsx` + `.test.tsx`.

**Interfaces:** Produces `hasDcFan(settings: Settings): boolean`.

Four sites, one predicate. **Do not touch `App.tsx`** — the `/settings/pwm` route stays registered
so a bookmarked URL still resolves; the *tab* explains why it is inert.

- [ ] **Step 1: Write failing tests.**
      - `hasDcFan`: true for `{platform:{dc_fan:true}}`; false for `false`, for a missing `platform`,
        and for a missing `dc_fan` (the wizard *derives* `dc_fan` for `x86_numato`/`ft232h_relay`
        per `PlatformTab.tsx:14-17`, so absence must read as AC, never as "unknown → show").
      - `SettingsShell`: with `dc_fan: true` the nav contains "PWM Fan"; with `false` it does not,
        **and the other ten labels are all still present in order** — extend the existing
        `TAB_LABELS` assertion (`SettingsShell.test.tsx:32-45`) rather than replacing it, and note
        that its own comment warns the count has drifted before.
      - `PwmTab` with `dc_fan: false`: renders an explanatory notice and **no** Save button, no
        duty-cycle fields.
      - `WorkModeTab` with `dc_fan: false`: "Fan Ramp" and "Duty Cycle" are absent; every other
        Smoke Plus field (Enabled, Min/Max Temp, On/Off Time) is still present.
      - `StartupTab` with `dc_fan: false`: "PWM Duty Cycle" is absent.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.**
      ```ts
      // helpers/settings/platform.ts
      import type { Settings } from "./settingsApi";
      export function hasDcFan(settings: Settings): boolean {
        return !!settings.platform?.dc_fan;
      }
      ```
      `SettingsShell.tsx`: filter the tab list at render — `SETTINGS_TABS.filter((t) => t.path !==
      "pwm" || hasDcFan(settings))`. Keep `SETTINGS_TABS` a module constant; do not move it into the
      component.
      `PwmTab.tsx`: early-return before any state is read is **wrong** (it would break the Rules of
      Hooks against the existing `useState` block at `:47-60`). Render the notice from inside the
      normal return instead — compute `const dcFan = hasDcFan(settings);` and return the notice
      branch after the hooks.
      `WorkModeTab.tsx`: wrap the Duty Cycle (`:204-211`) and Fan Ramp (`:212-216`) elements in
      `{dcFan && (<>…</>)}`. **The save delta must not change**: `onSave` (`:98`) iterates
      `Object.entries(v.smoke_plus)`, so `duty_cycle` and `fan_ramp` keep round-tripping their
      loaded values on an AC build — which is correct, and matches Flask, whose `_settings_cycle`
      leaves untouched keys alone.
      `StartupTab.tsx`: same treatment for `:167-174`. **Keep the clamp at `:94-100` unconditional**
      — it protects a value that is still in the delta.
- [ ] **Step 4: Run, confirm pass. Commit.**

### Task 3: PWM min/max validation and the dependent-value clamps (I6)

**Files:** Modify `web-react/src/components/settings/tabs/PwmTab.tsx` + `PwmTab.test.tsx`.

**Interfaces:** Consumes `clampToBounds` (Task 1) and `hasDcFan` (Task 2).

Ports `blueprints/settings/routes.py:485-495` and `index.html:747-758`. Mirrors the direction
`StartupTab.tsx:94-100` already implements.

- [ ] **Step 1: Write failing tests.**
      - `min_duty_cycle = 90`, `max_duty_cycle = 50` → clicking Save calls `save` **zero times** and
        a message naming the constraint is rendered.
      - `min == max` is likewise refused (with equal bounds every profile must equal exactly that
        value; treat it as invalid, as Flask does — its check is `>=`, `index.html:752`).
      - Valid bounds → `save` called once.
      - Narrowing to `min=40, max=60` with profiles `[20, 35, 50, 75, 100]` → the delta's
        `pwm.profiles` is `[40, 40, 50, 60, 60]`. Assert on the **delta object passed to `save`**,
        not on the rendered table.
      - The same narrowing with a context `startup.pwm_duty_cycle` of `100` → the delta contains
        `startup.pwm_duty_cycle === 60`. **This is the assertion that catches the cross-section
        bug**; without it the save is rejected by `SettingsSchema._check_startup_pwm_duty_cycle`.
      - When `startup.pwm_duty_cycle` is already inside the new range, it is **still** written
        (writing back an unchanged value is harmless and keeps the code branch-free).
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement** in `onSave`, before the existing `setPath` loop at `:83`:
      ```tsx
      if (pwm.min_duty_cycle >= pwm.max_duty_cycle) {
        setBoundsError("Max Duty Cycle must be greater than Min Duty Cycle.");
        return;                       // do NOT call save()
      }
      setBoundsError(null);
      // Ported from blueprints/settings/routes.py:485-495 — narrowing min/max
      // alone leaves these outside the new range, which write_settings() then
      // rejects (PwmSettings._check_profiles / _check_startup_pwm_duty_cycle).
      const clamped = {
        ...pwm,
        profiles: pwm.profiles.map((p) => ({
          ...p,
          duty_cycle: clampToBounds(p.duty_cycle, pwm.min_duty_cycle, pwm.max_duty_cycle),
        })),
      };
      let d: object = {};
      for (const [k, v] of Object.entries(clamped)) d = setPath(d, `pwm.${k}`, v);
      d = setPath(
        d,
        "startup.pwm_duty_cycle",
        clampToBounds(settings.startup?.pwm_duty_cycle ?? 100, pwm.min_duty_cycle, pwm.max_duty_cycle),
      );
      await save(d, ["settings_update"]);
      ```
      Render `boundsError` with `className="pf-settings-error-text"` and `role="alert"` —
      the class already exists (`settings.css:185-188`) and is exactly what `UnitsTab.tsx:60` uses.
      **Do not route this through `SaveBar`'s `status`**: that is the server's channel, and this
      error is client-side and pre-flight.
      Also raise the two `NumberField` mins from `0` to `1` (`:101-116`) to match `index.html:735,
      743`.
- [ ] **Step 4: Verify by reading** that `PwmTab.onSave` now writes into two top-level sections and
      that `["settings_update"]` is unchanged — the flag list is a control-loop contract, not a
      style choice.
- [ ] **Step 5: Run, confirm pass. Commit.**

### Task 4: Restore the Startup tab's conditional structure (I15)

**Files:** Modify `web-react/src/components/settings/tabs/StartupTab.tsx` + `StartupTab.test.tsx`.

Ports `index.html:812-856` plus `settings.js:943-980`. Three conditionals and one dynamic bound.

- [ ] **Step 1: Write failing tests.**
      - `after_startup_mode: "Smoke"` → neither "Primary Setpoint" nor "Start to Hold Prompt" is
        rendered; switching the Select to "Hold" reveals both; switching back hides them.
      - Switching the mode **does not change** `primary_setpoint`'s value — hiding is not clearing.
      - `startup_exit_temp: 0` → an "Exit Startup @ Temperature" toggle renders **off** and the
        number field is absent. Turning it on reveals the field seeded with **140**.
      - `startup_exit_temp: 200` → the toggle renders **on**, the field shows 200. Turning it off
        hides the field; the next `save` delta carries `startup.startup_exit_temp === 0`.
      - Turning it off and back on restores **200**, not 140 — Flask's `settings.js:952-956`
        captures the loaded value first and only substitutes 140 when it was 0.
      - The same three cases for "Always Prime on Startup" with a default of **10**
        (`settings.js:967-971`).
      - With `safety: { maxstartuptemp: 100, maxtemp: 550 }`, the setpoint input carries
        `min="100"` and `max="550"`; blurring 700 clamps to 550 (this is Task 1's enforcement seen
        through a real call site).
      - A save while Hold is *not* selected still includes `startup.start_to_mode.primary_setpoint`
        in the delta at its unchanged value.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** Add to the `Startup` state type two remembered defaults, seeded in
      `readStartup` — **not** derived in an effect (React Compiler):
      ```tsx
      exit_temp_default: (st.startup_exit_temp ?? 0) > 0 ? (st.startup_exit_temp as number) : 140,
      prime_default: (st.prime_on_startup ?? 0) > 0 ? (st.prime_on_startup as number) : 10,
      ```
      The toggles are derived, never stored: `const exitTempOn = v.startup_exit_temp > 0;`. Turning
      one off writes `0`; turning it on writes the remembered default. **Do not add a separate
      boolean state** — two sources of truth for one number is how "0 = disabled" got lost in the
      first place.
      Gate the Hold block on `v.after_startup_mode === "Hold"`. Bound the setpoint with
      `min={settings.safety?.maxstartuptemp ?? 100}` / `max={settings.safety?.maxtemp ?? 550}`
      (defaults from `settings_schema.py:48-49`).
      Add `max={200}` to "Prime on Startup" (`:161-166`) — schema-backed at
      `settings_schema.py:361`. Keep the existing out-of-range→0 coercion at `:90-92`; with the
      max attribute plus Task 1's blur clamp it should now be unreachable, which is the point.
      Add hints via Task 1's `hint` prop: `"0 = disabled"` on both gated numbers.
- [ ] **Step 4: Confirm the delta is unchanged in shape.** `onSave` (`:83-122`) must still write
      all thirteen paths it writes today — hiding a control must never drop its key, or the next
      save silently reverts a hidden field.
- [ ] **Step 5: Run, confirm pass. Commit.**

### Task 5: Monotonic range boundaries in `RangeProfileTable` (I18)

**Files:** Modify `web-react/src/components/settings/RangeProfileTable.tsx`; create
`web-react/src/components/settings/RangeProfileTable.test.tsx` (there is none today — the widget is
covered only indirectly through `PwmTab.test.tsx` / `StartupTab.test.tsx`).

**Interfaces:** `RangeProfileColumn` gains nothing; the component gains optional
`boundaryMin?: number` / `boundaryMax?: number` (Flask uses 0-200, `index.html:932, 978`).

Remember from Verified facts: **the schema does not catch an unsorted `temp_range_list`**, and
`smartstart.py:8-12` then silently strands the profiles after the inversion. This is the one
finding here that is not a visible-rejection problem.

- [ ] **Step 1: Write failing tests.**
      - Boundaries `[60, 80, 90]`: editing index 1 to `95` (past its successor) is **rejected** —
        `onChange` is not called with an unsorted array, and a message naming the valid range is
        shown.
      - Editing index 1 to `70` (strictly between 60 and 80) is accepted.
      - Editing index 0 to `-5` clamps to `boundaryMin`.
      - Editing the **last** boundary is bounded below by its predecessor and by `boundaryMax`
        above (Flask locks the final row's *label*, `settings.js:184-186`, but the last entry of
        `temp_range_list` is still an editable boundary in React's layout — keep it editable, just
        ordered).
      - `handleAdd` still appends `last + 10`, preserving order (regression guard on `:53-59`).
      - `handleRemove` still refuses below 2 profiles (`canRemove`, `:39-40`) — unchanged.
      - The boundary inputs carry `min`/`max` DOM attributes when the props are supplied.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** Extract a pure helper in the same file (it has no other consumer):
      ```tsx
      function boundaryLimits(i: number, boundaries: number[], min?: number, max?: number) {
        const lower = i > 0 ? boundaries[i - 1] + 1 : (min ?? Number.NEGATIVE_INFINITY);
        const upper = i < boundaries.length - 1 ? boundaries[i + 1] - 1 : (max ?? Number.POSITIVE_INFINITY);
        return { lower, upper };
      }
      ```
      `handleBoundaryChange` clamps into `[lower, upper]` on **blur** (same rationale as Task 1 —
      an on-change clamp makes multi-digit entry impossible) and shows the permitted range when the
      typed value was out of it. Wire `boundaryMin={0}` / `boundaryMax={200}` from both call sites
      (`PwmTab.tsx:124-133`, `StartupTab.tsx:190-199`) to match `index.html:932, 978`.
      **Do not change the removal rule to Flask's "last row only".** Flask's `onDelete`
      (`settings.js:93-96`) permits deleting only the final row; React allows any row and
      re-derives the boundary list. That is a deliberate simplification of an equivalent-outcome
      operation, not a dropped guard — note it and move on.
- [ ] **Step 4: Run, confirm pass — including `PwmTab.test.tsx` and `StartupTab.test.tsx`, which
      drive this widget. Commit.**

### Task 6: The bounds sweep (M16 + the four the audit missed + I16 copy)

**Files:** Modify `WorkModeTab.tsx`, `PelletsTab.tsx`, `SafetyTab.tsx`, `NotificationsTab.tsx`,
`ControllerTab.tsx`, `helpers/settings/settingsApi.ts`, and each tab's `.test.tsx`.

Every value below comes from the Verified-facts table — **do not re-derive it from the audit's prose
and do not invent a bound that appears in neither Flask nor the schema.**

- [ ] **Step 1: Write failing tests** — one per tab, asserting the DOM attributes and one blur-clamp
      per tab as a smoke test that Task 1's enforcement reaches the call site.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Apply.**
      `WorkModeTab.tsx`: PMode (`:127-132`) `max={9}` + `hint="0–9"`; LidOpenThreshold
      (`:150-155`) `min={1} max={80} step={1}`; LidOpenPauseTime (`:156-162`)
      `min={10} max={1000} step={1}`; SmokeOn/SmokeOff/keep-warm temp/sp min & max temp/sp off time
      → `min={1}`. Leave Smoke-Plus Duty Cycle alone — it is already `min={20} max={100}`.
      `PelletsTab.tsx`: warning_time (`:78-84`) `min={5} max={240}`; empty (`:88-94`)
      `min={1} max={100}`; full (`:95-101`) `min={0} max={100}`. **Plus I16**: add the DANGER
      paragraph under the "Prime Ignition" toggle (`:114-118`), text taken verbatim from
      `index.html:1403-1412`, rendered with `pf-settings-error-text` (the only red token in
      `settings.css`) — this is safety copy on a control that ignites fuel.
      `SafetyTab.tsx`: minstartuptemp / maxstartuptemp / maxtemp (`:52-69`) `min={1}`;
      reigniteretries (`:70-75`) `max={10}`.
      `NotificationsTab.tsx`: WLED notify_duration (`:314-319`) `max={3600}`.
      `ControllerTab.tsx` + `settingsApi.ts`: add `option_step: number | null` to the
      `ControllerOption` interface (beside `option_min`/`option_max` at `settingsApi.ts:27-28`) and
      pass `step={opt.option_step ?? undefined}` at `ControllerTab.tsx:140-148`. Verified needed:
      `controller/controllers.json` declares steps of `0.1`, `0.01`, `0.001`, `0.0001`, `1e-06` and
      `1e-10` across the nine controllers, and `_macro_settings.html:51` forwards it.
- [ ] **Step 4: Run `bun run gen:types:check`** — `settingsApi.ts` is hand-written but sits beside
      the generated `settingsTypes.gen.ts`; confirm the generator is unaffected.
- [ ] **Step 5: Run the full suite, confirm pass. Commit.**

**Do NOT touch in this task:** `StartupTab.tsx:14` `augerontime` (blocked on the decision above);
`HistoryTab.tsx` (Slice 5).

### Task 7: Delete confirmations (M15)

**Files:** Modify `NotificationsTab.tsx` + `.test.tsx`, `fields/StringListField.tsx` + `.test.tsx`.

Reuse `components/dashboard/ConfirmAction.tsx` exactly as `UnitsTab.tsx:5, 62-67` does, using its
`message` prop for the consequence sentence. No new CSS — its classes live in the globally-imported
`dashboard.css`. **Check `ConfirmAction.tsx` for a `.pf-modal-message` rule before assuming it is
styled**; the prop was added by the wizard slice and the class may not have a rule yet.

- [ ] **Step 1: Write failing tests.**
      - OneSignal: clicking Delete shows a dialog naming the device (`friendly_name` when set,
        `device_name` otherwise — Flask branches on exactly this, `index.html:1659-1670`); Cancel
        leaves the row; Confirm removes it; and a subsequent `save` delta no longer contains that
        device id.
      - `StringListField`: removing a row whose value is `""` removes it **without** a dialog;
        removing a non-empty row shows one; Cancel keeps it, Confirm removes it.
      - Removing the **only** row clears it to `""` instead of dropping the list to length 0
        (`settings.js:934-940` — the "keeps interface readier for use" branch).
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** In each component hold a `pending` id/index in state (`null` when
      closed) and render one `<ConfirmAction>` per component, not per row. `StringListField` is a
      shared primitive with one consumer today (`NotificationsTab.tsx:118-122`); keep the confirm
      **inside** it so the rule travels with the widget.
- [ ] **Step 4: Run, confirm pass. Commit.**

### Task 8: e2e + full gate

**Files:** Modify `web-react/tests/e2e/settings.spec.ts`.

- [ ] **Step 1: A DC-fan gating spec.** Read `platform.dc_fan` from `GET /api/settings` first —
      **do not assume**; the e2e suite shares one live store, which is why it runs `workers: 1`.
      Assert that the "PWM Fan" nav item's presence matches the flag, and that `/settings/pwm`
      still resolves either way (it is the app-shell plan's route, untouched).
- [ ] **Step 2: A PWM guard spec that does NOT collide with the save-failure spec.** That spec
      (`settings.spec.ts:110-131` today, extended by
      `react-save-failure-surfacing.md:353-362`) deliberately drives a *server* rejection through
      the UI. Task 3 makes the `min >= max` route unreachable, so **this** spec must assert the new
      client behaviour instead: set `min_duty_cycle` above `max_duty_cycle`, click Save, and assert
      (a) the inline constraint message is visible, (b) **no** `POST /api/settings_update` was
      issued — observe it with `page.route`/`waitForRequest`, not by inference — and (c) a read-back
      of `GET /api/settings` is byte-identical to the pre-click snapshot. If the save-failure
      spec's own witness has become unreachable, **say so in the task report and propose a
      replacement rejection path** (raising `min_duty_cycle` above an existing profile's duty cycle
      still trips `_check_profiles` *before* Task 3's clamp only if the clamp is bypassed — so the
      honest replacement is a direct API POST, which is what `:110-131` already does). Do not
      silently delete either spec.
- [ ] **Step 3: Restore state in a `finally`**, not on the happy path. Both specs above should be
      read-only, but Step 1 may have to look at a wizard-owned value; if anything is written, put
      it back.
- [ ] **Step 4: Run the e2e suite. Chromium is unavailable in agent worktrees and `[chromium]`
      tests SKIP silently there** — if the run reports 0 executed, say so explicitly in the task
      report rather than claiming a pass, and re-run in the main checkout before merge.
- [ ] **Step 5: Full gate** — `bun run typecheck && bun run lint && bun run test && bun run build &&
      bun run gen:types:check` — plus the repo-root artifact check (`os_info.json` / `settings.json`
      / `pelletdb.json` absent).
- [ ] **Step 6: Commit.**

---

## Parallelization

- **Wave 0 — Task 1 alone.** It changes a primitive that every other task builds on, and it can
  shift existing tab-test observations. Nothing runs beside it.
- **Wave 1 — Task 2 alone.** It edits four files (`SettingsShell`, `PwmTab`, `WorkModeTab`,
  `StartupTab`) that Waves 2 and 3 also edit. Serialize.
- **Wave 2 — Task 3 ∥ Task 4 ∥ Task 5.** `PwmTab.tsx` / `StartupTab.tsx` /
  `RangeProfileTable.tsx` — disjoint **except** that Task 5 wires new props at call sites in both
  Task 3's and Task 4's files. **Resolution: Task 5 changes only `RangeProfileTable.tsx` and its own
  test; the two `boundaryMin`/`boundaryMax` call-site props are added by Tasks 3 and 4 in their own
  files.** Make the props optional so each task lands green alone. Isolated jj workspaces.
- **Wave 3 — Task 6 ∥ Task 7.** Task 6 touches `WorkModeTab`, `PelletsTab`, `SafetyTab`,
  `NotificationsTab`, `ControllerTab`, `settingsApi.ts`; Task 7 touches `NotificationsTab` and
  `StringListField`. **They overlap on `NotificationsTab.tsx`** — either serialize them, or split
  Task 6's one-line WLED `max={3600}` into Task 7. Prefer the split; it is one attribute.
- **Wave 4 — Task 8 alone.**

Across plans: **fully parallel with `2026-07-25-react-probe-notifications.md`** (dashboard tree,
zero shared files). **Strictly after `2026-07-25-react-save-failure-surfacing.md`** — see the Hard
Precondition. **Blocked item:** the `augerontime` bound (schema + `StartupTab.tsx:14` + Flask)
touches Python and needs a human decision; it is deliberately in no task.

---

## Self-Review

**Spec coverage.** I5 → Task 2 (all four Flask gate sites). I6 → Task 3 (all three Flask behaviours,
including the cross-section clamp the audit did not mention). I15 → Task 4 (three conditionals +
the dynamic setpoint bound + the two remembered defaults). I18 → Task 5, monotonic half only; the
"lost on tab switch" half is dropped with a citation to the recorded single-Save-per-tab decision.
M16 → Tasks 1 + 6, minus `augerontime` (decision) and minus `HistoryTab` (Slice 5). M15 → Task 7.
Plus I16, an orphan the triage assigned nowhere, adopted in Task 6 because it is one paragraph in a
file already open.

**Findings that did not survive, with evidence:** controller `option['hidden']` (zero
`hidden: true` in `controller/controllers.json`, latent-only, same disposition as `numlist`);
controller `option_min`/`option_max` (`ControllerTab.tsx:145-146` already correct); I18's
lost-on-tab-switch half (recorded decision, `PwmTab.tsx:80-82`); `smoke_plus.duty_cycle` and
`pelletlevel.warning_level` bounds (already match Flask).

**Bound disagreements surfaced, not resolved:** exactly one — `augerontime` (Flask 1000 / schema 60
/ React 60), with the circular provenance documented from the schema's and the test's own comments.
Every other bound in this slice is UI-only, schema-backed in agreement, or already correct.

**Placeholder scan:** none — every conditional, bound and clamp names its Flask line, its schema
line and its React line.

**Type consistency:** `clampToBounds` defined in Task 1, consumed in Tasks 3 and 5. `hasDcFan`
defined in Task 2, consumed in Tasks 2-4. `boundaryMin`/`boundaryMax` defined in Task 5, supplied by
Tasks 3 and 4; optional so each lands green alone. `ControllerOption.option_step` defined and
consumed inside Task 6.

**Could not verify:** nothing was executed in a browser — the claim that React's `min`/`max` are
advisory is derived from the absence of any `<form>` in the settings tree plus
`NumberField.tsx:29`, which is strong but is static reasoning; **Task 1's Step 2 tests are what turn
it into an observation, and if they show the attributes already blocking, stop and re-scope.** The
e2e steps are written against a Chromium that agent worktrees do not have.
