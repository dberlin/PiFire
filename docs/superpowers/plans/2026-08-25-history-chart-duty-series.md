# Auger + fan duty on the history chart

**Status:** design, awaiting decisions
**Date:** 2026-08-25
**Visual companion:** <https://claude.ai/code/artifact/497ccd9d-457a-4f6d-8c08-c9715fe94b15>
— live, interactive mockups of every option below. Source: `assets/duty-chart-mockups.html`.

---

## The finding that reframes the request

The history chart cannot show duty because **history does not record duty.**

Duty is computed, published, and rendered *live* — the dashboard has AUGER DUTY and FAN DUTY
tiles today — but nothing writes it to the history store. The one slot in the schema that
looks purpose-built for this records hardcoded zeros.

So this is not a charting task with a small backend tail. It is a **persistence change**
with a charting tail. Roughly 70% of the work is behind the API.

---

## Current state (verified, with citations)

### The data exists live and is already on the wire

| Stage | Location | Shape |
| --- | --- | --- |
| Computed | `controller/runtime/modes/base.py:689-707` | `cycle_ratio` 0.0–1.0, `fan_duty` 0–100 |
| Stored (live only) | `status_data`, written every 0.5 s | `common/persistence/transforms.py:42-43` |
| Published | `blueprints/mobile/socket_io.py:444-445` | `cycleRatio`, `fanDuty` |
| Pinned | `common/web_contracts/core.py:205-206` | `DashSocketPayload` |
| Rendered | `packages/pifire-core/src/dashboard/deriveView.ts:252-254` | "AUGER DUTY 42%" tile |

The computation is not trivial and already carries two mode-specific branches:

```python
# controller/runtime/modes/base.py:688-707
if mode == Mode.MANUAL:
    status_data["cycle_ratio"] = 1.0 if current.get("auger") else 0.0
    ...
else:
    status_data["cycle_ratio"] = round(self.state.cycle.ratio, 2)
    if not current.get("fan"):
        status_data["fan_duty"] = 0
    elif self.settings["platform"].get("dc_fan"):
        status_data["fan_duty"] = int(control.get("duty_cycle", 0) or 0)
    else:
        status_data["fan_duty"] = 100
```

Note the deliberate gating on the *output* rather than the request: `control['duty_cycle']`
is the duty the fan **would** be given, and reporting it for a fan that is off puts
"FAN DUTY 100%" beside "FAN IDLE". Any second implementation of this must reproduce that,
which is the argument for §2 below.

### History records neither

Columns are fixed (`common/datastore.py:34`, written at `common/persistence/history.py:111`):

```
ts, psp, primary_temps, food_temps, aux_temps, notify_targets, ext_data
```

The only extension point is `ext_data` — a nullable JSON `TEXT` column gated on
`settings.globals.ext_data`, which defaults to **False** (`common/defaults.py:59`).

### The extended-data path is a dead stub

```python
# controller/runtime/modes/base.py:944-948
if self.settings["globals"]["ext_data"]:
    in_data["ext_data"] = {}
    in_data["ext_data"]["CR"] = 0     # cycle ratio
    in_data["ext_data"]["RCR"] = 0    # raw cycle ratio
```

Hardcoded zeros. And the block runs at line ~944, **before** `self.on_tick(...)` at line 985 —
the call that actually computes the tick's cycle ratio. Populating it in place would record
the *previous* tick's value.

Two further sharp edges in that path, which matter for Option B1:

- `history_row_to_dict` (`common/persistence/transforms.py:56-71`) emits an `EXD` key **only
  when the column is non-NULL**.
- `unpack_history` (`common/common.py:300-309`) derives its entire key set from **row 0** of
  the window.

Together: turn `ext_data` on mid-cook and every subsequent row's `EXD` is silently discarded,
because row 0 didn't have one. No error, no warning, just a missing series.

---

## Decision 1 — where duty comes from (backend)

| | Approach | Cost | Verdict |
| --- | --- | --- | --- |
| **B1** | Fill the existing `ext_data` CR/RCR stub for real; add fan duty | Low | Rejected |
| **B2** | Add real `cycle_ratio` / `fan_duty` columns to `history` | Medium | **Recommended** |
| **B3** | Don't persist; overlay duty only on the live tail from the socket | Trivial | Rejected |

