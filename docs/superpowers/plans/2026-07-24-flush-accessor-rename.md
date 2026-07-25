# Plan: no destructive or state-changing operation behind a `read_*` name

**Goal:** finish the accessor-naming cleanup started by `flush_history()` /
`flush_current()`. A reader should be able to tell from a call site whether it
mutates the datastore, without opening the callee.

**Why this matters concretely:** T5's Playwright suite went red because
`settings.spec.ts` was silently wiping the entire history store. The call that
did it was spelled `read_history(flushhistory=True)`, so nothing at the call
site suggested a delete. The whole e2e suite ended up serialized
(`workers: 1`) to work around cross-test interference a truthful name would
have made obvious.

**Method note:** every call-site count below came from LSP `findReferences` or
a literal call-form search, not from guessing. `read_control` alone has 170
references of which only 5 pass the flag — do not eyeball this.

---

## Tier 1 — a destructive flag behind a read name

The original defect. Each becomes its own function; the flag disappears.

| Current | Becomes | Prod call sites |
|---|---|---|
| `read_control(flush=True)` :50 | `flush_control()` | 5 |
| `read_errors(flush=True)` :124 | `flush_errors()` | 1 |
| `read_connected_users(flush=True)` :291 | `flush_connected_users()` | 2 |
| `read_autotune(flush=True)` :509 | `flush_autotune()` | 3 |

`_flush_control()` already exists at `:34` as a private function, so that row
is mostly promotion.

### Return contracts differ — do not force symmetry

Load-bearing; getting it wrong changes behaviour silently.

| Function | Returns | Why |
|---|---|---|
| `flush_history()` | nothing | every caller discards it |
| `flush_current()` | zeroed structure | callers send it straight to a client |
| `flush_control()` | reseeded control | `control.py:94`, `controller.py:420` assign it |
| `flush_errors()` | `[]` | see below |
| `flush_connected_users()` | `[]` | current code flushes then returns `m.list()` |
| `flush_autotune()` | `[]` | current code returns `[]` |

**`flush_errors()` must return `[]`, not the pre-flush errors.** `control.py:100`
looks like a read-and-clear, which would be a bug. It is not: this is the boot
path, and the caller wants a cleared store plus a fresh accumulator to hand to
`build_devices`. "Fixing" it to return the discarded contents would change what
`build_devices` accumulates into.

---

## Tier 2 — `write_metrics` does three unrelated jobs

`write_metrics(metrics=None, flush=False, new_metric=False)` :191 is
write-named, so the flag is not lying about mutation, but it is three
functions in a trench coat:

| Mode | What it actually does | Prod sites |
|---|---|---|
| `flush=True` | `DELETE FROM metrics` | 11 |
| `new_metric=True` | stamps `starttime`/`id`, **INSERTs a new row** | 28 (all `new_metric=` uses) |
| neither | **UPDATEs the last row**, presence-based per column | remainder |

Split into `flush_metrics()`, `append_metric(metrics=None)` and
`update_metrics(metrics)`. The insert-vs-update distinction is the one that
actually bites: "start a new cook's metrics" and "amend the current record"
currently differ by one keyword.

Preserve the presence-not-truthiness semantics in the update path — the
comment at `:224-229` documents a real fix (a partial dict updates only the
columns present; an explicit `{"col": None}` still nulls it). The ledger notes
`InMemoryStore.write_metrics` already diverges from those semantics; verify
and close that while here.

**Watch:** `flush_metrics()` is already called inside `flush_history()`, and a
redundant call was just removed from `file_mgmt/cookfile.py`. Do not
double-remove or reintroduce.

---

## Tier 3 — a read that WRITES (`init=True`)

Same defect as Tier 1, different word. Each persists to the datastore as a
side effect of being read:

| Current | Writes | Becomes | Prod sites |
|---|---|---|---|
| `read_status(init=True)` :633 | builds a status dict, calls `write_status()` | `init_status()` | 10 |
| `read_settings_store(init=True)` :268 | `set_blob("settings:general", …)` | `seed_settings_store()` | 1 |
| `read_pellets_store(init=True)` :340 | `set_blob("pellets:general", …)` | `seed_pellets_store()` | 1 |

`read_status` is the big one at 10 sites. Keep the `else` branch's self-heal
comment (`:665-667`) — it explains that a fresh DB reads back `{}` rather than
crashing, and that production seeds via `init=True` first. That ordering
constraint gets clearer once the two are separate functions, not less
important.

---

## Tier 4 — one name, two return *types*

Not destructive, so lower priority, but the same "two jobs" smell with a worse
failure mode: the caller cannot tell the return type from the call.

