# Control-Write Deltas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PiFire's control writers queue **what they meant** instead of **a whole control
snapshot they read**. Today every queued MERGE partial is a full (or near-full) `read_control()`
copy, and the drain *reconstructs* each writer's intent by diffing that copy against the pre-drain
ancestor. That inference is sound in one direction only, and the two places it fails are live and
user-reachable. A writer that states `{"op": "timer.clear"}` or `{"set": {"primary_setpoint": 225}}`
has nothing left to infer.

**Architecture:** A new self-describing, versioned **delta envelope** (`WriteKind.DELTA`) rides the
**same** `queue_control_write` table beside today's whole-dict patches. The drain branches on one
reserved key. Writers migrate module by module; the seam supports both the whole time. The envelope
has three channels — `set` (presence = intent, never deletes), `delete` (the only deletion channel),
and `ops` (named operations evaluated **at drain time against live state**, which is the only thing
that can fix a *coupled* member like `timer`). When the last whole-dict writer is gone,
`reduce_control_patch`, `merge_notify_data` and `CONTROL_COUPLED_MEMBERS` are deleted in this plan,
not in a follow-up.

**Tech Stack:** Python 3.14+, Flask + Socket.IO, SQLite datastore (`common/datastore.py`),
supervisor-managed `control` / `webapp` processes; React 19 + TS7 + rsbuild + Biome + @rstest/core +
bun for the one web-react task.

---

## Why this exists

`/home/dannyb/sources/PiFire-ctl/.superpowers/sdd/task-ctl-report.md` fixed the cross-writer
clobber **at the seam** (§2-§3, two slices, suite 2698 → 2747) and named the residual it could not
close (§4): *"Closing it properly requires writers to express deltas rather than whole states. That
is a call-site change across every writer, not a seam change, and it is the natural next slice."*

`/home/dannyb/sources/PiFire-wa/.superpowers/sdd/task-wa-report.md` then tried to retire the
client-side workarounds the old behaviour forced, and **refused to remove the `TimerBar` guard**
(§1), because `control["timer"]` is reduced whole and two writers that each computed a timer state
still resolve last-wins. It pinned both reachable button pairs live rather than arguing from
inference.

This plan closes both residuals and removes the workarounds they justified.

### Residual 1 — coupled members are whole-or-nothing, so they are still last-wins

`common/common.py:707` `CONTROL_COUPLED_MEMBERS = frozenset({"timer"})`, and
`common/common.py:781-785` takes that member intact or drops it. That exclusion is *correct* —
member-wise merging synthesizes `{start: 0, paused: <now>, end: 0}`, a paused timer with no
countdown, and the next `start` then arms an already-expired timer (`common/common.py:708-728`).
But whole-or-nothing means two coherent timer states still race. Pinned live:

- `tests/characterization/test_process_command_golden.py:1375`
  `test_a_pause_after_a_stop_in_one_cycle_resurrects_the_timer` asserts
  `after["timer"] == {"start": 1000.0, "paused": FIXED_NOW, "end": 2000.0}` (`:1403`).
- `tests/characterization/test_process_command_golden.py:1423`
  `test_a_resume_after_a_stop_in_one_cycle_resurrects_the_timer` asserts
  `after["timer"] == {"start": 1000.0, "paused": 0, "end": 2000.0 - 1500.0 + FIXED_NOW}` (`:1450`).

### Residual 2 — restoring the cycle's opening value expresses no intent

`common/common.py:762-770` states it in the docstring: a writer whose intent is to set a member back
to the ancestor's value is indistinguishable from one that never touched it. Live consequence:
`start` + `stop` in one cycle now leaves the timer **running**.

### The guard that is the only thing standing between users and residual 1

`web-react/src/components/shell/TimerBar.tsx:36-53` refuses a second write until the socket
republishes. The Flask dashboard (`templates/_macro_timer.html`) and the mobile Socket.IO client
have no such guard and can still hit it.

### The invariant this plan buys

> **N control commands issued inside one control cycle must produce the same control state as the
> same N commands issued one cycle apart.**

Both residuals are violations of it. Every task below is measured against it, and Task 17 pins it
as an executable property. It is **not** claimed for request-time *validation* — see
"Where the invariant does not hold" in Verified facts.

---

## Global Constraints

- **Python 3.14+.** `except A, B` **without parentheses** is ruff-canonical in this repo. Do **not**
  "fix" it to `except (A, B)`.
- **`uvx ruff format <changed .py files>` before EVERY commit.** Standing repo rule, every task.
- **Python gate, run per task:**
  `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
  Baseline **2796 passing**. It must only go **up**.
- **If `web-react` changes** (Task 10 only): **bun**, never npm. **@rstest/core** (`rs.fn`, `rs.mock`)
  — **`vi` does NOT exist**. Gate:
  `bun run typecheck && bun run lint && bun run test && bun run gen:types:check && bun run build`.
  `bun run lint` must be **0 errors and exactly 2 pre-existing `react-refresh` warnings**.
  `bun run test` baseline **954 passing** (measured 2026-07-25).
- **No lint suppressions, no `any`, no `@ts-ignore`, no `@ts-expect-error`, no `biome-ignore`,
  no `# noqa`.**
- **SHA-pinned golden fixtures exist over the command path.**
  `tests/characterization/fixtures/process_command_golden.json` is digest-pinned by
  `GOLDEN_SHA256` at `tests/characterization/test_process_command_golden.py:130`, and
  `tests/oracle/fixtures/*.json` are byte-asserted. **Any flip is a real behaviour change to be
  justified in the report, never silently re-baselined.**
- `process_command` pads `arglist` to `max_args = 4`
  (`common/api_commands.py:869-874`) and that padding is observed by the golden fixture
  (`arglist_after` in all 113 entries), **so a fifth argument is not free.** No task here adds one.
- **jj flags — this version has no `-F`:** `jj describe` accepts `-m <MSG>` or `--stdin`;
  `jj squash` accepts `-m` or `-u`/`--use-destination-message`. Any of these **without** a message
  flag opens an editor and **hangs forever**. For a long message, pipe it:
  `jj describe --stdin < file`.
- **Backticks inside a double-quoted shell arg are eaten by zsh.** Use `-F file`-style heredocs or
  `--stdin` for any commit message containing backticks.
- **Never run Playwright / `[chromium]` tests** — another agent owns the browser. See
  "Could NOT verify" for what that costs.
- Other agents are live in sibling `PiFire-*` workspaces. **Do not touch those directories.**

---

## Verified facts (checked against live code — do not re-derive, do not guess)

### The writer inventory (LSP, not grep)

Driven with `pyright-langserver --stdio`, `textDocument/didOpen` sent for **all 401 `.py` files in
the checkout** before querying (pyright analyses OPEN files only), then
`textDocument/references` on the definition at `common/datastore_accessors.py:70`.
**200 references total; 120 outside `tests/`.** Subtracting 15 import lines, 1 definition and 1
pass-through (`controller/runtime/store.py:385`) leaves **103 call sites**:

| Module | MERGE sites (queued — in scope) | OVERWRITE sites (control process — out of scope) |
|---|---|---|
| `common/api_commands.py` | 23 — `:112, 303, 318, 321, 338, 350, 369, 394, 415, 428, 484, 500, 515, 534, 633, 675, 680, 686, 695, 704, 710, 716, 754` | — |
| `blueprints/mobile/socket_io.py` | 12 — `:439, 516, 525, 542, 552, 603, 697, 706, 713, 723, 743, 1026` | — |
| `display/_base_flex.py` | 18 — `:1307, 1314, 1321, 1336, 1357, 1365, 1371, 1382, 1388, 1394, 1404, 1416, 1420, 1424, 1430, 1441, 1452, 1463` | — |
| `display/_base_fixed.py` | 12 — `:1089, 1141, 1151, 1161, 1170, 1204, 1224, 1235, 1261, 1274, 1278, 1282` | — |
| `blueprints/tuner/routes.py` | 8 — `:46, 51, 55, 61, 71, 76, 111, 119` | — |
| `display/ssd1306b.py` | 7 — `:385, 437, 446, 456, 467, 483, 495` | — |
| `display/qtquick_flex.py` | 6 — `:106, 119, 122, 127, 130, 137` | — |
| `blueprints/admin/routes.py` | 3 — `:88, 92, 137` | — |
| `blueprints/pellets/routes.py` | 3 — `:31, 44, 120` | — |
| `blueprints/settings/routes.py` | 2 — `:101, 300` | — |
| `blueprints/api/routes.py` | 1 — `:210` (`POST /api/control` pass-through) | — |
| `common/app.py` | 1 — `:405` (`save_settings_and_flag_update`) | — |
| `common/system.py` | 1 — `:341` (`gather_system_info`) | — |
| `common/datastore_accessors.py` | — | 1 — `:57` (`flush_control`) |
| `common/process_mon.py` | — | 1 — `:99` (heartbeat timeout) |
| `controller/runtime/devices.py` | — | 2 — `:143, 166` |
| `notify/notifications.py` | — | 1 — `:169` |
| **Total** | **97** | **5** |

**LSP limitation, stated rather than hidden:** `controller/runtime/modes/base.py` reaches the store
through `self.ctx.store.write_control(...)` / `ctx.store.write_control(...)`, and pyright resolves
those to neither the accessor (0 refs) nor the `Store` ABC method
(`controller/runtime/store.py:66`, **1** ref — the declaration only), because `ctx.store` is
untyped. Those 10 sites (`modes/base.py:229, 276, 335, 351, 365, 374, 473, 535, 561, 669`) were
found by grep and confirmed by reading: **every one is `WriteKind.OVERWRITE, origin="control"` and
runs inside the control process**, so none is queued and none is in scope.

### Writers that ALREADY queue minimal partials

Not every writer sends a whole dict, and the seam already tolerates minimal ones:

- `display/_base_flex.py:1307` — `data = {"updated": True, "mode": "Monitor"}` and 17 siblings.
- `display/qtquick_flex.py:106-137` — `{"updated": True, "mode": "Hold", "primary_setpoint": temp}`,
  `{"s_plus": toggle}`, `{"notify_data": control["notify_data"]}`, etc.
- `blueprints/pellets/routes.py:112-120` — `control.clear()` then `control["hopper_check"] = True`,
  i.e. `{"hopper_check": True}`.

These 25 sites become deltas by **wrapping**, with no read-modify-write to unpick.

### The seam as it stands

- `common/datastore_accessors.py:70-85` `write_control`: `OVERWRITE` writes the blob;
  `MERGE` stamps `control["origin"] = origin` **into the caller's dict** and pushes it onto
  `SqliteQueue("queue_control_write")`.
- `common/sqlite_queue.py:37-46` — push is `json.dumps`; pop is `json.loads`, FIFO by
  `id INTEGER PRIMARY KEY AUTOINCREMENT`. `_ALLOWED_TABLES` at `:9-17` whitelists the table names.
- `common/datastore_accessors.py:116-161` `execute_control_writes`: seeds the blob if absent
  (`:120-121`), captures `base = read_control()` **once** (`:125`), then per patch:
  `strip_null_members` → `reduce_control_patch(patch, base)` → if `notify_data` survives,
  `merge_notify_data(base, read_control(), patch)` → `UPDATE kv SET value = json_patch(value, ?)`.
- `controller/runtime/store.py:155-180` `InMemoryStore` mirrors it exactly, using `deep_update`
  where SQLite uses `json_patch` (both replace lists wholesale).
- `common/common.py:180-208` `strip_null_members` — **lists are returned unchanged** (`:186-188`),
  which is why `notify_data[*].eta = null` survives and why arrays need their own merge.

### The control dict's shape

`common/defaults.py:442-512` `default_control()`. **Exactly one array** — `notify_data`
(`:497`, built by `default_notify` at `:516-569`). `control["timer"]` is
`{"start": 0, "paused": 0, "end": 0, "shutdown": False}` (`:499`); the `shutdown` member has
exactly one consumer, `controller/runtime/modes/base.py:320`. `control["manual"]` is
`{"change": False, "pwm": 100}` (`:501`) — no `output` key until a writer adds one.

`notify_data` entry identity is `(label, type)` (`common/common.py:796-806`); `label` alone is not
unique — each probe contributes `probe`, `probe_limit_high`, `probe_limit_low`. The **timer** entry
is `{"label": "Timer", "type": "timer", "req": False, "shutdown": False, "keep_warm": False}`
(`common/defaults.py:550-552`), but `_cmd_set_timer` locates it by `type == "timer"` alone
(`common/api_commands.py:649-651`), **not** by label. Every op in this plan does the same.

### Exactly what the timer writers do today

`common/api_commands.py:633-716` `_cmd_set_timer` (+ `_timer_start_with_options` at `:576-632`):

| REST form | Branch condition | Mutations |
|---|---|---|
| `start/{s}/{options}` | `paused != 0` → **ERROR, writes nothing** (`:620-623`); `seconds <= 0` or non-numeric → ERROR (`:615-619`); bad options → ERROR (`:603-610`) | `notify[timer].req=True, .shutdown=opt, .keep_warm=opt`; `timer.start=now`; `timer.end=now+seconds` (`:625-629`). **Never touches `paused`.** |
| `start/{s}` | `paused == 0` | `notify[timer].req=True` (set **before** the branch, `:665`); `timer.start=now`; `timer.end = now + (int(float(s)) if is_float(s) else 60)` (`:667-673`) |
| `start/{s}` | `paused != 0` | `notify[timer].req=True`; `timer.end = (end - paused) + now`; `timer.paused = 0` (`:675-678`) |
| `pause` | `start != 0` | `notify[timer].req=False`; `timer.paused = now` (`:680-683`) |
| `pause` | `start == 0` | `req=False`; `start=end=paused=0`; `shutdown=False`; `keep_warm=False`; logs `"Timer cleared."` (`:685-693`) |
| `stop` | — | **byte-identical to the `pause`/`start == 0` branch**, logs `"Timer stopped."` (`:695-704`) |
| `shutdown/{b}`, `keep_warm/{b}` | — | one `notify[timer]` flag each (`:705-716`) |

`blueprints/mobile/socket_io.py:690-745` `_post_app_data_timer` is a second, independent
implementation of start / unpause / pause / stop over the same fields.

**No display writes `control["timer"]`** — grepped across `_base_fixed.py`, `_base_flex.py`,
`ssd1306b.py`, `qtquick_flex.py`: zero hits. So the complete set of timer writers is:
`_cmd_set_timer`, `_timer_start_with_options`, `_post_app_data_timer`, plus the two arbitrary-patch
doors below.

### The two arbitrary-patch doors

- `blueprints/api/routes.py:204-213` `_api_post_control` — `write_control(request_json, MERGE)`.
  Whatever JSON the client posts is queued verbatim. Pinned by
  `tests/web/test_page_api.py:134-148` (`{"mode": "Startup", "s_plus": True}` → 201 → drain →
  applied) — **a `[chromium]` test.**
