# Remove the Legacy External Mobile App Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the Socket.IO request/response protocol that only the legacy external PiFire app speaks, keeping the live broadcast feed that the in-tree `web-react` and `mobile` apps depend on.

**Architecture:** `blueprints/mobile/socket_io.py` serves two unrelated protocols through one socket. The **live feed** (`listen_app_data` → `socket_dash_data` / `socket_pellet_data`) is what the in-tree clients use through `@pifire/core`'s `createLiveConnection`. The **request/response dispatch** (`get_app_data` / `post_app_data`, plus the `socket_event_data` push) is spoken by nothing in this repo. This plan removes the second while leaving the first byte-identical, starting with a characterization test that pins the live payloads so any accidental change to them fails loudly.

**Tech Stack:** Python 3.14, Flask + Flask-SocketIO, pytest (`--random-order`, xdist), ruff 0.16, jj (colocated git), bun workspaces (`web-react`, `mobile`, `packages/pifire-core`).

**Spec:** None separate — this plan was derived directly from the codebase. The evidence it argues from is inline in **§ Evidence** below; executors should read that section as the spec.

## Global Constraints

- **Commit with `jj`, never `git`.** The repo is colocated so `git commit` silently works and puts the change in the wrong place. Use `jj new` **before** the first edit of a task and `jj describe --stdin` (there is no `-F` flag) to set the message.
- **Never put backticks inside a double-quoted shell argument** — zsh eats them. Use a quoted heredoc for every commit message.
- **Python test command:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q --timeout=300`. A bare `python`/`pytest` gives false failures.
- **Formatter/linter:** `.venv/bin/ruff format .` then `.venv/bin/ruff check .`. Never `uvx ruff`.
- **Baseline to hold:** 8066 passed, 5 skipped, 0 failed. Any task ending with a different failure count has broken something.
- **Find callers with serena/LSP, never grep.** `grep` missed `socket.io-client` in `packages/pifire-core/package.json` during this investigation and nearly produced a plan that deleted a live feed. Use `mcp__serena__find_referencing_symbols`, which works across Python *and* TypeScript.
- **Known flake, not caused by this work:** `tests/ui/test_fixed_drivers_methods.py::test_ili9341f_bring_up_diagnostics_go_to_the_control_log` failed once in ~12 full runs and has never reproduced. If it fails, re-run before investigating.

---

## Evidence

Established with `mcp__serena__find_referencing_symbols` and by reading the client sources. **Do not re-derive this with grep.**

### The live feed — MUST SURVIVE UNCHANGED

`packages/pifire-core/src/liveConnection.ts::createLiveConnection` opens socket.io at path `/socket.io` and:

- **emits** `listen_app_data` on connect
- **listens for** `socket_dash_data`, `socket_pellet_data`, plus `connect` / `connect_error` / `disconnect`

Both in-tree apps consume it:

- `web-react/src/helpers/useLiveState.ts:7` — `import { createLiveConnection } from "@pifire/core/liveConnection"`
- `mobile/src/useLive.ts:8` — same import

`socket.io-client` is a dependency of **`packages/pifire-core/package.json`**, not of `web-react/package.json` or `mobile/package.json`. That is why a naive dependency grep says "no client uses socket.io" and is wrong.

Server symbols that serve this feed, all of which stay:

| Symbol | Role |
| --- | --- |
| `handle_connect`, `handle_disconnect` | connection lifecycle |
| `listen_app_data` | starts the broadcast thread |
| `_emit_app_data` | broadcast loop |
| `_emit_app_data_to` | per-client initial burst |
| `_get_dash_data` | builds the `socket_dash_data` payload |
| `_get_pellet_socket_data` | builds the `socket_pellet_data` payload |

**`_get_dash_data` and `_get_pellet_socket_data` are shared** — called by both the live path and the legacy dispatch (`_get_app_data_dash_data:543`, `_get_app_data_pellets_data:547`). Deleting the legacy wrappers must not touch the builders.

### The command path — MUST SURVIVE UNCHANGED

`packages/pifire-core/src/command.ts::createCommand` does **not** use the socket. `buildCommandUrl` builds `${baseUrl}/api/${segments.join("/")}` and `post()` `fetch`es it. All control commands from both apps go over REST to `blueprints/api`. The socket `post_app_data` handler serves none of them.

### The legacy surface — TO BE REMOVED

Nothing in `mobile/`, `web-react/src`, `packages/pifire-core`, or `templates/` references any of these:

- Socket handlers `get_app_data` (`socket_io.py:204`) and `post_app_data` (`socket_io.py:209`)
- Dispatch tables `_GET_APP_DATA_DISPATCH`, `_POST_APP_DATA_DISPATCH`, `_ACTIONS_REQUIRING_JSON_DATA`
- All eight `_get_app_data_*` handlers and all eight `_post_app_data_*` handlers
- The `socket_event_data` emission (3 sites) and its payload source `read_events_records` → `read_events`

The events feed is the clearest case: `mobile/app/(tabs)/events.tsx` and `web-react/src/components/logs/EventsPage.tsx` both read the raw log through `GET /api/admin/logs/view?log=events`. The mobile source says so explicitly: *"there is no separate structured events API; this admin log view is the one PiFire already serves."*

---

## File Structure

| File | Change |
| --- | --- |
| `tests/web/test_socket_live_contract.py` | **Create** — characterization test pinning the live event names and payload keys |
| `blueprints/mobile/socket_io.py` | **Modify** — 1271 lines today; the legacy dispatch is the bulk of it |
| `common/common.py` | **Modify** — remove `read_events`, `read_events_records` once orphaned |
| `tests/unit/common/test_common_blobs.py` | **Modify** — remove the `read_events*` tests along with the code they cover |
| `tests/web/test_socketio_app_data.py` | **Modify** — drop legacy-dispatch cases, keep live-feed cases |
| `tests/web/test_api_admin_clear_events.py` | **Modify** — drops its `read_events_records` reference |

---

### Task 1: Pin the live socket contract before deleting anything

The whole risk of this plan is silently changing what `web-react` and `mobile` receive. This task builds the tripwire first. It deletes nothing.

**Files:**
- Create: `tests/web/test_socket_live_contract.py`

**Interfaces:**
- Consumes: nothing
- Produces: a test module later tasks re-run unchanged after every deletion

- [ ] **Step 1: Open a fresh change**

```bash
cd /home/dannyb/sources/PiFire && jj new
```

- [ ] **Step 2: Read the two payload builders so the pinned keys are real, not invented**

```bash
# Use serena, not sed, so you get the whole symbol:
#   mcp__serena__find_symbol name_path="_get_dash_data" \
#     relative_path="blueprints/mobile/socket_io.py" include_body=true
#   mcp__serena__find_symbol name_path="_get_pellet_socket_data" \
#     relative_path="blueprints/mobile/socket_io.py" include_body=true
```

Write down the exact top-level keys each returns. The test below asserts on those real keys — a fixture invented from the producer's fallback literals would only prove the test agrees with itself.

- [ ] **Step 3: Write the characterization test**

Model the fixtures on `tests/web/test_socket_dash_payload_fields.py`, which already calls `socket_io._get_dash_data(read_settings(), read_pellet_db())` — reuse its fixture setup rather than inventing one.

```python
"""The live socket contract that web-react and mobile depend on.

`packages/pifire-core/src/liveConnection.ts` emits `listen_app_data` and
listens for exactly `socket_dash_data` and `socket_pellet_data`. Both apps
reach it through `@pifire/core`. This module pins that surface so removing
the legacy `get_app_data`/`post_app_data` dispatch cannot quietly change it.
"""

