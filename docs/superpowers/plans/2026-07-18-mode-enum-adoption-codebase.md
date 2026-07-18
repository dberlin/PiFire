# Codebase-wide Mode Enum Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Adopt the existing `common.modes.Mode` StrEnum in the remaining non-controller areas — `common/` (incl. `api_commands.py`), `notify/`, `blueprints/`, `display/` — replacing bare mode-string literals at **mode-semantic** sites (~170) with `Mode.X`, with zero runtime behavior change. The controller domain was already converted; this completes the sweep.

**Architecture:** `Mode(StrEnum)` members ARE their strings (`Mode.SMOKE == "Smoke"`, `str(Mode.SMOKE) == "Smoke"`, JSON → `"Smoke"`), so this is a type-safety/readability change with byte-identical runtime behavior. `control["mode"]`/`status["mode"]` read back from the datastore as plain `str` and still compare `==` to `Mode` members. Templates (Jinja), JS, JSON, and recipe files are NOT touched — they keep plain strings and interoperate.

**Tech Stack:** Python 3.14, `enum.StrEnum`, pytest, `uv`/`uvx ruff`.

## Global Constraints

- Python 3.14. `except (A, B)` canonical.
- **TEST COMMAND (exact):** `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q`.
- Before every commit: `uvx ruff format <changed>` then `uvx ruff check <changed>` (leave pre-existing errors in lines you didn't touch).
- Plain Read/Edit. In a worktree, do NOT use Serena (edits the main checkout).
- Commit `git commit -F <msgfile>`; Co-author `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; use the per-task messages below. One commit per area.
- **BEHAVIOR-PRESERVING.** StrEnum serializes/compares/stringifies identically. The goldens (esp. the SHA-pinned `tests/characterization/test_process_command_golden.py`) + all area test suites must stay green with **zero assertion edits, no golden regeneration.** If any golden would move, STOP — you converted a non-mode string or hit a `.value`/`.name` mistake.
- Baseline to match: **1521** (+ no new tests unless a task adds a small guard).
- Branch `refactor/mode-enum-adoption`, off `massive-reworks-and-new-ui`.

## What IS and ISN'T a conversion site (the crux — this is judgment, not find-replace)

**CONVERT** (mode-semantic — the string functions as a controller mode):
- Comparisons against a mode value: `control["mode"] == "Smoke"`, `status["mode"] != "Stop"`, `mode in ["Smoke","Hold"]`, `if x["mode"] == "Recipe":` → `Mode.SMOKE`, etc.
- Assignments to a mode field: `control["mode"] = "Stop"`, `status["mode"] = "Error"`, `data["mode"] = "Monitor"` → `Mode.X`.
- Mode dispatch keys / mode-typed locals: a dict keyed by mode, `mode = "Smoke"`, function args that are a mode.

**DO NOT CONVERT** (incidental — the string is not a mode):
- Log/exception/UI text: `logger.info("... Stop ...")`, `"An Error occurred"`, button/label strings, `flash("...")`.
- Template files (`.html`/Jinja), JS, JSON, CSS — not touched at all.
- Config/dict keys that merely happen to spell a mode word but aren't the mode value.
- Recipe-file `step["mode"]` VALUES stay strings in the file; only Python comparisons/dispatch on them convert.
- Notification event keys (`"Grill_Error_01"`), `status` values (`"active"`/`"monitor"`/`"inactive"`), display command tuples (`("text","ERROR")`).

**Rule of thumb:** if replacing `"Smoke"` with `Mode.SMOKE` at that site would still read correctly AND the string is being used AS a mode (compared/assigned/dispatched against `control["mode"]`/`status["mode"]`/a mode variable), convert it. Otherwise leave it. When unsure, LEAVE IT — the gate can't catch an over-conversion that happens to still pass, so err toward under-converting.

---

## File Structure

```
common/modes.py                    (UNCHANGED — Mode already defined here)
common/api_commands.py + common/*.py   (MODIFIED — Task 1, ~18 sites; GOLDEN-PINNED)
notify/*.py                        (MODIFIED — Task 2, ~22 sites)
blueprints/**/*.py                 (MODIFIED — Task 3, ~20 sites)
display/*.py                       (MODIFIED — Task 4, ~110 sites)
```

Per area: add `from common.modes import Mode` to each file that gains a `Mode.X` reference.

---

## Task 1 — `common/` incl. `api_commands.py` (the mode authority) [GOLDEN-PINNED]

**Files:** `common/api_commands.py` + any other `common/*.py` with mode-semantic sites (grep first). NOT `common/modes.py`, NOT `common/defaults.py` line 460 `control["status"]=""` (that's status, not mode — but `default_control()["mode"]` at ~line 444 IS a mode site: `control["mode"] = "Stop"` → `Mode.STOP`).

- [ ] **Step 1:** `grep -nE '\["mode"\]|== "(Startup|Smoke|Hold|Monitor|Manual|Prime|Reignite|Shutdown|Stop|Error|Recipe)"' common/*.py`. Triage each hit CONVERT/LEAVE per the rule above. `api_commands.py` authors `control["mode"]` from the REST/socket API — those writes/compares convert; the notification-key and status strings do not.
- [ ] **Step 2:** Add `from common.modes import Mode` to each touched file; convert the mode-semantic sites to `Mode.X`.
- [ ] **Step 3: GOLDEN GATE.** `... uv run pytest tests/characterization/test_process_command_golden.py -q` → green with the SHA pin UNCHANGED and no regeneration (StrEnum writes serialize to the same strings). If the SHA moves, STOP — you converted a non-mode string or the golden captured a `.name`. Then run `tests/unit/common tests/characterization -q`.
- [ ] **Step 4: Commit:** `refactor(common): adopt Mode enum in api_commands + defaults`.

---

## Task 2 — `notify/` [COMMIT]

**Files:** `notify/*.py` mode-semantic sites (~22). The notification EVENT keys (`Grill_Error_*`, `Recipe_Step_Message`) are NOT modes — leave them; only `control["mode"]`/`status["mode"]` comparisons convert.

- [ ] **Step 1:** grep + triage as Task 1. Add `from common.modes import Mode`; convert.
- [ ] **Step 2:** `... uv run pytest tests/unit/notify tests/characterization -q` → green (the FSM notify characterization + goldens gate this).
- [ ] **Step 3: Commit:** `refactor(notify): adopt Mode enum at mode-comparison sites`.

---

## Task 3 — `blueprints/` (web) [COMMIT]

**Files:** `blueprints/**/*.py` mode-semantic sites (~20). Flask route Python only — **NOT** the Jinja templates (they keep string comparisons and interoperate). Mode values passed to `render_template(...)` may be plain strings from the datastore; leave template-facing context as-is unless it's a Python comparison.

- [ ] **Step 1:** grep + triage. Add `from common.modes import Mode`; convert Python comparisons/assignments only.
- [ ] **Step 2:** `... uv run pytest tests/web -q` → green (the Playwright/live_server suite renders every blueprint page).
- [ ] **Step 3: Commit:** `refactor(blueprints): adopt Mode enum at mode-comparison sites`.

---

## Task 4 — `display/` (the bulk, ~110 sites) [COMMIT — most care]

**Files:** `display/*.py` mode-semantic sites. This is the largest and least-uniformly-tested area — go file by file, triage each hit, and lean toward LEAVING anything that is a UI label / display-text string rather than a mode comparison.

- [ ] **Step 1:** For each `display/*.py`: `grep -nE '\["mode"\]|status\["mode"\]|== "(Startup|...)"'` and triage. Common real sites: reading `in_data`/`status`/`control` `["mode"]` to pick an icon/screen/color; comparing the current mode to decide rendering. Common LEAVE sites: display label text, screen names that coincidentally match, theme keys.
- [ ] **Step 2:** Add `from common.modes import Mode` per touched file; convert.
- [ ] **Step 3:** `... uv run pytest tests/ui tests/unit -k display -q` (adjust to the real display test paths — grep `tests/` for display/dsi/flex/qtquick module-load + launch tests). Confirm every registered display driver still imports and the launch/module-load tests pass.
- [ ] **Step 4: Commit:** `refactor(display): adopt Mode enum at mode-comparison sites`.

---

## Task 5 — Verification + audit [COMMIT if formatting only]

- [ ] **Step 1: Full suite** `... uv run pytest tests/ -q` → **1521**, zero assertion edits, no golden regenerated (esp. `test_process_command_golden`).
- [ ] **Step 2: Audit.** Re-grep each area for remaining bare mode literals at mode-semantic sites; every survivor should be a deliberate LEAVE (log/label/template-facing) — spot-check a sample and note the count left (silent under-conversion is acceptable and safe; over-conversion is the danger). Confirm `str(Mode.X)` behavior is relied upon nowhere incorrectly (StrEnum makes it a non-issue).
- [ ] **Step 3: ruff** the changed set; commit only if formatting changed: `style: ruff format mode-enum adoption`.

**Rollback:** revert the branch — additive imports + equal-value substitutions; a partial revert is clean.

## Note on remaining follow-ons
This completes the `Mode` adoption. Still open: `status` as a modeled second FSM dimension (needs its own design), and Phase E (Meater, parked). Templates/JS/JSON deliberately keep plain strings (they interoperate via StrEnum equality) — converting those is neither needed nor in scope.

## Self-Review
- **Scope coverage:** all 4 areas with mode-semantic sites (common/notify/blueprints/display); file_mgmt has none. ✅
- **Behavior-preserving:** StrEnum ⇒ identical serialization; SHA-golden + area suites gate every task; over-conversion guarded by the "when unsure, LEAVE" rule. ✅
- **No placeholders:** conversion is judgment-per-site (can't quote ~170 sites); the plan gives the exact CONVERT/LEAVE rule, per-area grep commands, and per-area gates — the mechanical substitution (`"X"` → `Mode.X`) is trivial; the judgment is the work. ✅