**Why not B1.** It ships the feature behind a default-off setting most users will never find,
so "add duty to the history graph" would produce a chart with no duty on it for almost
everyone. It inherits the row-0 key-set bug above. And a JSON blob per row across a 28,800-row
retention window costs far more to store and parse than two numeric columns.

**Why not B3.** The point of the history page is looking *back* at a cook. A live-tail-only
overlay vanishes on reload and never reaches cook files.

**Why B2.** Two numeric columns (`REAL` + `INTEGER`) cost ~12 bytes/row — under 350 KB across
the full retention window. There is direct precedent for the migration:
`_migrate_history_to_numeric_psp` (`common/datastore.py:259-275`) already rebuilds this exact
table. Rows written before the migration get `NULL`, which the frontend already handles: the
adapter null-pads short datasets and uPlot renders nulls as gaps
(`packages/pifire-core/src/history/historyAdapter.ts:54-60`).

**Retire the stub in the same change.** Once duty has real columns, the `CR`/`RCR` zeros have
no remaining reason to exist. Delete them rather than leaving two duty paths, one of which
lies.

---

## Decision 2 — one source of truth for the duty computation

Extract the block quoted above out of `_build_status_data` into a helper, and call it from
**both** the status write and the history write.

Two independent implementations would drift on exactly the cases that are hard to notice:
Manual mode's auger-bool coercion, the `dc_fan` PWM branch, and the fan-off gating. A
dashboard reading 0% while history plots 100% for the same instant is a bug nobody would
think to look for.

This is a cross-process seam — control.py writes, the web tier reads — so it needs a test that
pins **both ends**: one assertion that the status path and the history path yield the same
value for the same output state, across Manual / dc_fan / fan-off.

There is also a placement change. The duty write must happen **after** `on_tick()`, not before
it, or every recorded sample is one tick stale.

---

## Decision 3 — the downsampling trap

This one would ship silently and get misdiagnosed as "the duty line looks blocky".

`prepare_chartdata(reduce=True, tolerance=2.0)` calls
`select_indices(series, times, tolerance=tolerance, min_points=data_points)`
(`file_mgmt/cookfile.py:474`) to pick **one shared index set for every series**, using a
fidelity tolerance measured **in degrees**.

`cycle_ratio` lives in 0.0–1.0. A full 0% → 100% auger swing moves **1.0 units** — half the
2.0 °F tolerance. Duty would therefore contribute *nothing* to index selection: points get
chosen purely by temperature shape, and short auger pulses between two thermally quiet samples
are dropped outright.

The existing comment at `file_mgmt/cookfile.py:457-465` already makes precisely this argument
for `NT` and `PSP`:

> NT (targets) and PSP (primary setpoint) are step functions just like P/F — they share this
> same `window`, so they must share the same fidelity check or a step edge can be smoothed
> into a ramp that never happened.

Duty is the same class of series and needs the same treatment, with a unit correction: feed
`cycle_ratio * 100` into the fidelity check so a 2% duty change carries the same weight as
2 °F, and feed `fan_duty` as-is (already 0–100).

**Negative control for this:** with duty joining the check, a synthetic history containing a
single one-sample auger pulse on an otherwise flat temperature trace must retain that pulse
after reduction. Without the unit correction, it must not. Assert both directions — otherwise
the test proves nothing.

---

## Decision 4 — cook files (scope)

`file_mgmt/cookfile.py:105` builds cook-file charts through the same `prepare_chartdata`, with
`reduce=False`. Cook files are a versioned on-disk format, so including duty means a format
bump, and cooks saved before the change render duty as gaps.

**Recommendation: include them.** It is the same code path, the gap behaviour is already
correct, and "the live chart has duty but my saved cook doesn't" is a worse outcome than a
gap. `web-react/src/components/cookfiles/CookFileChart.tsx` (91 lines) shares the adapter and
would inherit it.

---

## Decision 5 — record *requested* duty as well as applied?

Strong recommendation: **yes**, as a third series, off by default.