import blueprints.mobile.socket_io as socket_io


def test_live_feed_handlers_are_registered():
    #  Replace with the real top-level keys recorded in Step 2.
    assert callable(socket_io.listen_app_data)
    assert callable(socket_io._emit_app_data_to)


def test_dash_payload_keys_are_unchanged(ds):
    payload = socket_io._get_dash_data(read_settings(), read_pellet_db())
    assert set(payload) == EXPECTED_DASH_KEYS  # from Step 2


def test_pellet_payload_keys_are_unchanged(ds):
    payload = socket_io._get_pellet_socket_data(read_settings(), read_pellet_db())
    assert set(payload) == EXPECTED_PELLET_KEYS  # from Step 2
```

- [ ] **Step 4: Run it — it must PASS (this is characterization, not TDD-red)**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_socket_live_contract.py -q --timeout=60
```

Expected: PASS. If it fails, the keys recorded in Step 2 are wrong — fix the test, not the production code.

- [ ] **Step 5: Prove the pin actually bites**

Temporarily add a junk key to `_get_dash_data`'s returned dict, re-run, confirm FAIL, then revert. A pin that cannot fail is worth nothing.

- [ ] **Step 6: Format, lint, full suite**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q --timeout=300
```

Expected: 8069 passed (8066 + 3 new), 5 skipped.

- [ ] **Step 7: Commit**

```bash
jj describe --stdin <<'EOF'
Pin the live socket contract before removing the legacy dispatch