- `read_metrics(all=True)` :173 returns a **list**; `all=False` returns a
  **dict**. 11 prod sites pass `all=True`. → `read_all_metrics()` /
  `read_metrics()`.
- `read_autotune(size_only=True)` :509 returns an **int**; otherwise a
  **list**. 2 prod sites. → `autotune_length()`.

Do these only once Tiers 1–3 are green. If they churn more call sites than
expected, this is a defensible place to stop — flag it and ask rather than
pushing through.

---

## Tier 5 — dead parameters (nearly free)

`read_settings(filename="settings.json", init=False, retry_count=0)` :236 —
**all three parameters are documented "Unused; kept for signature
compatibility"**, and the body is a one-line delegation to
`read_settings_store()`. Check whether any caller still passes them. If not,
delete all three. If some do, they are passing values that do nothing, which
is worth knowing on its own.

---

## Execution

One commit per tier row (or per function for Tier 2's split), in tier order.
Every step is a pure rename with no behaviour change, so **any test failure is
a real mistake, not a test that needs updating** — investigate, do not update
the expectation.

For each rename:

1. Add the new function; make the old one a pure read.
2. Update call sites — find them with LSP `findReferences`, not grep.
3. Update `controller/runtime/store.py`: the abstract protocol **and** both
   implementations. Add the abstract method — that is what catches a missed
   implementation immediately. When `flush_current` was added without it, the
   omission surfaced as 99 failures with one clear `TypeError: Can't
   instantiate abstract class`, which is exactly the desired outcome.
4. **Make `InMemoryStore` mirror production, not the old fake behaviour.** The
   fake's `read_current(zero_out=True)` ignored the flag entirely, so tests
   passed there while production zeroed. Preserving a known-wrong fake "to keep
   the rename behaviour-neutral" is how that gap survived; the file's own
   convention (`read_control`'s "Mirror common.read_control(flush=True)"
   comment) is to mirror. Making it correct cost nothing — 408 controller and
   characterization tests still passed.
5. Update stale comments naming the old spelling — at minimum
   `controller/runtime/controller.py:418`,
   `tests/characterization/test_controller_loop_golden.py:254`,
   `controller/runtime/store.py:124`.
6. Gate: `uvx ruff format` on changed files, `uv run ruff check`, then
   `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest -q`.
   Baseline at time of writing: **2661 passing**.

## Parallelization

Tiers 1–3 touch disjoint *functions*, but all of them edit the same two files
(`common/datastore_accessors.py`, `controller/runtime/store.py`), so isolated
workspaces would conflict on every merge. **Run sequentially.** Same reasoning
for Tiers 4 and 5.

## WAVE 2 — found by the review, NOT in the original tiers

The tier enumeration scoped itself to `common/datastore_accessors.py`. That was
too narrow: the implementer found one instance outside it
(`common/common.py::read_events_records(flush=True)`, fixed in Tier 1) and the
reviewer's independent AST scan found three more.

- [ ] **`common/system.py:144` — `get_os_info(loggername="events", persist=True)`.**
      A `get_`-named function that WRITES the datastore via `store_os_info()`,
      and **the destructive flag DEFAULTS TO TRUE** — strictly worse than every
      Tier-1 case, where the default was `False`. Three production call sites
      (`board-config.py:230,597`, `grillplat/system_commands.py:114`) all take
      the default; two tests pass `persist=False` precisely because they need a
      pure read. Split into `probe_os_info()` + `refresh_os_info()`.
- [ ] **`common/common.py:609` — `get_system_command_output()`.** Pops
      `SqliteQueue("queue_systemo")` and **silently discards every non-matching
      entry it pops**. A destructive drain behind a `get_` name, with real data
      loss for any concurrent consumer. This one is a bug, not just a naming
      problem.
- [ ] **`common/settings_migration.py:37` — `read_settings_file(..., init=False)`.**
      `init=True` runs `backup_settings()` (writes files) and `write_warning()`.
      Called with `init=True` from `common/datastore.py:279`. The comment above
      that call describes `init=True` as only "the version-overlay /
      upgrade_settings() path" — accurate but incomplete; extend it.
- [ ] **`read_warnings()` → `drain_warnings()`.** Deliberately left alone in
      wave 1 as "a genuine read-and-clear with no flag". The reviewer's counter
      is decisive: `blueprints/dash/routes.py:22` and
      `blueprints/mobile/socket_io.py:208` BOTH call it, so whichever polls
      first eats the other's warnings. That is the same cross-consumer
      interference that forced `workers: 1` on the e2e suite. 4 sites, cheap,
      and worth fixing as behaviour, not just naming.

## Not in scope

`write_control(kind=WriteKind…)` — an explicit enum, not a boolean flag, and
it names both behaviours at the call site. Leave it.