The duty floor (`u_min = pulse_time / HoldCycleTime`) sets the lowest temperature the grill can
actually hold, and the shipped 25 s default cannot hold 225 °F on a real MAK. That failure is
currently invisible: the grill just runs warm, and nothing on any screen says why.

Plot requested-vs-applied duty and it becomes obvious — the controller asks for 8%, the floor
clamps to 12%, and the two lines visibly separate and stay separated. That single view turns a
subtle tuning failure into something a user can see and report.

The data exists: `controller/applied_output.py` is literally "the duty that actually reached
the auger, and why", carrying an `OutputSource` for the reason. `self.state.cycle.raw_ratio`
is already tracked alongside `.ratio` (`controller/runtime/modes/base.py:449-462`).

This is what `RCR` in the dead stub was *supposed* to be.

---

## Frontend

### The unit problem

Temperature is °F/°C on the left axis; duty is 0–100 %. They cannot share a scale — a 225 °F
trace and a 0–1 ratio on one axis renders duty as a flat line pinned to the floor.

uPlot handles this natively: give duty series a second scale key and add a second axis with
`side: 1`. Fix the duty scale to `[0, 100]` rather than letting it autoscale, so the line's
height means the same thing across every window and every cook.

### Rendering treatment

Duty is a **step function**, not a curve. Draw it with `paths: uPlot.paths.stepped({align: 1})`,
a thinner stroke, and reduced opacity, so it reads as a control signal underneath the
temperatures rather than competing with them. Interpolating between duty samples draws ramps
that never happened.

### Three visual options

| | Option | Vertical cost | Notes |
| --- | --- | --- | --- |
| **A** | Right-hand % axis, overlaid on the same plot | 0 px | **Recommended.** Cheapest, no layout risk, correlation is directly readable |
| **B** | Stacked sub-panel below, shared x-axis + synced cursor | ~90–120 px | Clearest separation; classic "volume panel". Costs space on a page already tight at 720p |
| **C** | Filled area scaled into the bottom ~20% of the temp axis | 0 px | Cheapest to build, but the y-position is meaningless — rejected |

**A over B** mainly because of the 720p constraint. The `/settings/probes` page already
overflows a 1280×720 viewport, and PiFire's target is kitchen and garage tablets. Spending
another ~100 px of vertical budget on a second panel works against that. A also puts duty and
temperature on shared gridlines, which is what makes "the auger ramped 40 s before the temp
moved" legible at a glance.

Escalate to B only if A's overlay tests badly with 4+ probes already on the plot.

### Toggle UI — the actual question asked

| | Option | Verdict |
| --- | --- | --- |
| **1** | uPlot's built-in legend (click a label to toggle `show`) | Free, already rendered — but not discoverable, and a ~12 px text target is not usable on a touchscreen |
| **2** | A chip / checkbox row above the chart | **Recommended** |
| **3** | One "Show duty" switch covering both series | Simplest, but can't isolate auger from fan |

**Option 2, and it closes a pre-existing hole.** From `historyAdapter.ts:62-69`:

> Datasets flagged `hidden` are dropped: the flag mirrors
> `not probe_config[probe]["enabled"]`, i.e. a probe the user switched off in Settings, and the
> chart has no per-series visibility toggle to defer the decision to.

Right now a disabled probe's history is silently discarded with no way to see it. A toggle row
lets `hidden` mean **off by default but available** instead of **invisible** — so the same
control that adds duty also recovers data the chart currently throws away.

Defaults: temperatures on, duty off, requested-duty off. The chart should look unchanged until
someone asks for more.

### Hard constraint: toggling must not rebuild the plot

`HistoryChart.tsx:115-119` rebuilds uPlot whenever `seriesShape` changes, and
`useStableSeriesShape` keys that shape on `label + color`. **Implementing toggles by adding or
removing entries in the `series` array changes the shape, forces a rebuild, and drops the
user's zoom on every click.**

Toggles must go through `uPlot.setSeries(i, {show})` on the live instance, with visibility held
*outside* the shape key. Every series is always constructed; only `show` changes.

This deserves its own test — it is the kind of regression that looks fine in a screenshot and
is infuriating in use.

