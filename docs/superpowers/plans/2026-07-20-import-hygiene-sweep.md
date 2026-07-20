# Import Hygiene Sweep (F401/F841 + star imports) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the repo's unused imports (F401) and unused locals (F841), and replace all 5 wildcard `import *` statements (F403/F405) with explicit imports — behavior-preserving — then keep `ruff check` clean of these codes going forward.

**Architecture:** Two independent workstreams on one branch. (A) A mostly-mechanical F401/F841 sweep: apply ruff's *safe* autofixes in one reviewed pass, then hand-triage the ~35 unsafe ones (re-exports, side-effecting RHS). (B) A per-site star-import elimination: for each of the 5 `import *` sites, enumerate the names actually used and replace the wildcard with an explicit import list; the third-party hardware one (`bluepy.btle` in `probes/bt_ibbq.py`) is highest-risk and handled last/most carefully.

**Tech Stack:** Python 3.14, ruff (via `uvx ruff`), pytest, uv. Current counts (baseline at `massive-reworks-and-new-ui` @ `bc4f71a`): **F401 = 78, F841 = 35, F403 = 5, F405 = 20**; 60 F401/F841 are ruff-`--fix`-safe, 35 are `--unsafe-fixes`.

## Global Constraints

- Branch: `refactor/import-hygiene` off `massive-reworks-and-new-ui`. Behavior-preserving — no runtime behavior change; this is import/lint hygiene only.
- Interpreter for tests: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q` (bare `python` gives false failures). Baseline suite: **1570 passed**.
- `uvx ruff format` changed files before every commit; commit with `git commit -F <file>` (zsh eats backticks in `-m`).
- **Never auto-strip a re-export.** An "unused" import in a package `__init__.py`, a shim/compat module, or anything listed in an `__all__` is a public re-export — removing it breaks importers. Review every autofix diff before committing; when in doubt, keep the import and add `# noqa: F401` with a one-word reason.
- **Never let an F841 "unsafe fix" delete a right-hand side with side effects** (a call that must still run). Unused *result* → drop the binding, keep the call: `foo()` not `_ = foo()` removed entirely. Verify each.
- Do NOT change `ruff.toml`'s line-length; this plan only affects code and (optionally, final task) the enforced lint select.
- After each task: run the full suite; it must stay at 1570 (or higher if a task legitimately adds a test).

---

### Task 1: Safe F401/F841 autofix pass (the 60 ruff-safe fixes)

**Files:** many across `blueprints common controller display file_mgmt grillplat notify probes tests` (ruff picks them); no manual per-file list — the deliverable is "ruff's safe autofixes, reviewed".

**Interfaces:**
- Produces: a smaller F401/F841 count (only the ~35 unsafe + any re-export exclusions remain) for Task 2.

- [ ] **Step 1: Branch**

```bash
git checkout massive-reworks-and-new-ui && git pull --ff-only
git checkout -b refactor/import-hygiene
```

- [ ] **Step 2: Snapshot the baseline count**

```bash
uvx ruff check . --select F401,F841 --statistics
```
Expected: `78 F401`, `35 F841` (record exact numbers).

- [ ] **Step 3: Apply ONLY the safe autofixes**

```bash
uvx ruff check . --select F401,F841 --fix        # safe fixes only (NOT --unsafe-fixes)
```

- [ ] **Step 4: Review the diff for re-export removals BEFORE trusting it**

```bash
git diff --stat
git diff -- '**/__init__.py'                      # package re-exports: MUST be scrutinized
git diff -- display/base_fixed.py display/*shim* 2>/dev/null   # display Phase-B/C shims re-export names
```
For any removed import in an `__init__.py`, a shim/compat module, or a name that other modules import from this one (`grep -rn "from <thismodule> import <name>"`): **revert that specific removal** and add `# noqa: F401  # public re-export`. Behind-the-scenes side-effect imports (e.g. registering a driver by import) must also be kept + `# noqa`.

- [ ] **Step 5: Run the full suite**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```
Expected: 1570 passed. An ImportError here means a re-export was wrongly stripped — fix per Step 4.

- [ ] **Step 6: `uvx ruff format` changed files, then commit**

```bash
git commit -F <msgfile>   # "style: remove unused imports/vars (ruff safe autofixes)"
```

---

### Task 2: Hand-triage the remaining (unsafe) F401/F841

**Files:** whatever `uvx ruff check . --select F401,F841` still reports after Task 1 (the ~35 unsafe + any Step-4 exclusions).

**Interfaces:**
- Produces: `uvx ruff check . --select F401,F841` reporting zero *actionable* findings (only intentional `# noqa`-tagged re-exports remain).

- [ ] **Step 1: List what remains**

```bash
uvx ruff check . --select F401,F841 --output-format=concise
```

- [ ] **Step 2: Triage each, one of three dispositions**