- `blueprints/mobile/socket_io.py:432-441` `_post_app_data` `type == "control"` — the same, gated on
  at least one posted key existing in `read_control()`.

**Key insight:** a client-posted patch is *already* a statement of intent — the client sent only
what it means. These doors need **no client change** to become deltas; the server wraps them.
`web-react/src/helpers/notify/notifyApi.ts:51-67` (`postControl`) and `:69-77` (`postNotifyData`)
post exactly this shape, and `web-react/src/helpers/command.ts:195` (`recipeNextStep`) posts
`{updated: true}`.

### The golden fixture

- Format is exactly `json.dumps(obj, indent=2, sort_keys=True) + "\n"` — **verified by round-trip**
  (indent=2/sorted + trailing newline reproduces the file byte-for-byte; indent=4 does not).
- 113 entries. `queued_writes` is `[{"origin": ..., "diff": _diff(pre_control, q)}]`
  (`tests/characterization/test_process_command_golden.py:799`) — a diff of the **raw queued
  payload** against `pre_control`, with `_flatten` treating lists as leaves (`:632-641`).
- There is **deliberately no capture script and no `--update-golden`** (`:10-11`). Every edit is by
  hand plus a hand-edited `GOLDEN_SHA256`.
- **Six entries queue a timer write and will flip in Task 8:** `set_timer_start`,
  `set_timer_start_default_60`, `set_timer_start_resume`, `set_timer_stop`,
  `set_timer_pause_running`, `set_timer_pause_not_started`. Their `log_calls` are non-empty
  (`['Timer started.  Ends at: 17:18:20']` etc.) and **must not change** — see the logging decision
  in Task 6.
- `set_timer_shutdown_true/false` and `set_timer_keep_warm_true/false` are `notify_data` writers and
  flip in Task 11, not Task 8.
- The 4-argument options form has **no golden CASE** — only inline tests at `:1102-1215`.
- `tests/oracle/fixtures/control_merge.json` pins `write_control({"nested": {"b": 9, "c": 3}},
  MERGE)` — a **minimal** partial with a single writer. It is untouched by this plan and is the
  guard that the legacy MERGE primitive keeps working.

### Cross-process facts (these drive the upgrade answer)

- `common/datastore.py:11` — `DB_PATH = os.environ.get("PIFIRE_DB_PATH", <checkout>/pifire.db)`.
  A real install's `pifire.db` is a **file that survives an upgrade**, queue rows included.
- `control.py:102` — the control process calls `flush_control()` at **every boot**, and
  `common/datastore_accessors.py:52-57` does `DELETE FROM queue_control_write` (plus systemq/systemo),
  `delete_blob("control:general")`, and reseeds `default_control()`. **Any write queued when the
  control process restarts is already dropped today.**
- `updater/upgrade.sh` — 186 lines; `uv sync --no-dev --inexact` at `:128`, wizard/updater/board-config
  runs at `:155-170`, supervisor `.conf` files copied at `:174-184`. **The script itself never
  restarts supervisor**; the restart comes from `common/system.py:51-80` `restart_scripts()`
  (`systemctl restart supervisor|supervisord`, falling back to `service`), which restarts **both**
  the `control` and `webapp` programs. There is no ordering guarantee between them.

### Where the invariant does NOT hold, and why that is not this plan's failure

`_timer_start_with_options` rejects a **paused** timer at request time from a stale
`read_control()` (`common/api_commands.py:620-623`), and `_post_app_data` `type == "control"`
rejects unknown keys the same way. Stop-then-startWithOptions in one cycle is rejected; one cycle
apart it is accepted. **Deltas cannot close this** — the HTTP response is synchronous and the queue
is not readable as state. This plan does not claim to, and Task 17's property test scopes itself to
the control state produced by **accepted** commands.

---

## The delta representation

### The envelope (wire format, version 1)

```json
{
  "__control_delta__": 1,
  "origin": "app",
  "set":    { "mode": "Hold", "primary_setpoint": 225, "updated": true },
  "delete": [ ["recipe", "step_data"] ],
  "ops":    [ { "op": "timer.clear" } ]
}
```

Every member except `__control_delta__` is optional. The top-level key set is a **strict
whitelist** — `{__control_delta__, origin, set, delete, ops}` — and anything else is a
`ControlDeltaError` raised at **push** time, in the writing process, where the traceback is useful.

### `set` — presence is intent, and it NEVER deletes

`set` is deep-merged into the control dict (`common.common.deep_update`, `common/common.py:698-704`
— dict values recurse, everything else assigns). A key's **presence** is the writer's intent; its
**absence** is silence. A `None` value **assigns null**; it does not delete. That is deliberate and
is the whole reason the drain's Python-side application replaces `json_patch` for deltas: under
RFC 7386 a null member deletes, which is why `strip_null_members` exists at all
(`common/common.py:180-193`), and why `notify_data[*].eta = null` had to be special-cased.
Under a delta there is nothing to strip — nulls are just values.

`set` **must not contain `timer` or `notify_data`.** Validation rejects both. That rule is what
makes deleting `CONTROL_COUPLED_MEMBERS` sound: once no path can write a *value* into
`control["timer"]`, there is no coupled-member race left to exclude.

### `delete` — the only deletion channel

A list of **paths**, each a non-empty list of string keys: `[["recipe", "step_data"], ["system"]]`.
Applied after `set`. A path that does not exist is a **no-op** (idempotent — a delta may be the
second of two in a cycle).

**No writer in this plan emits a `delete`.** It exists because "how is deletion expressed, versus a
key simply being absent" has to have an answer *before* someone needs one — the absence of a
deletion channel is precisely what forced `saveTargetEdit` to post a whole array
(`notifyApi.ts:15-17`: *"an entry the incoming array omits is read as a deletion, not as silence"*).
It is covered by unit tests in Task 3 and has no production call site. **Rejected alternative:** omit
`delete` entirely and add it on first need — rejected because the validator's `set`-never-deletes
rule is only defensible if there *is* another way to delete.

### `ops` — named operations, evaluated at DRAIN time

An ordered list. Each op is applied in order against the **live, evolving** control dict inside the
drain, after `set` and before `delete`. This is the half that closes residual 1: two ops that both
concern the timer no longer race, because the second one *sees the first one's result*.

The clock does **not** move to the drain. Every time-bearing op carries `"at"`, the writer's
`time.time()`. So `end = at + seconds` is computed from the request's clock exactly as today —
the golden's `FIXED_NOW` values are reproduced byte-for-byte — while the **branch** is taken
against live state. That split is the design's load-bearing idea.

| op | fields | drain-time behaviour |
|---|---|---|
| `timer.clear` | — | `start=end=paused=0`; `notify[type=timer].req=shutdown=keep_warm=False` |
| `timer.pause` | `at` | `start != 0` → `req=False`, `paused=at`; else → exactly `timer.clear` |
| `timer.start_or_resume` | `at`, `seconds` (int or `null`) | `req=True`; then `paused == 0` → `start=at`, `end=at+(seconds if not null else 60)`; else → `end=(end-paused)+at`, `paused=0` |
| `timer.start_with_options` | `at`, `seconds` (int > 0), `shutdown`, `keep_warm` | `paused != 0` at drain → **drop + ERROR log** (request time already rejected this; state moved under us); else `req=True`, `shutdown`, `keep_warm`, `start=at`, `end=at+seconds` |
| `notify.set` | `label`, `type`, `fields` | field-merge into the `(label, type)` entry; **append** it if absent |
| `notify.delete` | `label`, `type` | drop the `(label, type)` entry |
| `notify.replace` | `entries` | replace the whole array |

`notify.replace` is how "an omitted entry means delete" stops being implicit. `saveTargetEdit` and
the factory-defaults reseed both genuinely mean *replace*; naming it removes the ambiguity that
`merge_notify_data`'s "entry in base but not in incoming → removed" rule encodes today
(`common/common.py:866-867`).

### Rejected representations

| Rejected | Why |
|---|---|
| **RFC 6902 JSON Patch** (`op/path/value` with `/`-pointers) | Buys `remove` and `test`, but is still *value*-oriented: a stop and a pause are two `replace` sets on `/timer` and race identically. It does not touch residual 1, which is the reason this plan exists. |
| **Whole patch + a `touched` path list** (keep the reduce, but read intent from an explicit list instead of a diff) | Closes residual 2 cleanly and is a smaller change. Does **nothing** for residual 1 — both writers "touched" `timer`. And it keeps every writer doing read-modify-write, which is the shape we are trying to delete. |
| **A second queue table `queue_control_delta`** | Structurally immune to an old drain (it cannot see a table it does not know), but splits the FIFO: two `AUTOINCREMENT` sequences means the relative order of a legacy patch and a delta in one cycle is lost, and order is exactly what decides a genuine same-field conflict. Bought safety we do not need — see the upgrade analysis. |
| **A dual-view envelope** (legacy partial *and* `__control_delta__` in one payload, so an old drain applies the legacy view) | Genuinely gives zero behavioural cliff mid-upgrade, but every converted writer must keep computing the stale whole-dict view for one release — which is the read-modify-write we are removing — and it writes a junk `__control_delta__` key into `control:general` on the old path anyway. Cost/benefit fails against a window bounded by `control.py:102`. |
| **Moving `time.time()` into the drain** | Would let ops be pure. Changes every timer timestamp by up to one control cycle, flips six golden `log_calls`, and moves "Timer started" logging from the web process to the control process. `at` gets the same correctness for none of that. |

---

## Cross-process compatibility during an upgrade

Two directions, answered separately. Neither is hand-waved: both are bounded by
`control.py:102`'s boot flush, which is an **existing** guarantee this plan does not change.

### Direction A — an OLD queued write drained by NEW code

**Safe by construction, and it is the dominant path for the whole migration.** A legacy whole-dict
patch has no `__control_delta__` key, so `is_control_delta()` is false and the new drain takes the
byte-identical existing branch: `strip_null_members` → `reduce_control_patch` → `merge_notify_data`
→ `json_patch`. This is not a compatibility shim bolted on for upgrades — it is the same code path
97 writers use until the last one is converted, exercised by the whole suite on every task.

### Direction B — a NEW delta drained by OLD code

Reachable only inside one window: **the `webapp` process running new code while the `control`
process is still running old code.** `restart_scripts()` restarts supervisor, which restarts both
programs with no ordering guarantee, so the window is the gap between the two — sub-second to a few
seconds — and the user must issue a control command inside it.

What happens: old `execute_control_writes` does not recognise the envelope, `strip_null_members`
leaves it alone (no nulls), `reduce_control_patch` finds `__control_delta__`/`set`/`ops` absent from
`base` and keeps them (`common/common.py:776-779` — "new member: the writer is adding it"), and
`json_patch` writes them into `control:general` as literal keys.

**Consequences, precisely:**

1. **The user's command does nothing.** No mode change, no timer, no flag.
2. **Three junk keys land in `control:general`.** The control loop reads only named keys, so nothing
   acts on them; they are inert.
3. **Both are erased at the next control-process boot**, which is *the very next thing that happens*
   — `control.py:102` `flush_control()` deletes `control:general` and reseeds `default_control()`.

**So the new failure mode is identical to the existing one:** a control write queued when the
control process restarts is dropped today, by the same flush, for the same reason. This plan adds
no new class of upgrade failure. It is a **lost command in a multi-second window during an
upgrade** — the same thing a user already gets by pressing a button while supervisor is cycling.

### Direction B′ — a FUTURE delta version drained by THIS code

The one that recurs once deltas exist. Handled explicitly, in Task 4:
`apply_control_delta` compares `envelope[CONTROL_DELTA_KEY]` against `CONTROL_DELTA_VERSION` and,
on a mismatch, **drops the envelope and logs at ERROR** to the `control` logger — it never applies a
partially-understood delta. Pinned by
`test_apply_control_delta_drops_an_unknown_version_and_logs`.

### Mitigations deliberately NOT taken

- **A `queue_control_write` schema/version column.** The queue is a bare `(id, value TEXT)` table
  (`common/datastore.py:115`); adding a column is a migration on a file that survives upgrades, for
  a window the boot flush already closes.
- **Draining the queue on `webapp` boot.** The web process does not own the queue and cannot know
  whether the control process has newer or older code.

---

## File Structure

**Create**
- `common/control_delta.py` — the envelope: `CONTROL_DELTA_KEY`, `CONTROL_DELTA_VERSION`,
  `ControlDeltaError`, `control_delta()`, `is_control_delta()`, `validate_control_delta()`,
  `apply_control_delta()`, and the seven op appliers.
- `tests/unit/common/test_control_delta_envelope.py` — construction + validation (Task 1).
- `tests/unit/common/test_control_delta_apply.py` — `set` / `delete` application (Task 2, extended
  by Task 3).
- `tests/unit/common/test_control_delta_timer_ops.py` — the four timer ops (Task 4).
- `tests/characterization/test_control_delta_seam.py` — the mixed-queue drain (Task 5) and the
  one-cycle/N-cycle property (Task 17).

**Modify**
- `common/common.py` — `WriteKind.DELTA` (Task 1); deletions in Tasks 9 and 16.
- `common/datastore_accessors.py` — `write_control` DELTA branch, `execute_control_writes` delta
  branch (Tasks 1, 5).
- `controller/runtime/store.py` — `InMemoryStore.write_control` / `.execute_control_writes` mirror
  (Task 5).
- `common/api_commands.py` — timer ops (Task 6), notify ops (Task 11), scalars (Task 12).
- `blueprints/mobile/socket_io.py` — timer + the `control` door (Task 7), the rest (Task 15).
- `blueprints/api/routes.py` — `_api_post_control` wrapping + `timer` rejection (Task 7).
- `blueprints/tuner/routes.py`, `blueprints/admin/routes.py`, `blueprints/pellets/routes.py`,
  `blueprints/settings/routes.py`, `common/app.py`, `common/system.py` (Task 15).
- `display/_base_fixed.py`, `display/ssd1306b.py` (Task 13); `display/_base_flex.py`,
  `display/qtquick_flex.py` (Task 14).
- `tests/characterization/fixtures/process_command_golden.json` +
  `tests/characterization/test_process_command_golden.py` (`GOLDEN_SHA256`, the harness's
  `queued_writes` capture, the two resurrection tests) — Tasks 8, 11, 12.
- `tests/characterization/test_control_writes_cross_writer.py` — Tasks 8, 15, 16.
- `tests/unit/datastore/test_sqlite_store_parity.py` — delta parity pin (Task 5).
- `tests/unit/common/test_write_kind.py` — the three-member enumeration (Task 1).
- `web-react/src/components/shell/TimerBar.tsx`,
  `web-react/src/components/shell/TimerBar.controlCycle.test.tsx` (Task 10);
  `web-react/src/helpers/command.ts` comments only (Task 8).