web-react and mobile both reach socket_dash_data and socket_pellet_data
through @pifire/core's createLiveConnection. These characterization tests
fix those payload key sets so the legacy-dispatch removal cannot change
them unnoticed.
EOF
jj new
```

---

### Task 2: Remove the `socket_event_data` feed

**Files:**
- Modify: `blueprints/mobile/socket_io.py` (3 `socket_event_data` emissions, the `read_events_records` import)
- Modify: `common/common.py` (`read_events`, `read_events_records`)
- Modify: `tests/unit/common/test_common_blobs.py` (the `read_events*` tests)
- Modify: `tests/web/test_socketio_app_data.py`, `tests/web/test_api_admin_clear_events.py`

**Interfaces:**
- Consumes: Task 1's contract test (must still pass)
- Produces: `common.common` no longer exports `read_events` or `read_events_records`

- [ ] **Step 1: Confirm the feed is unreferenced outside this repo's server**

```bash
#   mcp__serena__find_referencing_symbols name_path="read_events_records" \
#     relative_path="common/common.py"
```

Expected: only `blueprints/mobile/socket_io.py` (3 call sites + the import) and test files. If any `mobile/` or `web-react/` file appears, STOP and report — the premise is wrong.

- [ ] **Step 2: Delete the three emissions**

Remove the `socketio.emit("socket_event_data", ...)` line from `_emit_app_data`, from `_emit_app_data_to`, and the `_get_app_data_events_data` handler's dispatch entry.

- [ ] **Step 3: Delete the now-orphaned readers**

Remove `read_events_records` and `read_events` from `common/common.py`, and the `read_events_records` import in `socket_io.py`.

- [ ] **Step 4: Delete their tests**

From `tests/unit/common/test_common_blobs.py` remove `test_read_events_records_returns_dicts`, `test_read_events_records_caps_at_60`, `test_read_events_does_not_pad_short_logs`, `test_read_events_records_emits_only_real_events`, `test_read_events_on_a_missing_log_is_empty_and_creates_nothing`, and the now-unused `common_mod` / `read_events_records` imports.

These tests were added days ago and are being deleted with the code they cover — that is correct, not waste. Keep `test_flush_events_records_clears_and_returns_empty` only if `flush_events_records` survives (check with serena; it clears the datastore, not the file, and may have other callers).

- [ ] **Step 5: Verify nothing dangling**

```bash
.venv/bin/ruff check . --select F821,F401
```

Expected: no undefined names, no unused imports.

- [ ] **Step 6: Format, lint, full suite**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q --timeout=300
```

Expected: 0 failures. Task 1's contract test must still pass.

- [ ] **Step 7: Commit**

```bash
jj describe --stdin <<'EOF'
Remove the socket_event_data feed

Nothing in this repo consumes it. Both in-tree clients read the events log
through GET /api/admin/logs/view?log=events -- mobile/app/(tabs)/events.tsx
says so outright -- so read_events_records and read_events had no consumer
beyond the three Socket.IO emissions removed here.
EOF
jj new
```

---