For each finding decide:
- **Genuinely dead** → remove it (unused var: delete the binding; if the RHS is a bare name/literal, delete the line; if the RHS is a CALL that must still run for side effects, keep the call, drop the assignment).
- **Intentional** (re-export, `__future__`, TYPE_CHECKING-only, side-effect import) → keep + `# noqa: F401`/`F841` with a one-word reason.
- **Latent bug** (an assigned-but-unused var that reveals a real logic gap — e.g. a computed value that was *supposed* to be used) → do NOT silently delete; note it and flag for the reviewer; fix only if trivial and covered by a test.

Known clusters to check first (highest counts): `controller/update_ml.py` (6), `controller/fuzzy.py` (4), `probes/bt_ibbq.py` (3 — coordinate with Task 7), `display/ili9341f.py` (3), `blueprints/recipes/routes.py` (3).

- [ ] **Step 3: Full suite** — `... uv run pytest tests/ -q` → 1570 passed.

- [ ] **Step 4: `uvx ruff format`, commit** — "style: resolve remaining unused imports/vars (manual triage)". In the commit body, list any Step-2 "latent bug" items deferred.

---

### Task 3: Star import — `common/process_mon.py` (1 dependent name; lowest risk, do first)

**Files:** Modify `common/process_mon.py:33`.

**Interfaces:**
- Consumes: `notify.notifications` public names.
- Produces: explicit import replacing `from notify.notifications import *`.

- [ ] **Step 1: Find the names actually used from the star**

```bash
uvx ruff check common/process_mon.py --select F405 --output-format=concise   # the used-from-star names
```
Cross-check by reading `common/process_mon.py` for references and confirming each name is defined in `notify/notifications.py` (`grep -nE "^(def|class) |^[A-Z_]+ *=" notify/notifications.py`).

- [ ] **Step 2: Replace the wildcard**

Change `from notify.notifications import *` → `from notify.notifications import <name1>, <name2>, ...` (exactly the used set).

- [ ] **Step 3: Verify import + behavior**

```bash
uvx ruff check common/process_mon.py --select F403,F405     # expect: no findings
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python -c "import common.process_mon"
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```
Expected: no F403/F405, import succeeds, 1570 passed.

- [ ] **Step 4: `uvx ruff format`, commit** — "refactor(process_mon): replace notifications wildcard import with explicit names".

---

### Task 4: Star import — `blueprints/tuner/routes.py` (`from .tuner import *`, 3 names)

**Files:** Modify `blueprints/tuner/routes.py:14`.

**Interfaces:**
- Consumes: `blueprints/tuner/tuner.py` (defines `calc_shh_coefficients`, `calc_shh_chart`, `temp_to_tr`, `tr_to_temp`, `calc_auto_tune_status`).
- Produces: explicit import of exactly the names `tuner_page` uses.

- [ ] **Step 1: Determine the used set** — `uvx ruff check blueprints/tuner/routes.py --select F405` plus `grep -nE "calc_shh_coefficients|calc_shh_chart|temp_to_tr|tr_to_temp|calc_auto_tune_status" blueprints/tuner/routes.py`. Import exactly those referenced.
- [ ] **Step 2: Replace** `from .tuner import *` → `from .tuner import <used names>`.
- [ ] **Step 3: Verify** — `uvx ruff check blueprints/tuner/routes.py --select F403,F405` (clean); `... uv run pytest tests/web/test_page_tuner.py tests/unit/tuner -q` then full suite → 1570.
- [ ] **Step 4: `uvx ruff format`, commit** — "refactor(tuner): explicit imports from sibling tuner module".

---

### Task 5: Star import — `blueprints/wizard/routes.py` (`from .wizard import *`, 6 names)

**Files:** Modify `blueprints/wizard/routes.py:28`.

**Interfaces:**
- Consumes: `blueprints/wizard/wizard.py` (`parse_bt_device_info`, `get_settings_dependencies_values`, `wizardInstallInfoDefaults`, `wizardInstallInfoExisting`, `prepare_wizard_data`, `wizard_bus_kinds`).

- [ ] **Step 1: Determine the used set** — `uvx ruff check blueprints/wizard/routes.py --select F405` + grep the route for each `wizard.py` public name. **Watch the safety mocks:** `tests/web/test_page_wizard.py` patches discovery names at `blueprints.wizard.routes.<name>`; those names come from `common.i2c_bus`/`common.usb_serial`/`common.app`, NOT the sibling `import *`, so this change does not affect them — but re-run the wizard tests to confirm the mocks stay armed (os.system never really runs).
- [ ] **Step 2: Replace** `from .wizard import *` → explicit list.
- [ ] **Step 3: Verify** — `uvx ruff check blueprints/wizard/routes.py --select F403,F405` (clean); `... uv run pytest tests/web/test_page_wizard.py tests/web/test_wizard_* tests/unit/wizard -q` (confirm os.system-never-ran assertions still hold) then full suite → 1570.
- [ ] **Step 4: `uvx ruff format`, commit** — "refactor(wizard): explicit imports from sibling wizard module".

---

### Task 6: Star import — `display/base_flex.py` (`from display.flexobject import *`, 3 names)

**Files:** Modify `display/base_flex.py:23`.