**Delete**
- `common/common.py`: `CONTROL_COUPLED_MEMBERS` (`:707-728`) and `reduce_control_patch`'s `coupled`
  branch (`:781-785`) — Task 9.
- `common/common.py`: `reduce_control_patch` (`:731-793`), `merge_notify_data` (`:831-895`),
  `notify_data_key` (`:796-806`), `_key_notify_data` (`:809-828`) — Task 16.
- `tests/unit/common/test_reduce_control_patch.py` (15 tests),
  `tests/unit/common/test_merge_notify_data.py` (12 tests) — Task 16.
- The `TimerBar` write guard (`TimerBar.tsx:36-53`) and its trailing rationale block — Task 10.

**Explicitly NOT touched**
- `/api/set/timer/start/{seconds}/{options}` and `command.timerStartWithOptions`. Its *one-write*
  rationale dies in Task 8 and the comments saying so are corrected there, but the **endpoint
  stays**: it computes `end` from the **server** clock (`common/api_commands.py:576-589`,
  `command.ts:122-146`) and rejects non-numeric / zero / negative durations and a paused timer
  (`:611-623`). Both reasons are independent of the write seam.
- Every `WriteKind.OVERWRITE` site (`controller/runtime/modes/base.py`, `devices.py`,
  `notify/notifications.py`, `process_mon.py`, `flush_control`). They run in the control process and
  never touch the queue.
- `tests/oracle/fixtures/*.json` — the legacy MERGE primitive survives this plan intact, which is
  what `control_merge.json` pins.
- `web-react/src/components/dashboard/**` and `SetpointEntry`'s `saving` prop — an ordinary
  double-submit guard, not a seam workaround (task-wa report §2).

---

## Coordination

| Concern | Resolution |
|---|---|
| Sibling `PiFire-*` workspaces are live | This plan runs in its **own** jj workspace. Never edit a sibling. Copy `.lsp.json` into a new workspace (it is gitignored, and its absence is the real cause of "LSP unavailable") and run `bun install` if the workspace will run Task 10. |
| Playwright / `[chromium]` | Never run. **Task 7** touches the route covered by `tests/web/test_page_api.py::test_post_control_merges_via_write_control[chromium]`; it must be re-run in the main checkout by whoever merges. Recorded in "Could NOT verify". |
| `web-react` is edited by other plans | Only **Task 10** (logic) and **Task 8** (comments in `helpers/command.ts`) touch `web-react`. If another live plan owns `components/shell/TimerBar*` or `helpers/command.ts`, serialize behind it. |

---

### Task 1: The envelope — `WriteKind.DELTA` and a validated constructor

**Files:** create `common/control_delta.py`, `tests/unit/common/test_control_delta_envelope.py`;
modify `common/common.py`, `common/datastore_accessors.py`.

**Step 1 — the failing test.** Create `tests/unit/common/test_control_delta_envelope.py`:

```python
"""The delta envelope is a CROSS-PROCESS wire format: the web process builds it,
the control process reads it. Both ends are pinned here."""

import pytest

from common.common import WriteKind
from common.control_delta import (
    CONTROL_DELTA_KEY,
    CONTROL_DELTA_VERSION,
    ControlDeltaError,
    control_delta,
    is_control_delta,
    validate_control_delta,
)


def test_write_kind_has_a_delta_member_distinct_from_merge():
    assert WriteKind.DELTA is not WriteKind.MERGE
    assert WriteKind.DELTA is not WriteKind.OVERWRITE


def test_a_set_only_delta_has_exactly_the_expected_wire_shape():
    assert control_delta(set_values={"mode": "Hold", "primary_setpoint": 225}) == {
        CONTROL_DELTA_KEY: CONTROL_DELTA_VERSION,
        "set": {"mode": "Hold", "primary_setpoint": 225},
    }


def test_empty_members_are_omitted_not_emitted_as_empty_containers():
    assert control_delta(set_values={"updated": True}) == {
        CONTROL_DELTA_KEY: 1,
        "set": {"updated": True},
    }


def test_is_control_delta_distinguishes_an_envelope_from_a_legacy_partial():
    assert is_control_delta(control_delta(set_values={"updated": True})) is True
    assert is_control_delta({"updated": True, "mode": "Hold"}) is False
    assert is_control_delta(None) is False
    assert is_control_delta([{"op": "timer.clear"}]) is False


def test_set_may_not_carry_timer():
    """The rule that makes deleting CONTROL_COUPLED_MEMBERS sound."""
    with pytest.raises(ControlDeltaError, match="timer"):
        control_delta(set_values={"timer": {"start": 0, "paused": 0, "end": 0}})


def test_set_may_not_carry_notify_data():
    with pytest.raises(ControlDeltaError, match="notify_data"):
        control_delta(set_values={"notify_data": []})


def test_an_unknown_top_level_key_is_rejected():
    with pytest.raises(ControlDeltaError, match="patch"):
        validate_control_delta({CONTROL_DELTA_KEY: 1, "patch": {}})


def test_origin_is_an_allowed_top_level_key():
    validate_control_delta({CONTROL_DELTA_KEY: 1, "set": {"updated": True}, "origin": "app"})


def test_an_unknown_op_name_is_rejected():
    with pytest.raises(ControlDeltaError, match="timer.frobnicate"):
        control_delta(ops=[{"op": "timer.frobnicate"}])


def test_timer_pause_requires_at():
    with pytest.raises(ControlDeltaError, match="at"):
        control_delta(ops=[{"op": "timer.pause"}])


def test_timer_clear_takes_no_fields():
    assert control_delta(ops=[{"op": "timer.clear"}]) == {
        CONTROL_DELTA_KEY: 1,
        "ops": [{"op": "timer.clear"}],
    }


def test_notify_set_requires_label_type_and_fields():
    with pytest.raises(ControlDeltaError, match="fields"):
        control_delta(ops=[{"op": "notify.set", "label": "Grill", "type": "probe"}])


def test_delete_paths_must_be_non_empty_lists_of_strings():
    assert control_delta(delete_paths=[["recipe", "step_data"]]) == {
        CONTROL_DELTA_KEY: 1,
        "delete": [["recipe", "step_data"]],
    }
    with pytest.raises(ControlDeltaError, match="delete"):
        control_delta(delete_paths=[[]])
    with pytest.raises(ControlDeltaError, match="delete"):
        control_delta(delete_paths=[["recipe", 3]])


def test_the_constructor_deep_copies_so_a_later_mutation_cannot_reach_the_queue():
    values = {"manual": {"pwm": 50}}
    envelope = control_delta(set_values=values)
    values["manual"]["pwm"] = 99
    assert envelope["set"]["manual"]["pwm"] == 50
```

**Expected failure:** `ModuleNotFoundError: No module named 'common.control_delta'` on collection,
and `AttributeError: DELTA` once the module exists but `WriteKind` has not been extended.

**Step 2 — `WriteKind.DELTA`.** `common/common.py:40-42` currently reads:

```python
class WriteKind(Enum):
    OVERWRITE = "overwrite"  # replace control:general wholesale (legacy True)
    MERGE = "merge"  # queue a partial change, deep-merged on execute (legacy False)
```

Add a third member:

```python
class WriteKind(Enum):
    OVERWRITE = "overwrite"  # replace control:general wholesale (legacy True)
    MERGE = "merge"  # queue a partial change, deep-merged on execute (legacy False)
    # queue a validated intent envelope (common/control_delta.py): the writer
    # states what it MEANT, not the whole snapshot it read. MERGE keeps its
    # meaning; the two coexist on one queue for the whole migration.
    DELTA = "delta"
```

`tests/unit/common/test_write_kind.py:5-9` is `test_write_kind_is_enum_with_two_members`, which
asserts `{m.name for m in WriteKind} == {"OVERWRITE", "MERGE"}`. **Rename it to
`test_write_kind_is_enum_with_three_members` and add `"DELTA"` in the same commit** rather than
letting it fail; that file's other two tests (`:12-24`) are unaffected.

**Step 3 — `common/control_delta.py`.** Constants, error, constructor, predicate, validator:

```python
"""
==============================================================================
 PiFire Control Deltas
==============================================================================

Description: The queued-control-write payload that states a writer's INTENT
  instead of the whole control snapshot it happened to read.

  This is a CROSS-PROCESS wire format. The web process (and the display
  process) build envelopes with control_delta(); the control process applies
  them in execute_control_writes(). Both ends are versioned, both ends are
  validated, and the wire shape is pinned by
  tests/unit/common/test_control_delta_envelope.py and
  tests/unit/datastore/test_sqlite_store_parity.py.
==============================================================================
"""

import copy
import logging
from collections.abc import Mapping

CONTROL_DELTA_KEY = "__control_delta__"
CONTROL_DELTA_VERSION = 1

#: Top-level members an envelope may carry. A strict whitelist, not a minimum:
#: a key we do not recognise means the writer and this reader disagree about
#: the format, and applying the half we understand is worse than dropping it.
_ALLOWED_MEMBERS = frozenset({CONTROL_DELTA_KEY, "origin", "set", "delete", "ops"})

#: Members that may never appear in `set`. `timer` is a coupled value object
#: (start/paused/end are one countdown and the code branches on their
#: COMBINATIONS) and `notify_data` is an array whose elements need addressing;
#: both are expressible only as ops, which is what lets the drain stop guessing.
_SET_FORBIDDEN = frozenset({"timer", "notify_data"})

_OP_FIELDS = {
    "timer.clear": (),
    "timer.pause": ("at",),
    "timer.start_or_resume": ("at", "seconds"),
    "timer.start_with_options": ("at", "seconds", "shutdown", "keep_warm"),
    "notify.set": ("label", "type", "fields"),
    "notify.delete": ("label", "type"),
    "notify.replace": ("entries",),
}
CONTROL_DELTA_OPS = frozenset(_OP_FIELDS)


class ControlDeltaError(ValueError):
    """A malformed envelope. Raised at PUSH time, in the writing process, so the
    traceback points at the writer rather than at a control-loop drain in
    another process minutes later."""


def control_delta(set_values=None, delete_paths=None, ops=None):
    """Build a validated delta envelope.

    :param set_values: members to assign (deep-merged). Presence is intent;
        absence is silence; None is a NULL VALUE, never a deletion.
    :param delete_paths: iterable of key paths to remove, e.g. [["recipe", "step_data"]].
        The only deletion channel there is.
    :param ops: ordered named operations, applied at drain time against live state.
    """
    envelope = {CONTROL_DELTA_KEY: CONTROL_DELTA_VERSION}
    if set_values:
        envelope["set"] = copy.deepcopy(dict(set_values))
    if delete_paths:
        envelope["delete"] = [list(path) for path in delete_paths]
    if ops:
        envelope["ops"] = [copy.deepcopy(dict(op)) for op in ops]
    validate_control_delta(envelope)
    return envelope


def is_control_delta(payload):
    """True when a queued payload is a delta envelope rather than a legacy partial."""
    return isinstance(payload, Mapping) and CONTROL_DELTA_KEY in payload


def validate_control_delta(envelope):
    """Raise ControlDeltaError unless `envelope` is a well-formed version-1 delta."""
    if not isinstance(envelope, Mapping):
        raise ControlDeltaError(f"delta must be a mapping, got {type(envelope).__name__}")
    unknown = sorted(set(envelope) - _ALLOWED_MEMBERS)
    if unknown:
        raise ControlDeltaError(f"unknown delta member(s): {', '.join(unknown)}")
    if envelope.get(CONTROL_DELTA_KEY) != CONTROL_DELTA_VERSION:
        raise ControlDeltaError(
            f"{CONTROL_DELTA_KEY} must be {CONTROL_DELTA_VERSION}, got {envelope.get(CONTROL_DELTA_KEY)!r}"
        )
    _validate_set(envelope.get("set"))
    _validate_delete(envelope.get("delete"))
    _validate_ops(envelope.get("ops"))


def _validate_set(set_values):
    if set_values is None:
        return
    if not isinstance(set_values, Mapping):
        raise ControlDeltaError(f"set must be a mapping, got {type(set_values).__name__}")
    forbidden = sorted(set(set_values) & _SET_FORBIDDEN)
    if forbidden:
        raise ControlDeltaError(
            f"set may not carry {', '.join(forbidden)}: use the matching timer.*/notify.* op, "
            f"which the drain evaluates against live state instead of against a stale read"
        )


def _validate_delete(delete_paths):
    if delete_paths is None:
        return
    if not isinstance(delete_paths, list):
        raise ControlDeltaError(f"delete must be a list, got {type(delete_paths).__name__}")
    for path in delete_paths:
        if not isinstance(path, list) or not path or not all(isinstance(k, str) for k in path):
            raise ControlDeltaError(f"delete path must be a non-empty list of strings, got {path!r}")


def _validate_ops(ops):
    if ops is None:
        return
    if not isinstance(ops, list):
        raise ControlDeltaError(f"ops must be a list, got {type(ops).__name__}")
    for op in ops:
        if not isinstance(op, Mapping) or "op" not in op:
            raise ControlDeltaError(f"each op must be a mapping with an 'op' key, got {op!r}")
        name = op["op"]
        if name not in _OP_FIELDS:
            raise ControlDeltaError(f"unknown op {name!r}; known: {', '.join(sorted(CONTROL_DELTA_OPS))}")
        missing = [f for f in _OP_FIELDS[name] if f not in op]
        if missing:
            raise ControlDeltaError(f"op {name!r} is missing field(s): {', '.join(missing)}")
        extra = sorted(set(op) - {"op"} - set(_OP_FIELDS[name]))
        if extra:
            raise ControlDeltaError(f"op {name!r} has unknown field(s): {', '.join(extra)}")
    _validate_op_types(ops)


def _validate_op_types(ops):
    for op in ops:
        name = op["op"]
        if name == "notify.set" and not isinstance(op["fields"], Mapping):
            raise ControlDeltaError("notify.set 'fields' must be a mapping")
        if name == "notify.replace" and not isinstance(op["entries"], list):
            raise ControlDeltaError("notify.replace 'entries' must be a list")
        if name == "timer.start_with_options" and not (
            isinstance(op["seconds"], int) and not isinstance(op["seconds"], bool) and op["seconds"] > 0
        ):
            raise ControlDeltaError("timer.start_with_options 'seconds' must be an int greater than zero")
```

**Step 4 — `write_control` accepts DELTA.** `common/datastore_accessors.py:70-85` becomes:

```python
def write_control(control, kind, origin="unknown"):
    """
    Write control to SQLite DB.

    :param control: for OVERWRITE/MERGE, a control dictionary or partial; for
                    DELTA, an envelope from common.control_delta.control_delta().
    :param kind: WriteKind.OVERWRITE writes control:general directly.
                 WriteKind.MERGE queues a partial for deep-merge on execute.
                 WriteKind.DELTA queues a validated intent envelope.
    :param origin: Source label recorded on queued writes.
    """
    if kind is WriteKind.OVERWRITE:
        _write_json_blob("control:general", control)
    elif kind is WriteKind.MERGE:
        control["origin"] = origin
        SqliteQueue("queue_control_write").push(control)
    elif kind is WriteKind.DELTA:
        # Validate HERE, in the writing process: a malformed envelope caught at
        # drain time surfaces as a control-loop log line in a different process.
        validate_control_delta(control)
        payload = dict(control)
        payload["origin"] = origin
        SqliteQueue("queue_control_write").push(payload)
    else:
        raise TypeError(f"write_control: kind must be WriteKind, got {kind!r}")
```

Note the asymmetry, and keep it: MERGE stamps `origin` **into the caller's dict** (existing,
observed behaviour — the golden records `"origin": ["<absent>", "app"]` in every `queued_writes`
entry); DELTA copies first, because the envelope is an immutable value.

**Gate:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_control_delta_envelope.py tests/unit/common/test_write_kind.py -q`
→ **14 passed**. Then the full gate → **2796 + 14 = 2810 passed**.
`uvx ruff format common/control_delta.py common/common.py common/datastore_accessors.py tests/unit/common/test_control_delta_envelope.py tests/unit/common/test_write_kind.py`.

---

### Task 2: `apply_control_delta` — `set` and `delete`

**Files:** modify `common/control_delta.py`; create `tests/unit/common/test_control_delta_apply.py`.

**Step 1 — the failing test:**

```python
"""apply_control_delta over the `set` and `delete` channels."""

import logging

from common.control_delta import CONTROL_DELTA_KEY, apply_control_delta, control_delta


def _control():
    return {
        "mode": "Stop",
        "updated": False,
        "primary_setpoint": 0,
        "manual": {"change": False, "pwm": 100},
        "recipe": {"filename": "", "step": 0, "step_data": {"hold_temp": 225}},
        "timer": {"start": 0, "paused": 0, "end": 0},
        "notify_data": [{"label": "Timer", "type": "timer", "req": False}],
    }


def test_set_assigns_top_level_members():
    control = _control()
    apply_control_delta(control, control_delta(set_values={"mode": "Hold", "primary_setpoint": 225}))
    assert control["mode"] == "Hold"
    assert control["primary_setpoint"] == 225


def test_set_deep_merges_a_nested_member_without_clobbering_siblings():
    control = _control()
    apply_control_delta(control, control_delta(set_values={"manual": {"pwm": 50}}))
    assert control["manual"] == {"change": False, "pwm": 50}


def test_an_absent_member_is_silence_not_a_deletion():
    control = _control()
    apply_control_delta(control, control_delta(set_values={"updated": True}))
    assert control["mode"] == "Stop"
    assert control["primary_setpoint"] == 0


def test_a_none_value_assigns_null_and_does_not_delete():
    """The asymmetry with the legacy path. json_patch (RFC 7386) DELETES on a
    null member, which is why strip_null_members exists. A delta applies in
    Python, so a null is just a value and deletion has its own channel."""
    control = _control()
    apply_control_delta(control, control_delta(set_values={"primary_setpoint": None}))
    assert "primary_setpoint" in control
    assert control["primary_setpoint"] is None


def test_delete_removes_a_nested_path():
    control = _control()
    apply_control_delta(control, control_delta(delete_paths=[["recipe", "step_data"]]))
    assert "step_data" not in control["recipe"]
    assert control["recipe"]["filename"] == ""


def test_delete_of_a_missing_path_is_a_no_op():
    control = _control()
    before = dict(control)
    apply_control_delta(control, control_delta(delete_paths=[["recipe", "never_existed"], ["nope"]]))
    assert control == before


def test_set_is_applied_before_delete():
    control = _control()
    apply_control_delta(
        control,
        control_delta(set_values={"recipe": {"step_data": {"hold_temp": 250}}}, delete_paths=[["recipe", "step_data"]]),
    )
    assert "step_data" not in control["recipe"]


def test_the_applier_does_not_alias_the_envelope():
    control = _control()
    envelope = control_delta(set_values={"manual": {"pwm": 50}})
    apply_control_delta(control, envelope)
    control["manual"]["pwm"] = 99
    assert envelope["set"]["manual"]["pwm"] == 50


def test_apply_control_delta_drops_an_unknown_version_and_logs(caplog):
    """Direction B' of the upgrade analysis: a FUTURE writer, THIS drain."""
    control = _control()
    envelope = {CONTROL_DELTA_KEY: 99, "set": {"mode": "Hold"}}
    with caplog.at_level(logging.ERROR, logger="control"):
        apply_control_delta(control, envelope)
    assert control["mode"] == "Stop", "a partially-understood delta must not be applied"
    assert "unsupported control delta version" in caplog.text
```

**Expected failure:** `ImportError: cannot import name 'apply_control_delta'`.

**Step 2 — implement.** Append to `common/control_delta.py`:

```python
def apply_control_delta(control, envelope, log=None):
    """Apply a delta envelope to `control` IN PLACE and return it.

    Order is `set` -> `ops` -> `delete`. `set` and `ops` have disjoint domains by
    construction (validation forbids `timer`/`notify_data` under `set`), so their
    relative order is not observable; `delete` runs last so a writer can assign
    and then remove within one envelope.

    An envelope whose version this build does not understand is DROPPED and
    logged at ERROR. It is never partially applied: the half we can parse is not
    evidence about the half we cannot. See the upgrade analysis in
    docs/superpowers/plans/2026-07-25-control-write-deltas.md.
    """
    log = log or logging.getLogger("control")
    version = envelope.get(CONTROL_DELTA_KEY)
    if version != CONTROL_DELTA_VERSION:
        log.error(
            "apply_control_delta: unsupported control delta version %r (this build understands %r); "
            "dropping the envelope from origin=%r. A newer PiFire queued this write.",
            version,
            CONTROL_DELTA_VERSION,
            envelope.get("origin"),
        )
        return control

    if "set" in envelope:
        _deep_assign(control, copy.deepcopy(envelope["set"]))
    for op in envelope.get("ops", ()):
        _apply_op(control, op, log)
    for path in envelope.get("delete", ()):
        _delete_path(control, path)
    return control


def _deep_assign(target, values):
    """deep_update without importing common.common (which imports this module's
    siblings). Mapping values recurse; everything else assigns."""
    for key, value in values.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), Mapping):
            _deep_assign(target[key], value)
        else:
            target[key] = value
    return target


def _delete_path(target, path):
    node = target
    for key in path[:-1]:
        node = node.get(key) if isinstance(node, Mapping) else None
        if not isinstance(node, Mapping):
            return
    if isinstance(node, Mapping):
        node.pop(path[-1], None)


def _apply_op(control, op, log):
    _OP_APPLIERS[op["op"]](control, op, log)
```

`_OP_APPLIERS` is populated in Tasks 3 and 4; for this task define it as `{}` and let
`_apply_op` be unreachable (no test here passes an op).

**Gate:** `... uv run pytest tests/unit/common/test_control_delta_apply.py -q` → **9 passed**.
Full gate → **2819 passed**. `uvx ruff format` on the two files.

---

### Task 3: The notify ops

**Files:** modify `common/control_delta.py`; extend `tests/unit/common/test_control_delta_apply.py`.

**Step 1 — the failing test.** Append:

```python
def _notify_control():
    return {
        "notify_data": [
            {"label": "Grill", "type": "probe", "req": False, "target": 0, "eta": None},
            {"label": "Grill", "type": "probe_limit_high", "req": False, "target": 0, "triggered": False},
            {"label": "Timer", "type": "timer", "req": False, "shutdown": False, "keep_warm": False},
        ]
    }


def _entry(control, label, type_):
    return next(e for e in control["notify_data"] if e["label"] == label and e["type"] == type_)