### Task 3: Remove the `post_app_data` handler and its dispatch

**Files:**
- Modify: `blueprints/mobile/socket_io.py`
- Modify: `tests/web/test_socketio_app_data.py`

**Interfaces:**
- Consumes: Task 1's contract test
- Produces: `socket_io` no longer defines `post_app_data`, `_POST_APP_DATA_DISPATCH`, `_ACTIONS_REQUIRING_JSON_DATA`, or any `_post_app_data_*`

- [ ] **Step 1: Confirm no client posts over the socket**

Re-read **§ Evidence → The command path**: `createCommand` uses REST. Confirm with serena that `post_app_data` has no referencing symbol outside `socket_io.py` and its tests:

```bash
#   mcp__serena__find_referencing_symbols name_path="post_app_data" \
#     relative_path="blueprints/mobile/socket_io.py"
```

- [ ] **Step 2: Delete the handler, the dispatch table, and the eight sub-handlers**

Remove `post_app_data`, `_POST_APP_DATA_DISPATCH`, `_ACTIONS_REQUIRING_JSON_DATA`, and `_post_app_data_update`, `_post_app_data_admin`, `_post_app_data_units`, `_post_app_data_pellets`, `_post_app_data_timer`, `_post_app_data_recipes`, `_post_app_data_probes`, `_post_app_data_notify`, `_post_app_data`.

- [ ] **Step 3: Drop the legacy cases from the socket tests**

In `tests/web/test_socketio_app_data.py`, remove tests that exercise `post_app_data`. Keep every test covering `listen_app_data`, `_emit_app_data`, and the dash/pellet payloads.

- [ ] **Step 4: Format, lint, full suite**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q --timeout=300
```

Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
jj describe --stdin <<'EOF'
Remove the legacy post_app_data socket dispatch

Both in-tree apps send commands over REST: @pifire/core's createCommand
builds ${baseUrl}/api/<segments> and fetches it. Nothing speaks this
socket write protocol.
EOF
jj new
```

---

### Task 4: Remove the `get_app_data` handler and its dispatch

**Files:**
- Modify: `blueprints/mobile/socket_io.py`
- Modify: `tests/web/test_socketio_app_data.py`

**Interfaces:**
- Consumes: Task 1's contract test
- Produces: `socket_io` no longer defines `get_app_data`, `_GET_APP_DATA_DISPATCH`, or any `_get_app_data_*`. `_get_dash_data` and `_get_pellet_socket_data` remain and keep their current signatures.

- [ ] **Step 1: Delete the handler, the dispatch table, and the sub-handlers**

Remove `get_app_data`, `_GET_APP_DATA_DISPATCH`, and `_get_app_data_settings_data`, `_get_app_data_dash_data`, `_get_app_data_pellets_data`, `_get_app_data_events_data`, `_get_app_data_hopper_level`, `_get_app_data_info_data`, `_get_app_data_manual_data`, `_get_app_data_recipe_data`, `_get_app_data`.

**Do not touch `_get_dash_data` or `_get_pellet_socket_data`.** `_get_app_data_dash_data` and `_get_app_data_pellets_data` are thin wrappers *around* them; only the wrappers go.

- [ ] **Step 2: Confirm the builders survived**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_socket_live_contract.py tests/web/test_socket_dash_payload_fields.py -q --timeout=60
```

Expected: PASS.

- [ ] **Step 3: Drop the legacy cases from the socket tests**

- [ ] **Step 4: Format, lint, full suite**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q --timeout=300
```

- [ ] **Step 5: Commit**

```bash
jj describe --stdin <<'EOF'
Remove the legacy get_app_data socket dispatch

The live feed keeps its payload builders: _get_app_data_dash_data and
_get_app_data_pellets_data were wrappers around _get_dash_data and
_get_pellet_socket_data, which _emit_app_data still uses.
EOF
jj new
```

---

### Task 5: Sweep the helpers the dispatch left orphaned

**Files:**
- Modify: `blueprints/mobile/socket_io.py`

**Interfaces:**
- Consumes: Tasks 2–4
- Produces: no unreferenced module-level symbols in `socket_io.py`