### Cross-platform

`historyAdapter.ts` is shared: web renders with uPlot, mobile with ~220 lines of hand-rolled
SVG (`mobile/src/components/HistoryChart.tsx`). Growing the adapter's `ChartSeries` with an
`axis: "temp" | "duty"` field lets mobile filter to `temp` and ignore duty until it grows a
second axis of its own — rather than mobile silently plotting a 0–1 ratio against °F.

### Contract changes

- `HistoryDataset` is an `ExtensibleWireModel` (`extra="allow"`) — it can carry a new `axis`
  field without breaking existing validation.
- `HistoryChartData` is a strict `WireModel` (`extra="forbid"`) — any new top-level field must
  be added to the model.
- TypeScript types are **generated** into `packages/pifire-core/src/contracts/*.gen.ts`.
  Regenerate; never hand-edit.

---

## Phased plan

| Phase | Work | Gate |
| --- | --- | --- |
| **1** | Extract the duty helper; add `cycle_ratio`/`fan_duty`(/`requested_cycle_ratio`) columns + migration; write from the control loop *after* `on_tick`; delete the CR/RCR stub | Migration test on a pre-migration DB; status-vs-history agreement test across Manual / dc_fan / fan-off |
| **2** | `prepare_chartdata` emits duty datasets with `axis: "duty"`; duty joins the fidelity check in corrected units; contract + codegen | Single-sample-pulse retention test **with** its negative control |
| **3** | `HistoryChart` second scale + right axis, stepped paths, fixed `[0, 100]` domain | Visual check at 720p with 4 probes + 3 duty series |
| **4** | Toggle row; `hidden` becomes off-by-default; `setSeries` wiring | Test that toggling preserves an active zoom |
| **5** | Mobile filters to `axis === "temp"` | Existing `mobile/tests/HistoryChart.test.tsx` stays green |

### Parallelization

- **Phases 1 and 3 are independent** and can run concurrently in isolated workspaces: Phase 1
  is `controller/`, `common/persistence/`, `common/datastore.py`; Phase 3 is
  `web-react/src/components/history/`. Phase 3 can develop against a hand-built fixture with a
  synthetic duty series.
- **Phase 2 is the join point** — it needs Phase 1's column names and Phase 3's `axis`
  convention. It cannot start until both have settled their interfaces (names only, not
  implementations).
- **Phases 4 and 5 are independent of each other** and both depend only on Phase 3's series
  shape.
- Concurrent work needs isolated `jj` workspaces, not merely disjoint file lists.

---

## Decisions — settled 2026-08-25

1. **Visual treatment: A, the right-hand percent axis overlay.** Duty gets its own scale fixed
   to `[0, 100]` and a stepped path; no second panel. B and C stay documented above as the
   record of what was weighed, and B remains the escalation if A reads badly once four or more
   probes are on the plot.
2. **Toggle UI: the chip row.** One chip per series above the chart, each independent,
   36 px touch targets. `hidden` datasets become *off by default but available* rather than
   silently discarded. Visibility rides `setSeries(i, {show})`, never the series array — the
   zoom constraint above is now a hard requirement, not a caution.
3. **Cook files are in scope.** Same `prepare_chartdata` path; accepts a format bump, and cooks
   saved before the change render duty as gaps.
4. **Requested duty is recorded alongside applied.** Three duty values per sample, not two:
   applied auger, requested auger, fan.
5. **Storage: B2, real columns + migration.** Not asked directly, but decided by (4): the
   reason to record requested duty is to make the duty-floor failure *visible*, and a feature
   gated on a setting that defaults off cannot make anything visible. B1 is therefore
   incompatible with the decision above rather than merely worse than it. Flagged here in case
   avoiding schema churn matters more than (4) does.

### What this fixes in the phased plan

Phase 1 writes **three** columns (`cycle_ratio`, `requested_cycle_ratio`, `fan_duty`), so the
migration wants writing once, with all three, rather than being revisited. Phase 4 is no longer
optional polish — the chip row is the only way the duty series can be reached, so it moves onto
the critical path with Phase 3 rather than trailing it.