def test_notify_set_field_merges_the_addressed_entry_only():
    control = _notify_control()
    apply_control_delta(
        control,
        control_delta(
            ops=[{"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"target": 203, "req": True}}]
        ),
    )
    assert _entry(control, "Grill", "probe")["target"] == 203
    assert _entry(control, "Grill", "probe")["req"] is True
    assert _entry(control, "Grill", "probe")["eta"] is None, "untouched fields survive"
    assert _entry(control, "Grill", "probe_limit_high")["target"] == 0, "same label, different type"


def test_notify_set_appends_when_the_entry_does_not_exist():
    control = _notify_control()
    apply_control_delta(
        control,
        control_delta(ops=[{"op": "notify.set", "label": "Probe9", "type": "probe", "fields": {"target": 165}}]),
    )
    assert _entry(control, "Probe9", "probe") == {"label": "Probe9", "type": "probe", "target": 165}
    assert len(control["notify_data"]) == 4


def test_two_notify_sets_on_the_same_entry_both_land_when_they_touch_different_fields():
    """The residual-2 case for notify_data: neither is inferred, so neither is dropped."""
    control = _notify_control()
    apply_control_delta(
        control, control_delta(ops=[{"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"target": 203}}])
    )
    apply_control_delta(
        control, control_delta(ops=[{"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"req": True}}])
    )
    assert _entry(control, "Grill", "probe")["target"] == 203
    assert _entry(control, "Grill", "probe")["req"] is True


def test_a_notify_set_back_to_the_starting_value_still_lands():
    """Under reduce_control_patch this write was indistinguishable from silence."""
    control = _notify_control()
    apply_control_delta(
        control, control_delta(ops=[{"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"target": 203}}])
    )
    apply_control_delta(
        control, control_delta(ops=[{"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"target": 0}}])
    )
    assert _entry(control, "Grill", "probe")["target"] == 0


def test_notify_delete_removes_exactly_one_entry():
    control = _notify_control()
    apply_control_delta(control, control_delta(ops=[{"op": "notify.delete", "label": "Grill", "type": "probe"}]))
    assert [(e["label"], e["type"]) for e in control["notify_data"]] == [
        ("Grill", "probe_limit_high"),
        ("Timer", "timer"),
    ]


def test_notify_replace_swaps_the_whole_array():
    control = _notify_control()
    fresh = [{"label": "Only", "type": "probe", "req": True}]
    apply_control_delta(control, control_delta(ops=[{"op": "notify.replace", "entries": fresh}]))
    assert control["notify_data"] == fresh
    fresh[0]["req"] = False
    assert control["notify_data"][0]["req"] is True, "replace deep-copies"


def test_notify_replace_then_set_composes_in_order():
    control = _notify_control()
    apply_control_delta(
        control,
        control_delta(
            ops=[
                {"op": "notify.replace", "entries": [{"label": "Only", "type": "probe", "req": False}]},
                {"op": "notify.set", "label": "Only", "type": "probe", "fields": {"req": True}},
            ]
        ),
    )
    assert control["notify_data"] == [{"label": "Only", "type": "probe", "req": True}]
```

**Expected failure:** `KeyError: 'notify.set'` from `_OP_APPLIERS`.

**Step 2 — implement.** Append to `common/control_delta.py`:

```python
def _notify_index(control, label, type_):
    for index, entry in enumerate(control.get("notify_data", ())):
        if isinstance(entry, Mapping) and entry.get("label") == label and entry.get("type") == type_:
            return index
    return None


def _op_notify_set(control, op, log):
    index = _notify_index(control, op["label"], op["type"])
    if index is None:
        control.setdefault("notify_data", []).append(
            {"label": op["label"], "type": op["type"], **copy.deepcopy(dict(op["fields"]))}
        )
        return
    control["notify_data"][index].update(copy.deepcopy(dict(op["fields"])))


def _op_notify_delete(control, op, log):
    index = _notify_index(control, op["label"], op["type"])
    if index is not None:
        del control["notify_data"][index]


def _op_notify_replace(control, op, log):
    control["notify_data"] = copy.deepcopy(list(op["entries"]))
```

and register them in `_OP_APPLIERS`.

**Gate:** `... uv run pytest tests/unit/common/test_control_delta_apply.py -q` → **16 passed**.
Full gate → **2826 passed**.

---

### Task 4: The timer ops — the half that closes residual 1

**Files:** modify `common/control_delta.py`; create
`tests/unit/common/test_control_delta_timer_ops.py`.

**Step 1 — the failing test:**

```python
"""The four timer ops.

Each op reproduces one branch of common/api_commands.py::_cmd_set_timer, with
one difference that is the entire point: the BRANCH is chosen at drain time from
live state, while the CLOCK travels in the op as `at`. So a stop followed by a
pause inside one control cycle pauses a timer that is already cleared -- which is
`_cmd_set_timer`'s own start == 0 branch, i.e. a no-op -- instead of resurrecting
a countdown from a pre-stop read.
"""

import logging

from common.control_delta import apply_control_delta, control_delta

NOW = 1_700_000_000.0


def _running():
    return {
        "timer": {"start": 1000.0, "paused": 0, "end": 2000.0},
        "notify_data": [{"label": "Timer", "type": "timer", "req": True, "shutdown": True, "keep_warm": False}],
    }


def _paused():
    control = _running()
    control["timer"]["paused"] = 1500.0
    return control


def _stopped():
    return {
        "timer": {"start": 0, "paused": 0, "end": 0},
        "notify_data": [{"label": "Timer", "type": "timer", "req": False, "shutdown": False, "keep_warm": False}],
    }


def _timer_entry(control):
    return next(e for e in control["notify_data"] if e["type"] == "timer")


def test_clear_zeroes_the_countdown_and_disarms_both_expiry_flags():
    control = _running()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.clear"}]))
    assert control["timer"] == {"start": 0, "paused": 0, "end": 0}
    assert _timer_entry(control) == {
        "label": "Timer",
        "type": "timer",
        "req": False,
        "shutdown": False,
        "keep_warm": False,
    }


def test_pause_on_a_running_timer_stamps_paused_from_the_requests_clock():
    control = _running()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.pause", "at": NOW}]))
    assert control["timer"] == {"start": 1000.0, "paused": NOW, "end": 2000.0}
    assert _timer_entry(control)["req"] is False


def test_pause_on_a_stopped_timer_clears():
    """_cmd_set_timer's start == 0 branch (common/api_commands.py:685-693)."""
    control = _stopped()
    control["timer"]["end"] = 5.0
    apply_control_delta(control, control_delta(ops=[{"op": "timer.pause", "at": NOW}]))
    assert control["timer"] == {"start": 0, "paused": 0, "end": 0}


def test_start_or_resume_on_a_stopped_timer_arms_seconds_from_at():
    control = _stopped()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.start_or_resume", "at": NOW, "seconds": 300}]))
    assert control["timer"] == {"start": NOW, "paused": 0, "end": NOW + 300}
    assert _timer_entry(control)["req"] is True


def test_start_or_resume_substitutes_sixty_seconds_for_a_null_duration():
    """The bare form's is_float() fallback (common/api_commands.py:672)."""
    control = _stopped()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.start_or_resume", "at": NOW, "seconds": None}]))
    assert control["timer"]["end"] == NOW + 60


def test_start_or_resume_on_a_paused_timer_shifts_the_end_and_unpauses():
    control = _paused()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.start_or_resume", "at": NOW, "seconds": 500}]))
    assert control["timer"] == {"start": 1000.0, "paused": 0, "end": 2000.0 - 1500.0 + NOW}


def test_start_with_options_arms_the_countdown_and_both_flags():
    control = _stopped()
    apply_control_delta(
        control,
        control_delta(
            ops=[{"op": "timer.start_with_options", "at": NOW, "seconds": 600, "shutdown": True, "keep_warm": False}]
        ),
    )
    assert control["timer"] == {"start": NOW, "paused": 0, "end": NOW + 600}
    entry = _timer_entry(control)
    assert (entry["req"], entry["shutdown"], entry["keep_warm"]) == (True, True, False)


def test_start_with_options_drops_and_logs_when_the_timer_became_paused(caplog):
    """Request time already rejected a paused timer (common/api_commands.py:620-623).
    Reaching the drain paused means another writer paused it in the same cycle."""
    control = _paused()
    with caplog.at_level(logging.ERROR, logger="control"):
        apply_control_delta(
            control,
            control_delta(
                ops=[
                    {"op": "timer.start_with_options", "at": NOW, "seconds": 600, "shutdown": True, "keep_warm": False}
                ]
            ),
        )
    assert control["timer"] == {"start": 1000.0, "paused": 1500.0, "end": 2000.0}
    assert "timer.start_with_options" in caplog.text


# --- the two resurrections, at the op level --------------------------------


def test_clear_then_pause_leaves_the_timer_stopped():
    """web-react TimerBar's Stop-then-Pause pair. Pinned as resurrecting at
    tests/characterization/test_process_command_golden.py::
    test_a_pause_after_a_stop_in_one_cycle_resurrects_the_timer."""
    control = _running()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.clear"}, {"op": "timer.pause", "at": NOW}]))
    assert control["timer"] == {"start": 0, "paused": 0, "end": 0}
    assert _timer_entry(control)["shutdown"] is False


def test_clear_then_start_or_resume_arms_a_fresh_timer_rather_than_the_old_one():
    """Stop-then-Resume. The old end time (2000.0) must NOT come back; what the
    user gets is what they would get one control cycle apart -- the resume sees
    paused == 0 and arms a fresh countdown."""
    control = _paused()
    apply_control_delta(
        control,
        control_delta(ops=[{"op": "timer.clear"}, {"op": "timer.start_or_resume", "at": NOW, "seconds": 500}]),
    )
    assert control["timer"] == {"start": NOW, "paused": 0, "end": NOW + 500}


def test_start_or_resume_then_clear_leaves_the_timer_stopped():
    """Residual 2: a `stop` against an already-zero ancestor carried no evidence
    of intent, so start + stop in one cycle left the timer RUNNING."""
    control = _stopped()
    apply_control_delta(
        control,
        control_delta(ops=[{"op": "timer.start_or_resume", "at": NOW, "seconds": 600}, {"op": "timer.clear"}]),
    )
    assert control["timer"] == {"start": 0, "paused": 0, "end": 0}
```

**Expected failure:** `KeyError: 'timer.clear'` from `_OP_APPLIERS`.

**Step 2 — implement.** Append to `common/control_delta.py`:

```python
def _op_timer_clear(control, op, log):
    control["timer"]["start"] = 0
    control["timer"]["end"] = 0
    control["timer"]["paused"] = 0
    index = _timer_notify_index(control)
    if index is not None:
        entry = control["notify_data"][index]
        entry["req"] = False
        entry["shutdown"] = False
        entry["keep_warm"] = False


def _timer_notify_index(control):
    """_cmd_set_timer locates the timer entry by TYPE alone
    (common/api_commands.py:649-651), not by label. Match that."""
    for index, entry in enumerate(control.get("notify_data", ())):
        if isinstance(entry, Mapping) and entry.get("type") == "timer":
            return index
    return None


def _op_timer_pause(control, op, log):
    if control["timer"]["start"] == 0:
        # _cmd_set_timer's own start == 0 branch is a full clear, not a pause.
        _op_timer_clear(control, op, log)
        return
    index = _timer_notify_index(control)
    if index is not None:
        control["notify_data"][index]["req"] = False
    control["timer"]["paused"] = op["at"]


def _op_timer_start_or_resume(control, op, log):
    index = _timer_notify_index(control)
    if index is not None:
        # Set BEFORE the branch, matching common/api_commands.py:665.
        control["notify_data"][index]["req"] = True
    if control["timer"]["paused"] == 0:
        seconds = op["seconds"] if op["seconds"] is not None else 60
        control["timer"]["start"] = op["at"]
        control["timer"]["end"] = op["at"] + seconds
    else:
        control["timer"]["end"] = (control["timer"]["end"] - control["timer"]["paused"]) + op["at"]
        control["timer"]["paused"] = 0


def _op_timer_start_with_options(control, op, log):
    if control["timer"]["paused"] != 0:
        log.error(
            "apply_control_delta: dropping timer.start_with_options -- the timer is paused at drain time. "
            "The 4-argument REST form rejects a paused timer at request time, so another writer paused it "
            "inside this control cycle. Resume or stop it first."
        )
        return
    index = _timer_notify_index(control)
    if index is not None:
        entry = control["notify_data"][index]
        entry["req"] = True
        entry["shutdown"] = op["shutdown"]
        entry["keep_warm"] = op["keep_warm"]
    control["timer"]["start"] = op["at"]
    control["timer"]["end"] = op["at"] + op["seconds"]
```

**Gate:** `... uv run pytest tests/unit/common/test_control_delta_timer_ops.py -q` → **11 passed**.
Full gate → **2837 passed**.

---

### Task 5: Wire both drains, and pin the cross-process seam

**Files:** modify `common/datastore_accessors.py`, `controller/runtime/store.py`; create
`tests/characterization/test_control_delta_seam.py`; extend
`tests/unit/datastore/test_sqlite_store_parity.py`.

**Step 1 — the failing test.** Create `tests/characterization/test_control_delta_seam.py`:

```python
"""The drain must handle a queue that mixes legacy whole-dict patches and delta
envelopes, in push order, for the whole migration."""

import json

import pytest

from common import common as c
from common import datastore_accessors as dsa
from common.common import WriteKind
from common.control_delta import CONTROL_DELTA_KEY, control_delta
from common.datastore_accessors import default_control, read_control, write_control, write_settings_store
from common.defaults import default_settings

NOW = 1_700_000_000.0


@pytest.fixture
def seeded(ds):
    write_settings_store(default_settings())
    write_control(default_control(), WriteKind.OVERWRITE, origin="test-delta-seam")
    c.SqliteQueue("queue_control_write").flush()
    return ds


def test_a_delta_is_queued_verbatim_with_an_origin_stamp(seeded):
    write_control(control_delta(set_values={"mode": "Hold"}), WriteKind.DELTA, origin="app")
    rows = c.datastore.connection().execute("SELECT value FROM queue_control_write ORDER BY id").fetchall()
    assert json.loads(rows[0][0]) == {CONTROL_DELTA_KEY: 1, "set": {"mode": "Hold"}, "origin": "app"}


def test_a_delta_write_lands_on_the_blob(seeded):
    write_control(control_delta(set_values={"mode": "Hold", "primary_setpoint": 225}), WriteKind.DELTA, origin="app")
    dsa.execute_control_writes()
    control = read_control()
    assert control["mode"] == "Hold"
    assert control["primary_setpoint"] == 225


def test_a_legacy_whole_dict_write_is_unaffected_by_the_delta_branch(seeded):
    control = read_control()
    control["mode"] = "Startup"
    write_control(control, WriteKind.MERGE, origin="legacy")
    dsa.execute_control_writes()
    assert read_control()["mode"] == "Startup"


def test_a_delta_and_a_legacy_patch_in_one_cycle_both_land_in_push_order(seeded):
    write_control(control_delta(set_values={"primary_setpoint": 225}), WriteKind.DELTA, origin="delta")
    stale = read_control()
    stale["s_plus"] = True
    write_control(stale, WriteKind.MERGE, origin="legacy")
    assert c.SqliteQueue("queue_control_write").length() == 2
    dsa.execute_control_writes()
    control = read_control()
    assert control["primary_setpoint"] == 225, "the legacy patch's stale copy must not revert the delta"
    assert control["s_plus"] is True


def test_a_legacy_patch_queued_first_does_not_stop_a_later_delta(seeded):
    stale = read_control()
    stale["s_plus"] = True
    write_control(stale, WriteKind.MERGE, origin="legacy")
    write_control(control_delta(set_values={"primary_setpoint": 225}), WriteKind.DELTA, origin="delta")
    dsa.execute_control_writes()
    control = read_control()
    assert control["s_plus"] is True
    assert control["primary_setpoint"] == 225


def test_two_deltas_restoring_the_opening_value_are_not_confused_with_silence(seeded):
    """Residual 2 at the seam."""
    opening = read_control()["primary_setpoint"]
    write_control(control_delta(set_values={"primary_setpoint": 225}), WriteKind.DELTA, origin="a")
    write_control(control_delta(set_values={"primary_setpoint": opening}), WriteKind.DELTA, origin="b")
    dsa.execute_control_writes()
    assert read_control()["primary_setpoint"] == opening


def test_a_delta_on_a_fresh_store_is_not_silently_dropped(ds):
    """Mirrors the seed guard at common/datastore_accessors.py:120-121."""
    write_settings_store(default_settings())
    c.datastore.delete_blob("control:general")
    write_control(control_delta(set_values={"mode": "Hold"}), WriteKind.DELTA, origin="app")
    dsa.execute_control_writes()
    assert read_control()["mode"] == "Hold"


def test_a_future_version_envelope_is_dropped_rather_than_applied(seeded, caplog):
    c.SqliteQueue("queue_control_write").push({CONTROL_DELTA_KEY: 99, "set": {"mode": "Hold"}, "origin": "future"})
    dsa.execute_control_writes()
    assert read_control()["mode"] != "Hold"
```

**Expected failure:** `test_a_delta_write_lands_on_the_blob` fails with the envelope's keys landing
in `control:general` — `read_control()["mode"]` is still `"Stop"` and
`read_control()["__control_delta__"] == 1` — because the untouched drain treats the envelope as a
legacy partial and `json_patch`es it. That is precisely Direction B of the upgrade analysis,
observed as a test failure.

**Step 2 — the SQLite drain.** In `common/datastore_accessors.py::execute_control_writes`, after
`origin = command.pop("origin", None)` (`:130`) insert:

```python
        if is_control_delta(command):
            # A delta states intent, so nothing is inferred and nothing is reduced.
            # Ops branch on LIVE state, so this is a read-modify-write rather than
            # a json_patch: read what earlier patches in this batch already left.
            control = read_control()
            apply_control_delta(control, command)
            _write_json_blob("control:general", control)
            continue
```

and extend the docstring:

```
    A queued payload carrying ``__control_delta__`` is a DELTA envelope
    (common/control_delta.py): the writer stated what it meant, so it is applied
    directly and never reduced. Everything else is a legacy whole-dict partial and
    takes the three-way-merge path below, unchanged, for as long as any writer
    still sends one. ``base`` stays the pre-drain ancestor for those patches even
    when a delta has landed in between -- it is what THEY read.
```

Import `apply_control_delta, is_control_delta` from `common.control_delta` at `:22-28`.

**Step 3 — the in-memory drain.** `controller/runtime/store.py:162-180`, same branch, using
`self._control` directly:

```python
            if is_control_delta(partial):
                apply_control_delta(self._control, partial)
                continue
```

placed immediately after `partial.pop("origin", None)`.

**Step 4 — the parity pin.** Append to `tests/unit/datastore/test_sqlite_store_parity.py`, beside
`test_whole_dict_cross_writer_merge_parity`:

```python
def test_delta_envelope_parity_between_sqlite_and_in_memory(ds):
    """The web process queues, the control process drains. Pin both ends."""
    envelope = control_delta(
        set_values={"mode": "Hold", "primary_setpoint": 225},
        ops=[{"op": "timer.clear"}, {"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"target": 203}}],
    )
    dsa.write_settings_store(default_settings())
    dsa.write_control(default_control(), WriteKind.OVERWRITE, origin="parity")
    c.SqliteQueue("queue_control_write").flush()
    dsa.write_control(envelope, WriteKind.DELTA, origin="parity")
    dsa.execute_control_writes()
    sqlite_control = dsa.read_control()

    store = InMemoryStore(control=default_control(), settings=default_settings())
    store.write_control(envelope, WriteKind.DELTA, origin="parity")
    store.execute_control_writes()

    assert store.read_control() == sqlite_control
```

**Gate:** `... uv run pytest tests/characterization/test_control_delta_seam.py tests/unit/datastore/test_sqlite_store_parity.py -q`
→ **8 + (existing + 1) passed**. Full gate → **2846 passed**.

---

### Task 6: `_cmd_set_timer` and `_timer_start_with_options` emit ops

**Files:** modify `common/api_commands.py`.

**Step 1 — the failing test.** Append to `tests/characterization/test_control_delta_seam.py`:

```python
def _cmd(*args, origin="test"):
    from unittest import mock
    from common import api_commands

    with mock.patch.object(api_commands, "write_log"), mock.patch.object(c.time, "time", return_value=NOW):
        return api_commands.process_command(action="set", arglist=list(args), origin=origin)


def test_stop_then_pause_in_one_cycle_leaves_the_timer_stopped(seeded):
    control = read_control()
    control["timer"] = {"start": 1000.0, "paused": 0, "end": 2000.0}
    write_control(control, WriteKind.OVERWRITE, origin="seed")
    c.SqliteQueue("queue_control_write").flush()

    assert _cmd("timer", "stop")["result"] == "OK"
    assert _cmd("timer", "pause")["result"] == "OK"
    dsa.execute_control_writes()

    assert read_control()["timer"] == {"start": 0, "paused": 0, "end": 0}


def test_stop_then_resume_in_one_cycle_does_not_bring_back_the_old_end_time(seeded):
    control = read_control()
    control["timer"] = {"start": 1000.0, "paused": 1500.0, "end": 2000.0}
    write_control(control, WriteKind.OVERWRITE, origin="seed")
    c.SqliteQueue("queue_control_write").flush()

    assert _cmd("timer", "stop")["result"] == "OK"
    assert _cmd("timer", "start", "500")["result"] == "OK"
    dsa.execute_control_writes()

    assert read_control()["timer"] == {"start": NOW, "paused": 0, "end": NOW + 500}


def test_start_then_stop_in_one_cycle_leaves_the_timer_stopped(seeded):
    """Residual 2, through the real commands."""
    assert _cmd("timer", "start", "600")["result"] == "OK"
    assert _cmd("timer", "stop")["result"] == "OK"
    dsa.execute_control_writes()

    assert read_control()["timer"] == {"start": 0, "paused": 0, "end": 0}
```

**Expected failure:**
`test_stop_then_pause...` → `AssertionError: assert {'start': 1000.0, 'paused': 1700000000.0, 'end': 2000.0} == {'start': 0, 'paused': 0, 'end': 0}`.
`test_stop_then_resume...` → `assert {'start': 1000.0, 'paused': 0, 'end': 1700000500.0} == {'start': 1700000000.0, ...}`.
`test_start_then_stop...` → `assert {'start': 1700000000.0, 'paused': 0, 'end': 1700000600.0} == {'start': 0, ...}`.

**Step 2 — rewrite the branches.** `common/api_commands.py:633-704`. Every branch keeps its
request-time `write_log` (see the logging decision below) and replaces its `write_control(control,
kind, origin="app")` with a DELTA write:

```python
if arglist[1] == "start" and arglist[3] is not None:
    _timer_start_with_options(data, control, arglist, index, now, kind)
elif arglist[1] == "start":
    seconds = int(float(arglist[2])) if is_float(arglist[2]) else None
    # The BRANCH is not decided here. `start` is also the unpause command and
    # which one it is depends on control["timer"]["paused"] -- a value this
    # read_control() cannot see the queue behind. The drain decides, against
    # live state; the clock still comes from here, as `at`.
    if control["timer"]["paused"] == 0:
        write_log("Timer started.  Ends at: " + epoch_to_time(now + (seconds if seconds is not None else 60)))
    else:
        write_log(
            "Timer unpaused.  Ends at: " + epoch_to_time((control["timer"]["end"] - control["timer"]["paused"]) + now)
        )
    write_control(
        control_delta(ops=[{"op": "timer.start_or_resume", "at": now, "seconds": seconds}]),
        WriteKind.DELTA,
        origin="app",
    )
elif arglist[1] == "pause":
    if control["timer"]["start"] != 0:
        write_log("Timer paused.")
    else:
        write_log("Timer cleared.")
    write_control(control_delta(ops=[{"op": "timer.pause", "at": now}]), WriteKind.DELTA, origin="app")
elif arglist[1] == "stop":
    write_log("Timer stopped.")
    write_control(control_delta(ops=[{"op": "timer.clear"}]), WriteKind.DELTA, origin="app")
```

and in `_timer_start_with_options` (`:625-632`), after the three rejections:

```python
    write_log("Timer started.  Ends at: " + epoch_to_time(now + seconds))
    write_control(
        control_delta(
            ops=[
                {
                    "op": "timer.start_with_options",
                    "at": now,
                    "seconds": seconds,
                    "shutdown": options["shutdown"],
                    "keep_warm": options["keep_warm"],
                }
            ]
        ),
        WriteKind.DELTA,
        origin="app",
    )
```

The `index` local and the `control["notify_data"][index][...]` assignments in these branches are
**deleted** — the ops own those fields now. `index` is still needed by the `shutdown` /
`keep_warm` branches (`:705-716`), which stay on MERGE until Task 11.

**The logging decision, stated in the code.** Add above `_cmd_set_timer`:

```python
# NOTE: the log line is still computed HERE, from this request's (possibly
# stale) read, while the STATE change is computed in the drain from live state.
# They can disagree: two timer commands in one control cycle can log "Timer
# unpaused" and then correctly take the start branch. That is deliberate --
# moving the logging into the drain would move it into a different PROCESS and
# flip `log_calls` in six golden entries, for a diagnostic line. The drain logs
# the op it actually applied at DEBUG (common/control_delta.py).
```

**Step 3 — the drain-side DEBUG line.** In `common/control_delta.py::_apply_op`:

```python
def _apply_op(control, op, log):
    log.debug("apply_control_delta: applying %s", op["op"])
    _OP_APPLIERS[op["op"]](control, op, log)
```

**Gate:** the three new tests pass. **At least eight other tests now fail, deliberately** — the six
golden cases named in Verified facts (`test_process_command_matches_golden[set_timer_*]`), plus
`test_a_pause_after_a_stop_in_one_cycle_resurrects_the_timer` and
`test_a_resume_after_a_stop_in_one_cycle_resurrects_the_timer`. That is Task 7's and Task 8's work.
Do **not** try to make the full gate green here; run
`... uv run pytest tests/characterization/test_control_delta_seam.py -q` → **11 passed**, then
`... uv run pytest tests/characterization/test_process_command_golden.py -q` and record the exact
failing node IDs for Task 8.

---

### Task 7: The socket timer writer and the two arbitrary-patch doors

**Files:** modify `blueprints/mobile/socket_io.py`, `blueprints/api/routes.py`.

**Step 1 — the failing test.** Append to `tests/characterization/test_control_delta_seam.py`:

```python
def test_post_control_rejects_a_timer_value(flask_app):
    """control["timer"] is a coupled value object. A client that posts one is
    computing a timer state from a read it cannot trust; make it use the REST
    timer grammar, which is now an op."""
    client = flask_app.test_client()
    resp = client.post("/api/control", json={"timer": {"start": 0, "paused": 0, "end": 0}})
    assert resp.status_code == 400
    assert "timer" in resp.get_json()["message"]


def test_post_control_still_accepts_ordinary_members(flask_app):
    client = flask_app.test_client()
    assert client.post("/api/control", json={"mode": "Startup", "s_plus": True}).status_code == 201
    dsa.execute_control_writes()
    control = read_control()
    assert control["mode"] == "Startup"
    assert control["s_plus"] is True


def test_post_control_routes_notify_data_through_the_replace_op(flask_app, seeded):
    """saveTargetEdit's shape. An omitted entry still means DELETE -- now by name."""
    client = flask_app.test_client()
    entries = [{"label": "Only", "type": "probe", "req": True, "target": 165}]
    assert client.post("/api/control", json={"notify_data": entries}).status_code == 201
    dsa.execute_control_writes()
    assert read_control()["notify_data"] == entries


def test_socket_timer_stop_then_pause_leaves_the_timer_stopped(sio, seeded):
    control = read_control()
    control["timer"] = {"start": 1000.0, "paused": 0, "end": 2000.0}
    write_control(control, WriteKind.OVERWRITE, origin="seed")
    c.SqliteQueue("queue_control_write").flush()

    sio.mod._post_app_data("timer", "stop_timer", {})
    sio.mod._post_app_data("timer", "pause_timer", {})
    dsa.execute_control_writes()

    assert read_control()["timer"] == {"start": 0, "paused": 0, "end": 0}
```

Reuse the `flask_app` fixture from `tests/web/conftest.py` and the `sio` fixture pattern from
`tests/web/test_socketio_app_data.py:121-123` (which already patches
`restart_control`/`restart_webapp`/`restart_scripts`). **These are plain Flask/function-level
tests, not `[chromium]`.**

**Expected failure:** the `POST /api/control` timer test returns **201** (the route accepts any
JSON, `blueprints/api/routes.py:204-213`); the socket test asserts
`{'start': 1000.0, 'paused': 1700000000.0, 'end': 2000.0} == {'start': 0, ...}`.

**Step 2 — `_api_post_control`.** Replace `blueprints/api/routes.py:204-213`:

```python
def _api_post_control(settings, request_json):
    """Queue a client-supplied control patch as a delta.

    A posted patch is ALREADY a statement of intent -- the client sent only what
    it means -- so it needs no reduction and no client change; it is wrapped, not
    rewritten. Two members are special:

      * `notify_data` travels WHOLE (an omitted entry is a deletion, not silence
        -- web-react/src/helpers/notify/notifyApi.ts:15-17), so it becomes an
        explicit notify.replace op rather than an implicit array swap;
      * `timer` is refused. start/paused/end are one countdown and the control
        code branches on their combinations, so a value computed from a read that
        cannot see the write queue is exactly the race this endpoint used to feed.
        Use /api/set/timer/{start,pause,stop}, which queue ops the drain resolves
        against live state.
    """
    if "timer" in request_json:
        return jsonify(
            {
                "control": "error",
                "result": "error",
                "message": "control['timer'] cannot be set through /api/control; use /api/set/timer/...",
            }
        ), 400
    try:
        entries = request_json.pop("notify_data", None)
        ops = [{"op": "notify.replace", "entries": entries}] if entries is not None else None
        write_control(control_delta(set_values=request_json, ops=ops), WriteKind.DELTA, origin="app")
        return jsonify({"control": "success", "result": "success", "message": "Settings updated successfully."}), 201
    except Exception:
        return jsonify({"control": "error", "result": "error", "message": "Settings update failed."}), 201
```

**Step 3 — the socket `control` pass-through** (`blueprints/mobile/socket_io.py:432-441`): same
treatment — refuse `timer`, route `notify_data` through `notify.replace`, wrap the rest in `set`.
Keep the existing "key not found in control" rejection so the response contract is unchanged.

**Step 4 — `_post_app_data_timer`** (`blueprints/mobile/socket_io.py:690-745`): replace the four
branches with the same four ops `_cmd_set_timer` now emits. The `start_timer` branch with
`hours_range`/`minutes_range` becomes
`{"op": "timer.start_with_options", "at": now, "seconds": seconds, "shutdown": ..., "keep_warm": ...}`
— it already carries both flags in one write, so it maps exactly.

**Gate:** `... uv run pytest tests/characterization/test_control_delta_seam.py tests/web/test_socketio_app_data.py -q`.
Still red on the golden — Task 8.

---

### Task 8: Flip the golden fixture and the two resurrection pins

**Files:** modify `tests/characterization/test_process_command_golden.py`,
`tests/characterization/fixtures/process_command_golden.json`,
`tests/characterization/test_control_writes_cross_writer.py`, `common/api_commands.py` (comments),
`web-react/src/helpers/command.ts` (comments — no logic).

**This task is the justification, not the fix.** Nothing here changes behaviour; it records that
Tasks 6-7 did.

**Step 1 — teach the harness to record a delta verbatim.** `_diff(pre_control, envelope)` is
meaningless for an envelope: `pre_control` has ~40 members and the envelope has three, so every
control key would be recorded as `[value, "<absent>"]` — noise, and *larger* than what it replaces.
At `tests/characterization/test_process_command_golden.py:799`:

```python
    queued_writes = [
        {"origin": q.get("origin", "<absent>"), "delta": _normalize({k: v for k, v in q.items() if k != "origin"})}
        if is_control_delta(q)
        else {"origin": q.get("origin", "<absent>"), "diff": _diff(pre_control, q)}
        for q in queued
    ]
```

Document it above `_run_case`: *a delta is recorded as-is because the envelope IS the observable;
a legacy partial is still diffed against `pre_control` because a whole snapshot is not.*

**Step 2 — hand-edit the six entries.** Only `queued_writes` changes; `return`, `arglist_after`,
`log_calls`, `settings_diff`, `systemq`, `cmd_calls`, `sleeps` and — critically —
**`control_diff_after_execute` must be byte-identical**. That is the proof the conversion is
behaviour-preserving for a lone writer. New values:

| entry | new `queued_writes` |
|---|---|
| `set_timer_start` | `[{"delta": {"__control_delta__": 1, "ops": [{"at": 1700000000.0, "op": "timer.start_or_resume", "seconds": 300}]}, "origin": "app"}]` |
| `set_timer_start_default_60` | same with `"seconds": null` |
| `set_timer_start_resume` | same with `"seconds": 500` (check the case's `arglist`) |
| `set_timer_stop` | `[{"delta": {"__control_delta__": 1, "ops": [{"op": "timer.clear"}]}, "origin": "app"}]` |
| `set_timer_pause_running` | `[{"delta": {"__control_delta__": 1, "ops": [{"at": 1700000000.0, "op": "timer.pause"}]}, "origin": "app"}]` |
| `set_timer_pause_not_started` | identical to `set_timer_pause_running` — **one op now serves both branches, which is the point** |

Rewrite the file with exactly `json.dumps(golden, indent=2, sort_keys=True) + "\n"` (verified: this
recipe reproduces the current file byte-for-byte). Then compute and hand-edit `GOLDEN_SHA256` at
`:130`:

```
python3 -c "import hashlib;print(hashlib.sha256(open('tests/characterization/fixtures/process_command_golden.json','rb').read()).hexdigest())"
```

Add a numbered entry to the module docstring's sanctioned-exception list (`:24-26` names the
existing precedent) recording **what changed, why, and that `control_diff_after_execute` did not.**

**Step 3 — flip the two resurrection pins.** In
`tests/characterization/test_process_command_golden.py`:

- `test_a_pause_after_a_stop_in_one_cycle_resurrects_the_timer` (`:1375`) →
  `test_a_pause_after_a_stop_in_one_cycle_leaves_the_timer_stopped`. `:1403` becomes
  `assert after["timer"] == {"start": 0, "paused": 0, "end": 0}`.
- `test_a_resume_after_a_stop_in_one_cycle_resurrects_the_timer` (`:1423`) →
  `..._arms_a_fresh_timer`. `:1450` becomes
  `assert after["timer"] == {"start": FIXED_NOW, "paused": 0, "end": FIXED_NOW + 500}` — which is
  **exactly what the test's own second half already asserts for the same two commands one cycle
  apart** (`:1454-1463`). Say so in the docstring: the undrained and drained results now agree,
  which is the invariant this work exists for.
- Rewrite the section header at `:1319-1327`, which currently says the countdown still comes back
  and that the `TimerBar` guard stays.

**Step 4 — comments.** `common/api_commands.py`'s `_timer_start_with_options` docstring
(`:576-601`) still says the one-write form is "kept". Keep the endpoint; correct the text to name
only the two surviving reasons (server clock; input rejections) and state that the seam reason is
now gone at both ends. Same for the `NOTE` block in `web-react/src/helpers/command.ts:63-72` and
the "Arming a timer" block at `:122-146` — **comments only, no logic, no gate change**.

**Step 5 — the cross-writer suite.** `tests/characterization/test_control_writes_cross_writer.py`
has a test named `test_a_reset_to_the_ancestor_value_cannot_be_distinguished_from_silence` pinning
residual 2 through `_cmd_set_timer`. If Task 6 flipped it, rename it to
`..._is_now_distinguishable_because_the_writer_states_it` and assert the fixed value; if it uses a
non-timer writer it stays red until Task 12 and must be left alone with a `# still true for legacy
whole-dict writers` comment.

**Gate:** full gate → **2846 + 3 (Task 6) + 4 (Task 7) = 2853 passed**, everything green.
`uvx ruff format` on all changed `.py`. For web-react, comments only: `bun run lint` and
`bun run typecheck` must still be clean; no `bun run test` count change.

---

### Task 9: Delete `CONTROL_COUPLED_MEMBERS`

**Files:** modify `common/common.py`, `common/datastore_accessors.py`,
`controller/runtime/store.py`, `tests/unit/common/test_reduce_control_patch.py`.

**Precondition, verify before starting:** `grep -rn 'control\["timer"\]' --include="*.py" .`
outside `tests/`, `controller/` and `notify/` must return **zero** assignment sites. Tasks 6 and 7
removed the last ones; Task 7's rejections closed the two client doors. If anything remains,
**stop** — the exclusion is still load-bearing.

**Step 1 — the failing test.** Add to `tests/unit/common/test_reduce_control_patch.py`:

```python
def test_reduce_control_patch_no_longer_special_cases_timer():
    """CONTROL_COUPLED_MEMBERS existed because two writers could each compute a
    whole timer state from a stale read. No writer can any more: the REST and
    socket timer paths emit ops (common/control_delta.py) and both arbitrary-patch
    doors refuse a `timer` value. A legacy patch that still carries one is a
    stale snapshot, and reducing it member-wise is now strictly better than
    imposing it whole."""
    base = {"timer": {"start": 1000.0, "paused": 0, "end": 2000.0}}
    patch = {"timer": {"start": 1000.0, "paused": 1700000000.0, "end": 2000.0}}
    assert c.reduce_control_patch(patch, base) == {"timer": {"paused": 1700000000.0}}
```

**Expected failure:** `assert {'timer': {'start': 1000.0, 'paused': 1700000000.0, 'end': 2000.0}} ==
{'timer': {'paused': 1700000000.0}}` — the coupled branch keeps the whole object.

**Step 2 — delete.** Remove `CONTROL_COUPLED_MEMBERS` (`common/common.py:707-728`), the `coupled`
parameter and the `if key in coupled:` branch (`:781-785`) from `reduce_control_patch`, and the
recursive `coupled=frozenset()` argument (`:787`). Update the docstring: the coupling hazard is now
handled by ops, not by exclusion, with a pointer to `common/control_delta.py`. Remove the import
from `common/datastore_accessors.py` and `controller/runtime/store.py` if either names it.

Delete the four existing tests in `test_reduce_control_patch.py` that assert coupled behaviour, and
say in the commit message which four and why.

**Gate:** full gate → **2853 + 1 − 4 = 2850 passed**. This is the **first of two** deliberate net
decreases in the plan (the other is Task 16); it must be called out in the report. Everything else
must go up.

---

### Task 10: Retire the `TimerBar` guard

**Files:** modify `web-react/src/components/shell/TimerBar.tsx`,
`web-react/src/components/shell/TimerBar.controlCycle.test.tsx`.

**Precondition:** Tasks 6-9 merged. The guard's justification is
`test_a_pause_after_a_stop_in_one_cycle_resurrects_the_timer` and its sibling; both now assert the
opposite.

**Step 1 — the failing test.** In `TimerBar.controlCycle.test.tsx`, the `ControlProcess` model
(`:1-226`) currently mirrors the *reduce* seam. Rewrite it to mirror the *delta* seam: a queue of
ops applied in order against live state, no ancestor, no reduction, no coupled member. Then flip
the two cases in `describe("the control-write seam this guard exists for")` (`:338-386`):

```ts
  it("keeps a stopped countdown stopped when a pause lands in the same cycle", () => {
    const control = new ControlProcess({ start: 1000, paused: 0, end: 2000 });
    control.queue({ op: "timer.clear" });
    control.queue({ op: "timer.pause", at: 1_700_000_000 });
    control.drain();
    expect(control.timer).toEqual({ start: 0, paused: 0, end: 0 });
  });

  it("arms a fresh countdown rather than the old one when a resume lands in the same cycle", () => {
    const control = new ControlProcess({ start: 1000, paused: 1500, end: 2000 });
    control.queue({ op: "timer.clear" });
    control.queue({ op: "timer.start_or_resume", at: 1_700_000_000, seconds: 500 });
    control.drain();
    expect(control.timer).toEqual({ start: 1_700_000_000, paused: 0, end: 1_700_000_500 });
  });
```

and add:

```ts
  it("lets a second gesture through immediately", async () => {
    const { rendered } = renderRunningBar();
    await userEvent.click(rendered.getByRole("button", { name: "Stop timer" }));
    expect(rendered.getByRole("button", { name: "Pause timer" })).not.toBeDisabled();
  });
```

**Expected failure:** the last one fails with
`expected element to not be disabled` — `pendingAt` is set and `disabled={pending}` is still on both
buttons.

**Step 2 — remove the guard.** In `TimerBar.tsx` delete `pendingAt`, `pending`, the render-phase
adjustment (`:39-43`), the `signature` local (`:38`), the `write()` wrapper (`:46-53`) and every
`disabled={pending}` (three sites: `:76`, `:93`, `:107`). Buttons call `command.timerPause()` /
`timerStart(remaining)` / `timerStop()` directly. Delete the trailing rationale block (`:127-…`)
and replace it with a short note:

```tsx
// Every timer gesture queues an OP, not a computed timer state
// (common/control_delta.py). Two gestures in one control cycle compose in the
// drain against live state -- a stop followed by a pause pauses a timer that is
// already cleared, i.e. nothing -- so the bar does not need to serialize them.
// Pinned in Python at tests/characterization/test_control_delta_seam.py and in
// this file's ControlProcess model below.
```

**Step 3 — the modal comment.** `:115-120` explains why `TimerModal` is exempt from a guard that no
longer exists; delete it.

**Gate:** `bun run typecheck && bun run lint && bun run test && bun run gen:types:check && bun run build`.
`bun run lint` → **0 errors, exactly 2 pre-existing `react-refresh` warnings**.
`bun run test` → **954 + 1 (new immediacy case) = 955 passed** (the two flipped cases are renamed,
not added). No Python change, no Python gate.

---

### Task 11: The remaining `api_commands` notify writers

**Files:** modify `common/api_commands.py`; hand-edit four more golden entries.

`_cmd_set_notify` (`common/api_commands.py:431-487`, dispatched for `notify`, `limit_high`,
`limit_low` at `:837-839`, and choosing its entry `type` from `arglist[0]` at `:445-447`), the
`shutdown`/`keep_warm` branches of `_cmd_set_timer` (`:705-716`) and `_cmd_get_hopper` (`:112`) are
the notify/one-shot writers left on MERGE.

**Step 1 — the failing test.** Append to `tests/characterization/test_control_delta_seam.py`:

```python
def test_a_notify_target_set_back_to_zero_survives_a_concurrent_writer(seeded):
    """Residual 2 for notify_data: under merge_notify_data the second write was
    identical to the ancestor and therefore invisible."""
    assert _cmd("notify", "Grill", "target", "203")["result"] == "OK"
    dsa.execute_control_writes()
    assert _cmd("notify", "Grill", "target", "0")["result"] == "OK"
    assert _cmd("splus", "true")["result"] == "OK"
    dsa.execute_control_writes()
    entry = next(e for e in read_control()["notify_data"] if e["label"] == "Grill" and e["type"] == "probe")
    assert entry["target"] == 0
```

**Expected failure:** `assert 203 == 0` — the `splus` command's stale whole-dict patch carries
`target: 203`, which differs from the drain-start ancestor, so it is imposed.

**Step 2 — convert.** `_cmd_set_notify` mutates one entry's fields; emit
`{"op": "notify.set", "label": <label>, "type": <notify type>, "fields": {<only the fields it set>}}`.
Derive `type` from the subcommand exactly as the current code does
(`notify` → `probe`, `limit_high` → `probe_limit_high`, `limit_low` → `probe_limit_low`), and read
that mapping off the live code rather than from this table. The timer `shutdown`/`keep_warm`
branches become
`{"op": "notify.set", "label": <the entry's label>, "type": "timer", "fields": {"shutdown": <bool>}}`.
`_cmd_get_hopper` becomes `control_delta(set_values={"hopper_check": True})`.

**Step 3 — the golden.** `set_timer_shutdown_true`, `set_timer_shutdown_false`,
`set_timer_keep_warm_true`, `set_timer_keep_warm_false`, plus every `set_notify*` /
`set_limit_*` entry that queues a write, get a `"delta"` `queued_writes` in place of `"diff"`.
Same rules as Task 8: `control_diff_after_execute` byte-identical, `GOLDEN_SHA256` recomputed by
hand, docstring entry added. Enumerate the exact list with
`python3 -c "import json;d=json.load(open('tests/characterization/fixtures/process_command_golden.json'));print([k for k in sorted(d) if d[k]['queued_writes']])"`
and convert only those the task touches.

**Gate:** full gate green, count up by 1.

---

### Task 12: The `api_commands` scalar writers

**Files:** modify `common/api_commands.py`; hand-edit the remaining golden entries.

The remaining MERGE sites, listed by their **handler definition** line (the write sites drift, the
`def` does not): `_cmd_set_psp` (`def` at `:291`, writes at `:303`), `_cmd_set_units` (`:309`;
writes `:318, 321`), `_cmd_set_mode` (`:328`; writes `:338, 350, 369, 394`), `_cmd_set_pmode`
(`:381`), `_cmd_set_splus` (`:406`; write `:415`), `_cmd_set_lid_open` (`:418`; write `:428`),
`_cmd_set_pwm` (`:490`; write `:500`), `_cmd_set_duty_cycle` (`:503`; write `:515`),
`_cmd_set_tuning_mode` (`:524`; write `:534`), `_cmd_set_manual` (`:722`; write `:754`).

Each becomes `control_delta(set_values={...})` naming **only the members that handler assigns** —
read them off the handler, do not guess. `_cmd_set_manual` is the one with structure:
`_manual_toggle` (`:33-57`) sets `control["manual"]["change"]`, `["output"]` and sometimes
`["pwm"]`, and the guard at `:753-754` (`if control["manual"]["change"] in ["power", "igniter",
"fan", "auger", "pwm"]: write_control(...)`) decides whether to write at all — **keep that guard
exactly**, including the documented wart that it sits outside the `if/elif` chain so a rejected
request still writes when `change` holds a stale value (`:731-734`). Build the delta from the
values the toggle computed rather than from the mutated `control`.

**Step 1 — the failing test:**

```python
def test_a_setpoint_set_back_to_its_opening_value_survives_a_concurrent_writer(seeded):
    assert _cmd("psp", "225")["result"] == "OK"
    dsa.execute_control_writes()
    assert _cmd("psp", "0")["result"] == "OK"
    assert _cmd("splus", "true")["result"] == "OK"
    dsa.execute_control_writes()
    assert read_control()["primary_setpoint"] == 0


def test_a_manual_pwm_change_and_a_fan_toggle_in_one_cycle_both_land(seeded):
    control = read_control()
    control["mode"] = "Manual"
    write_control(control, WriteKind.OVERWRITE, origin="seed")
    c.SqliteQueue("queue_control_write").flush()
    assert _cmd("manual", "pwm", "50")["result"] == "OK"
    assert _cmd("manual", "fan", "true")["result"] == "OK"
    dsa.execute_control_writes()
    assert read_control()["manual"]["pwm"] == 50
```

**Expected failure:** the first asserts `225 == 0`.

**Gate:** full gate green. The golden's remaining `set_*` entries flip to `"delta"`;
`control_diff_after_execute` byte-identical throughout; `GOLDEN_SHA256` recomputed.

---

### Task 13: The fixed-layout displays

**Files:** modify `display/_base_fixed.py` (12 sites), `display/ssd1306b.py` (7 sites).

Every one is `control = read_control()` + one to three assignments + `write_control(control,
MERGE)`. Convert each to a `control_delta(set_values={...})` naming exactly those assignments and
**delete the now-unused `read_control()`**, except `ssd1306b.py:483-495` (SmokePlus) which reads
`control["s_plus"]` to toggle it — keep the read, emit `{"s_plus": not control["s_plus"]}`.

**Step 1 — the failing test.** Displays are exercised by `tests/ui/` and the display driver matrix
tests. Add `tests/unit/display/test_display_control_writes_are_deltas.py`:

```python
"""A display write must name what it changed. A fixed-layout menu that queues a
whole read_control() reverts anything the web process did in the same cycle --
`tests/characterization/test_control_writes_cross_writer.py` shows the shape."""

from unittest import mock

import pytest

from common.control_delta import CONTROL_DELTA_KEY


@pytest.mark.parametrize("module_name", ["display._base_fixed", "display.ssd1306b"])
def test_every_display_write_queues_a_delta_envelope(module_name, ds, monkeypatch):
    import importlib

    module = importlib.import_module(module_name)
    queued = []
    monkeypatch.setattr(module, "write_control", lambda payload, kind, origin=None: queued.append(payload))
    # Drive the menu handler for each command the module offers; assert every
    # queued payload carries CONTROL_DELTA_KEY and no payload has more than four
    # top-level `set` members.
    ...
```

Write the driving half against the module's actual menu entry point rather than the sketch above —
read `_base_fixed.py:1060-1290` for the handler signature before writing it.

**Expected failure:** `assert '__control_delta__' in {...40 control keys...}`.

**Gate:** full gate green, count up by 2.

---

### Task 14: The flex-layout displays

**Files:** modify `display/_base_flex.py` (18 sites), `display/qtquick_flex.py` (6 sites).

These **already** queue minimal partials, so the change is a wrap:
`write_control(data, WriteKind.MERGE, ...)` → `write_control(control_delta(set_values=data),
WriteKind.DELTA, ...)`. Two exceptions:

- `display/_base_flex.py`'s `notify` branch and `display/qtquick_flex.py:119` build
  `{"notify_data": control["notify_data"]}` from a read-modify-write. Replace with
  `control_delta(ops=[{"op": "notify.set", "label": <entry label>, "type": <entry type>,
  "fields": {"target": target, "req": bool(target)}}])` — and note that `qtquick_flex.py:113-118`
  matches on `entry["name"] == origin`, **not** on label, so read the entry's `label` and `type` off
  the matched entry rather than assuming they equal `origin`.
- `tests/ui/test_qtquick_dispatch_persistence.py:26,58` drains the queue and asserts the resulting
  control. Those assertions are about the **result**, not the payload, and must not change.

**Step 1 — the failing test:** extend Task 13's parametrize list with
`"display._base_flex"` and `"display.qtquick_flex"`.

**Gate:** full gate green.

---

### Task 15: The blueprints and the shared helpers

**Files:** modify `blueprints/tuner/routes.py` (8), `blueprints/admin/routes.py` (3),
`blueprints/pellets/routes.py` (3), `blueprints/settings/routes.py` (2), `common/app.py` (1),
`common/system.py` (1), `blueprints/mobile/socket_io.py` (the 8 non-timer, non-control sites).

Mechanical, one delta per site, naming only the assigned members:

| site | delta |
|---|---|
| `tuner/routes.py:46, 71, 111` | `control_delta(set_values={"tuning_mode": True/False})` |
| `tuner/routes.py:51, 55, 61, 76, 119` | `control_delta(set_values={"mode": Mode.MONITOR/Mode.STOP, "updated": True})` |
| `admin/routes.py:88, 92` | `control_delta(set_values={"settings_update": True})` |
| `admin/routes.py:137` (factory defaults) | `control_delta(set_values={<every default_control() scalar>}, ops=[{"op": "notify.replace", "entries": default_control()["notify_data"]}])` — the reseed genuinely replaces the array, and `notify.replace` says so instead of relying on `merge_notify_data`'s delete-on-omit (`common/common.py:866-867`) |
| `pellets/routes.py:31, 44, 120` | `control_delta(set_values={"hopper_check": True})` |
| `settings/routes.py:101` | `control_delta(set_values={"probe_profile_update": True})` — and see `common/app.py:381-390` `update_probe_config`, which sets it |
| `settings/routes.py:300` | same |
| `common/app.py:405` `save_settings_and_flag_update` | `control_delta(set_values={flag: True for flag in flags})`. **It no longer needs the `control` parameter** — check all callers before removing it, and if any relies on the in-place mutation, keep the parameter and note why |
| `common/system.py:341` `gather_system_info` | `control_delta(set_values={"system": system_info_slice})` — read `:300-341` for exactly which members it assigns |
| `socket_io.py:516, 525` (units) | `control_delta(set_values={"updated": True, "units_change": True})` |
| `socket_io.py:542, 552, 603, 697, 706` (pellets) | `control_delta(set_values={"hopper_check": True})` |
| `socket_io.py:713, 723` (recipes) | `control_delta(set_values={"updated": True, "mode": Mode.RECIPE, "recipe": {"filename": ...}})` |
| `socket_io.py:1026` (probes) | `control_delta(ops=[{"op": "notify.replace", "entries": updated_notify_data}])` |

**Step 1 — the failing test.** `tests/characterization/test_control_writes_cross_writer.py:216`
`test_background_full_control_write_does_not_eat_a_notify_write` drives `gather_system_info` and
today passes *because of* the reduce. Add a sibling that only a delta can satisfy:

```python
def test_a_background_system_write_does_not_hide_a_restore_to_the_opening_value(seeded):
    """gather_system_info writes only control["system"]. Under the reduce it
    still carried a stale copy of everything else, and a concurrent writer
    restoring a member to its opening value was invisible."""
    assert _set_notify("Grill", "target", "203")["result"] == "OK"
    dsa.execute_control_writes()
    assert _set_notify("Grill", "target", "0")["result"] == "OK"
    _gather_system_info()
    dsa.execute_control_writes()
    assert _entry(read_control(), "Grill", "probe")["target"] == 0
```

**Gate:** full gate green after every sub-step. This task is large; commit **per module**, each
with its own green full gate, so a bisect lands on one file.

---

### Task 16: Delete `reduce_control_patch`, `merge_notify_data` and their tests

**Files:** modify `common/common.py`, `common/datastore_accessors.py`,
`controller/runtime/store.py`; delete `tests/unit/common/test_reduce_control_patch.py`,
`tests/unit/common/test_merge_notify_data.py`; modify
`tests/characterization/test_control_writes_cross_writer.py`.

**Precondition, verify before starting:**
`grep -rn "WriteKind.MERGE" --include="*.py" . | grep -v "^./tests/"` must return **only**
`common/datastore_accessors.py:81` (the branch itself) and `controller/runtime/store.py:157`.
If any writer remains, **stop and convert it** — deleting the reduce with a whole-dict writer still
live reintroduces the original clobber for that writer.

**Step 1 — the failing test.** Add to `tests/characterization/test_control_delta_seam.py`:

```python
def test_no_production_writer_still_queues_a_whole_control_dict(seeded):
    """The reduce is about to be deleted. Its safety net was that a stale whole
    dict could not revert an earlier writer; without a whole-dict writer there is
    nothing to net."""
    import pathlib

    hits = []
    for path in pathlib.Path(".").rglob("*.py"):
        parts = path.parts
        if parts[0] in {"tests", ".jj", ".git", ".venv"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "WriteKind.MERGE" in text and path.as_posix() not in {
            "common/datastore_accessors.py",
            "controller/runtime/store.py",
            "common/common.py",
        }:
            hits.append(path.as_posix())
    assert hits == [], f"still queueing whole control dicts: {hits}"
```

**Expected failure before Task 15 completes:** a list of the unconverted modules.

**Step 2 — delete.** Remove `reduce_control_patch` (`common/common.py:731-793`),
`merge_notify_data` (`:831-895`), `notify_data_key` (`:796-806`) and `_key_notify_data`
(`:809-828`). Strip the corresponding branch from both drains, leaving `WriteKind.MERGE` as a plain
`strip_null_members` + `json_patch` (SQLite) / `deep_update` (in-memory) — **which is exactly what
`tests/oracle/fixtures/control_merge.json` pins** (a single minimal partial, no competing writer),
so that fixture stays untouched and is the guard that the primitive still works. Delete the two
unit-test files (15 + 12 tests).

**Step 3 — rewrite the cross-writer suite.**
`tests/characterization/test_control_writes_cross_writer.py`'s module docstring (`:1-46`) describes
the reduce/merge fix as the mechanism. Rewrite it to describe the delta seam, keep every test that
still names real writers, and re-point the two that assert reduce-specific behaviour.

**Gate:** full gate. Expected count: previous **− 27** (15 reduce + 12 merge unit tests) **+ 1**
(the sweep). Both the deletion and the arithmetic go in the report; **this is the second and last
sanctioned decrease.**

---

### Task 17: The property, and the final gate

**Files:** modify `tests/characterization/test_control_delta_seam.py`.

**Step 1 — the property test.** The invariant this whole plan bought, made executable:

```python
_PAIRS = [
    (("timer", "start", "600"), ("timer", "stop")),
    (("timer", "stop"), ("timer", "pause")),
    (("timer", "pause"), ("timer", "stop")),
    (("psp", "225"), ("splus", "true")),
    (("psp", "225"), ("psp", "0")),
    (("notify", "Grill", "target", "203"), ("notify", "Grill", "req", "true")),
    (("notify", "Grill", "target", "203"), ("notify", "Grill", "target", "0")),
    (("splus", "true"), ("pmode", "2")),
    (("timer", "start", "600"), ("timer", "shutdown", "true")),
]


@pytest.mark.parametrize("first,second", _PAIRS, ids=lambda p: "_".join(str(x) for x in p))
def test_two_commands_in_one_cycle_match_the_same_two_one_cycle_apart(seeded, first, second):
    """THE invariant. Both residuals named in the task-ctl report were violations
    of it; every op and every `set` in this plan exists to restore it.

    Scoped to ACCEPTED commands: request-time validation (e.g. the 4-argument
    timer form's paused-timer rejection, common/api_commands.py:620-623) reads a
    stale blob, and no queue representation can fix a synchronous HTTP answer.
    """

    def _run(drain_between):
        write_control(default_control(), WriteKind.OVERWRITE, origin="prop")
        c.SqliteQueue("queue_control_write").flush()
        assert _cmd(*first)["result"] == "OK"
        if drain_between:
            dsa.execute_control_writes()
        assert _cmd(*second)["result"] == "OK"
        dsa.execute_control_writes()
        return read_control()

    assert _run(drain_between=False) == _run(drain_between=True)
```

**Expected failure if any earlier task regressed:** a dict diff naming the exact member that still
depends on drain timing.

**Step 2 — the final gate.**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
uvx ruff format common/ blueprints/ display/ tests/
uvx ruff check --config ruff.toml
cd web-react && bun run typecheck && bun run lint && bun run test && bun run gen:types:check && bun run build
```

**Step 3 — the report.** Record, with numbers: the final suite count and its full derivation from
2796; every golden entry edited and the `GOLDEN_SHA256` before and after; the two deliberate test-count
decreases (Tasks 9 and 16); and a one-line statement that
`tests/oracle/fixtures/*.json` were **not** touched.

---

## Parallelization

**Concurrency here needs isolated jj workspaces — disjoint file lists are not sufficient.** Every
task runs the full Python suite against a shared `pifire.db` resolved from the checkout
(`common/datastore.py:11`), so two tasks in one working copy corrupt each other's fixtures even with
zero file overlap. Create one workspace per concurrent lane:

```
jj workspace add ../PiFire-delta-<lane>
cp .lsp.json ../PiFire-delta-<lane>/.lsp.json      # gitignored; its absence is why LSP "breaks"
cd ../PiFire-delta-<lane> && bun install           # only for the Task 10 lane
```

- **Wave 0 — Tasks 1 → 2 → 3 → 4, strictly sequential, one lane.** Each builds directly on the
  previous file. Task 3 and Task 4 both append to `_OP_APPLIERS`; that is a two-line collision in
  one dict and is not worth a second workspace.
- **Wave 1 — Task 5 alone.** It changes both drains, which every later task depends on.
- **Wave 2 — Task 6 → Task 7 → Task 8, strictly sequential, one lane.** Task 6 leaves the suite
  red on purpose and Task 8 is what makes it green; they cannot be split across workspaces without
  merging a red tree. Task 7 sits between them because Task 8's golden edit must account for both.
- **Wave 3 — Task 9 ∥ Task 10.** Task 9 is Python-only (`common/common.py`, both drains); Task 10
  is web-react-only. **Two workspaces**; Task 10's lane needs `bun install`. Task 9 is Task 10's
  logical precondition (it deletes the exclusion the guard cites) but not its *mechanical* one —
  Task 8 already flipped the pins Task 10's comments point at, so they may run at once.
- **Wave 4 — Task 11 → Task 12, sequential, one lane.** Both edit `common/api_commands.py` and both
  hand-edit the same golden file and the same `GOLDEN_SHA256` constant. Two lanes would produce two
  incompatible digests.
- **Wave 5 — Task 13 ∥ Task 14 ∥ Task 15.** Three disjoint module groups (fixed displays / flex
  displays / blueprints+helpers), **three workspaces**. They share only the new test file from
  Tasks 13-14 — resolve by having Task 14 extend the parametrize list Task 13 created, and land
  Task 13 first if both finish together. Task 15 is the largest; commit it per module.
- **Wave 6 — Task 16 alone**, and it must not start until Wave 5's sweep test is green.
- **Wave 7 — Task 17 alone.**

**Blocked / human-decision items:** none. Every flip in this plan is a behaviour change this plan
argues for; there is no item awaiting a product call.

---

## Could NOT verify (stated, not glossed)

- **The full suite baseline was measured as `10 failed, 2786 passed` (451 s), not 2796 passed.**
  All visible failures are `tests/web/test_page_admin.py::…[chromium]`, and 2786 + 10 = 2796 exactly,
  which is consistent with the stated baseline plus the known "another agent owns the browser"
  contention (`feedback_chromium_tests_skip_in_agent_env`). **I could not confirm the 10 are the
  only chromium casualties** — pytest's `-q` tail truncated the list to 4. Whoever merges must
  re-run `tests/web/` in the main checkout with the browser free.
- **`tests/web/test_page_api.py::test_post_control_merges_via_write_control` is a `[chromium]`
  test**, and Task 7 changes exactly the route it covers. Task 7's own tests are plain Flask
  test-client tests and do cover the behaviour, but the chromium one must be re-run before merge.
- **Playwright e2e (`web-react/tests/e2e/*.spec.ts`) was not run** for Task 10. The task-wa report
  §1 states no e2e spec asserts the timer guard; I did not re-verify that against the current tree.
- **`controller/runtime/modes/base.py`'s 10 write sites were found by grep, not LSP**, because
  `ctx.store` is untyped and pyright resolves the calls to nothing. I read all 10 and every one is
  `WriteKind.OVERWRITE, origin="control"`. If `ctx` is ever typed, re-run the LSP query before
  trusting this plan's "97 MERGE sites" total.
- **The exact set of golden entries Tasks 11 and 12 must edit** is derived at task time by the
  one-liner in Task 11 Step 3 rather than enumerated here; I verified only the six timer entries
  Task 8 touches.
- **Whether supervisor restarts `control` before `webapp`** on this deployment. `updater/upgrade.sh`
  copies both `.conf` files (`:174-184`) and the restart comes from
  `systemctl restart supervisor` — I found no `priority=` in the shipped configs and did not read
  `auto-install/supervisor/*.conf`. The upgrade analysis therefore assumes **no ordering guarantee**,
  which is the conservative reading.
- **Real-hardware behaviour of any of this.** Everything above is verified against source and the
  test suite only.

---

## Self-Review

**Spec coverage.** Residual 1 (coupled `timer`) → Tasks 4, 6, 7, closed by drain-time op evaluation
and pinned by the two flipped resurrection tests. Residual 2 (restore-to-ancestor) → Tasks 2, 11,
12, 15, closed by presence-is-intent and pinned per writer class. Writer enumeration → LSP over all
401 `.py` files, 97 MERGE sites in 15 modules, with the one LSP blind spot named. Delta
representation → `set`/`delete`/`ops` with deletion, `notify_data` addressing and the coupled
`timer` all answered explicitly. Migration shape → both paths on one queue, converted in waves,
with four alternatives rejected on the record. Deletions → `CONTROL_COUPLED_MEMBERS` (Task 9),
`TimerBar` guard (Task 10), `reduce_control_patch` + `merge_notify_data` (Task 16), the 4-arg form's
one-write rationale (Task 8, comments only — the endpoint stays on its two independent reasons).
Cross-process → both directions plus a future-version direction, bounded by `control.py:102`.

**Test-count arithmetic.** 2796 → 2810 (T1 +14) → 2819 (T2 +9) → 2826 (T3 +7) → 2837 (T4 +11) →
2846 (T5 +9) → 2853 (T6 +3 and T7 +4, red until T8 makes them green) → **2850** (T9: +1, −4) → 2850
(T10, web-react only) → 2851 (T11 +1) → 2853 (T12 +2) → 2855 (T13 +2) → 2855 (T14 +0) → 2856
(T15 +1) → **2830** (T16: +1, −27) → **2839** (T17 +9). Two decreases, both deliberate, both
flagged for the report; every other task's count goes up. web-react: 954 → 955 (T10).

**Golden fixtures.** Two hand-edits of `process_command_golden.json` (Tasks 8 and 11-12), each with
a recomputed `GOLDEN_SHA256`, each restricted to `queued_writes`, each required to leave
`control_diff_after_execute` byte-identical. `tests/oracle/fixtures/*.json` untouched, and Task 16
explains why `control_merge.json` survives the deletion.

**Placeholder scan.** Every task names its files by path, its failing test by name, its expected
failure text, and its gate command with an expected count. Two tasks (13, 15) deliberately defer a
*sub-list* to a stated one-liner rather than guessing it — flagged in "Could NOT verify" rather than
filled with plausible-looking content.