- [ ] **Step 1: Test each candidate with serena before deleting it**

For every symbol below, run `mcp__serena__find_referencing_symbols name_path="<symbol>" relative_path="blueprints/mobile/socket_io.py"`. Delete only those whose *sole* remaining references are the ones you just removed. A symbol still referenced by `_get_dash_data`, `_emit_app_data`, or anything outside this module **stays**.

Candidates, in the order to check them:

`_response`, `recipe_folder`, `_NOTIFY_DTO_FIELDS`, `_NOTIFY_CLEARED`, `_get_probe_data`, `_get_probe_structure`, `_get_probe_max_temp`, `_get_timer_notify_data`, `_encode_assets`, `_encode_img`, `_update_probe_config`, `_notify_fields_from_dto`, `_update_notify_data`, `_write_settings`, `_get_system_info`.

Expect several to survive. `_check_control_status`, `_finite_float`, and `_project_thermocouple_health` are **expected to be live** (they feed `_get_dash_data`) — verify rather than assume, but do not delete them on a hunch.

- [ ] **Step 2: Delete only the confirmed-orphaned symbols, and their now-unused imports**

- [ ] **Step 3: Let ruff confirm nothing dangles**

```bash
.venv/bin/ruff check . --select F401,F811,F821
```

Expected: clean. F401 is in `ruff.toml`'s ignore list, so pass `--select` explicitly as shown — a bare `ruff check .` will not report unused imports.

- [ ] **Step 4: Format, lint, full suite**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q --timeout=300
```

- [ ] **Step 5: Commit**

```bash
jj describe --stdin <<'EOF'
Drop helpers orphaned by the legacy socket dispatch removal

Each deletion was confirmed unreferenced with the language server; the
helpers feeding _get_dash_data are untouched.
EOF
jj new
```

---

### Task 6: Reconcile the generated contracts and run the client gates

The Python suite cannot tell you whether the TypeScript side still builds. This task closes that gap.

**Files:**
- Modify (only if the inventory test demands it): `packages/pifire-core/src/contracts/*.gen.ts`
- Modify (only if it references removed surfaces): `tests/unit/common/web_contracts/test_inventory.py`

- [ ] **Step 1: Run the contract inventory test**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/web_contracts/ -q --timeout=60
```

If it passes, the removed surfaces were never in the contract inventory — skip to Step 3 and note that in the commit.

- [ ] **Step 2: If it fails, regenerate rather than hand-edit**

`.gen.ts` files are generated. Find the generator the inventory test names in its failure message and re-run it; do not edit a `.gen.ts` by hand.

- [ ] **Step 3: Run the web-react and mobile gates**

```bash
cd web-react && bun run lint && bun run typecheck && bun run test
cd ../packages/pifire-core && bun run typecheck && bun run test
cd ../../mobile && bun run typecheck
```

All must pass. `bun run lint` is required, not optional — Biome formatting is a merge gate here. Note that `bun run test` uses **rstest**, not vitest; a vitest-style import error means you invoked it wrong, not that a test is red.

- [ ] **Step 4: Confirm the live feed still works end to end**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/ -q --timeout=300
```

- [ ] **Step 5: Full suite one last time**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q --timeout=300
```

Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
jj describe --stdin <<'EOF'
Reconcile contracts after the legacy socket removal

web-react, mobile and pifire-core all typecheck, lint and test against the
reduced socket surface.
EOF
jj new
```

---

## Follow-ups, deliberately out of scope

The legacy external app is **dead** — that is settled, and no task needs to hedge
about it. Breaking its protocol is the point of this plan, not a risk to weigh.

1. **`blueprints/mobile/` may become an empty shell.** After Tasks 2–5, check whether `mobile_bp` (registered at `/mobile` in `app.py:125`) still serves anything. If it does not, removing the blueprint and its registration is a natural follow-up — left out here because it changes `app.py`'s startup path and deserves its own review.
2. **`socket_io.py` is 1271 lines today** and should shrink dramatically. If what remains is only the live feed, consider renaming the module to say so.