**Interfaces:**
- Consumes: `display/flexobject.py` public names (widget classes / `FlexObject_TypeMap`).

- [ ] **Step 1: Determine the used set** — `uvx ruff check display/base_flex.py --select F405` + grep. Note `flexobject.py` may define an `__all__` or many widget classes; import exactly what `base_flex.py` references (likely `FlexObject`, the type map, and specific widgets).
- [ ] **Step 2: Replace** the wildcard with the explicit list.
- [ ] **Step 3: Verify** — `uvx ruff check display/base_flex.py --select F403,F405` (clean); the display code is import-heavy and hardware-adjacent — at minimum `... uv run python -c "import display.base_flex"` and `... uv run pytest tests/unit tests/ui -q` (ui may skip without chromium; note it) then full suite → 1570.
- [ ] **Step 4: `uvx ruff format`, commit** — "refactor(display): explicit imports from flexobject in base_flex".

---

### Task 7: Star import — `probes/bt_ibbq.py` (`from bluepy.btle import *`, THIRD-PARTY, 7 names; highest risk)

**Files:** Modify `probes/bt_ibbq.py:51`.

**Interfaces:**
- Consumes: `bluepy.btle` (third-party Bluetooth LE lib, importable only where `bluepy` is installed — hardware/BT boxes, likely NOT the dev/test env).

- [ ] **Step 1: Enumerate the used names** — `uvx ruff check probes/bt_ibbq.py --select F405` gives the 7 names used from the star; also grep the file for any other `btle.`-style or bare references that resolve to `bluepy.btle` (e.g. `Peripheral`, `DefaultDelegate`, `BTLEDisconnectError`, `ADDR_TYPE_PUBLIC`, `Scanner`, exception classes). Build the complete used set by reading the file, not just the F405 list.
- [ ] **Step 2: Confirm `bluepy` is not importable in this env** — `uv run python -c "import bluepy.btle"` will likely fail (no hardware lib). This means you CANNOT runtime-verify the import here; rely on a static, exhaustive name enumeration from Step 1 and a `python -m pyflakes`/`ruff` static check.
- [ ] **Step 3: Replace** `from bluepy.btle import *` → `from bluepy.btle import <complete used set>`. If any used name is ambiguous or you cannot confirm it exists in `bluepy.btle` without importing, STOP and report — do not guess a third-party API surface.
- [ ] **Step 4: Verify statically** — `uvx ruff check probes/bt_ibbq.py --select F403,F405` (expect clean — F405 gone means every previously-star name is now explicitly imported); `uvx ruff check probes/bt_ibbq.py` (no new F821 undefined-name). Full suite → 1570 (this module is import-guarded/hardware-only, so the suite won't import it; note that runtime verification requires a BT box).
- [ ] **Step 5: `uvx ruff format`, commit** — "refactor(bt_ibbq): explicit imports from bluepy.btle (was wildcard)". Flag in the commit body that runtime verification needs hardware.

---

### Task 8: Confirm clean + document the standard

**Files:** Modify `docs/coverage/README.md` (or create `docs/lint.md`); optionally `ruff.toml`.

- [ ] **Step 1: Prove the sweep is complete**

```bash
uvx ruff check . --select F401,F841,F403,F405 --statistics
```
Expected: zero (or only intentional `# noqa`-tagged re-exports).

- [ ] **Step 2: Decide enforcement** — the default ruff select already reports these; document `uvx ruff check .` as a pre-merge step so they don't regress. (Optional: add an explicit `[tool.ruff.lint] select = ["E4","E7","E9","F"]` to `ruff.toml` to pin the pyflakes set so a future ruff default change can't silently drop them.)

- [ ] **Step 3: Full suite → 1570; `uvx ruff format`; commit** — "docs: document import-hygiene lint gate".

---

## Verification

- `uvx ruff check . --select F401,F841,F403,F405` reports zero actionable findings (only intentional `# noqa` re-exports remain).
- Full suite `1570 passed` after every task (no behavior change).
- Each star-import site: `--select F403,F405` clean AND the consuming module still imports (`python -c "import <module>"`), except `probes/bt_ibbq.py` which is statically verified only (hardware lib).
- `git log` shows one commit per logical unit; no re-export was silently stripped (spot-check `__init__.py` and shim diffs).

## Self-Review

- **Spec coverage:** F401 + F841 (Tasks 1-2), all 5 star imports individually (Tasks 3-7), regression gate (Task 8). Covers the ask.
- **Placeholder scan:** name sets in Tasks 3-7 are derived by a concrete command (`--select F405`) + grep rather than guessed; the sibling-module public names are enumerated from the real files. No unconstrained TODOs.
- **Risk ordering:** lowest-risk star import first (`process_mon`, 1 name), highest last (`bt_ibbq`, third-party hardware lib, static-only verification). The re-export and side-effecting-RHS traps are called out as global constraints and in Task 1 Step 4 / Task 2 Step 2.
- **Behavior preservation:** every task is import/lint-only; the wizard task explicitly re-checks that its safety mocks stay armed since it touches `blueprints/wizard/routes.py`.
