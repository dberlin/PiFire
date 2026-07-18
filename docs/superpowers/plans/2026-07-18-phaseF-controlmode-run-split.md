# Phase F — Split `ControlMode.run()` into private helpers

**Feature Name:** Phase F — `ControlMode.run()` extraction (Tier 1 & 2 refactors)

**For agentic workers:** REQUIRED SUB-SKILL → **superpowers:subagent-driven-development**. Execute one task at a time, in order. Each task is a pure, independently-revertible extraction under a frozen golden contract; do not batch tasks or "improve" code while extracting.

**Goal:** Shrink the ~420-line `ControlMode.run()` method (`controller/runtime/modes/base.py:231-652`) into a ~130-line skeleton by extracting five private helpers, **preserving exact control-write ordering and every read/write side effect**. This is a pure structural refactor — no behavior change whatsoever.

> **NOTE (2026-07-18, post-FSM + post-enums refresh):** this plan was written against the pre-FSM `run()`. Since then, TWO refactors merged into `massive-reworks-and-new-ui`:
>
> 1. **The mode-transition FSM** — turned the switch-off block into a `request_transition(...)` seam call, and added two inline guard hooks that are NOT extraction targets and stay exactly where they are: `evaluate_phase(self, ctx, "pre_loop", ...)` (~base.py:332-337) and, in the SAFETY section, `evaluate_phase(self, ctx, "pre_act", now, ptemp)` + the residual `if self.check_safety(now, ptemp): break` (~base.py:516-529). Do not touch them.
> 2. **The Mode/TransitionKind StrEnums** — added `from common.modes import Mode` at base.py line 17, so **every line number cited below is now +1** (anchor on the unchanged `# ...` comment strings, which are the real anchors). AND every mode-string comparison in the quoted blocks is now the enum: `mode == "Manual"` → `mode == Mode.MANUAL`, `control["mode"] == "Recipe"` → `== Mode.RECIPE`, `in ["Smoke","Hold"]` → `in (Mode.SMOKE, Mode.HOLD)`, `self.name == "Smoke"` → `== Mode.SMOKE`, etc. **Extract the ACTUAL current code** — the quoted blocks below illustrate STRUCTURE; the live comparisons are enum-based and come along verbatim in the extraction (behavior-identical). The switch-off seam call is now `request_transition(ctx, control, Mode.STOP, kind=TransitionKind.TERMINAL)` (updated in Task 2's block/helper below).
>
> `base.py` already imports `request_transition, evaluate_phase` and `Mode` / `TransitionKind` — extracted helpers may reference them directly.

**Architecture:** `run()` is the shared work-cycle driver for every concrete mode handler (Smoke/Hold/Startup/Shutdown/Monitor/Manual/Reignite/Prime/Recipe/Error subclasses of `ControlMode`). It performs: pre-loop setup (process monitor, metrics, recipe triggers, timers) → a `while status == "Active"` main loop (SENSE / SAFETY / ACT / PUBLISH banners) → post-loop cleanup. The extraction lifts five self-contained blocks into `self._*` helpers called from the same positions, so the control-write sequence observed by the store is byte-for-byte unchanged.

**Tech Stack:** Python 3.14, pytest, Serena symbolic editing, ruff, uv.

## Global Constraints

Copy these verbatim into your working context; they are binding for every task.

- **Python 3.14.** `except (A, B)` is the canonical form (ruff keeps the parens off / on per repo rule — do not "fix" except-tuples).
- **TEST COMMAND (exact, always):**
  ```
  timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q
  ```
  Bare `python`/`pytest` HANGS or false-fails (no PySide6 in system python). Always go through `uv run`.
- **Before every commit:** `uvx ruff format <changed>` then `uvx ruff check <changed>`.
- **Edits — plain Read/Edit preferred.** The tasks below name Serena symbolic tools (`insert_after_symbol`/`replace_symbol_body`), but **if you are executing in a git worktree, DO NOT use Serena — it silently edits the MAIN checkout, not your worktree** (this bit Phases H, I, and the FSM). Use plain `Edit`/`Write` anchored on the quoted code blocks instead. Either way: add each helper as a sibling of `run()`, and swap each extracted block for its one-line call site. Do not hand-retype the whole method from memory — anchor on the exact quoted blocks.
- **Commit with `git commit -F <msgfile>`** (zsh eats backticks in `-m`). Co-author trailer, exactly:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```
- **BEHAVIOR-PRESERVING, frozen golden.** This is the strictest gate in the Tier 1 & 2 effort. Each task = extract **one** helper, run the three suites, confirm **all pass with zero assertion changes**, commit. One helper per commit so any regression is bisectable.
- **NO test-flip, NO golden regeneration.** Unlike the bugfix effort, the golden here is a frozen contract that must NOT move. If any assertion changes value, you have introduced a behavior change — revert and re-extract, do not touch the test.

### What "byte-identical golden output" actually means here (read before starting)

The three gating suites are **RUN-THEN-FREEZE behavioral characterizations**, not a hashed output file:

- `tests/characterization/test_modes_golden.py` (27 tests) and `tests/e2e/test_work_cycle_e2e.py` (5 tests) drive `run_mode(...)` → `controller.runtime.controller.run_work_cycle(mode, ctx)` → `_MODE_HANDLERS[mode](ctx, WorkCycleState()).run()`. These are the **direct gates** on `run()`. They assert on captured `grill_calls`, `display_commands`, `notifications`, `final_control`, `final_status`, `final_metrics`.
- `tests/characterization/test_controller_loop_golden.py` (17 tests) exercises the **outer** `Controller.run()/tick()` and **spies `work_cycle`** — it does NOT execute `ControlMode.run()`. Keep it in the command as a broad safety net, but understand it will not catch an inner-loop drift.
- There is **no `GOLDEN_SHA256` pin for `run()`.** (The only SHA-pinned golden in the repo is `test_process_command_golden.py`, which is unrelated to this method and not part of this phase's gate.)

So the pass criterion for every task is: **all 49 tests pass and no assertion literal in any of the three files was edited.** "Byte-identical" = the store sees the identical sequence of writes, so every frozen assertion still holds.

---

## File Structure

**Production file (only one is edited):**

- `controller/runtime/modes/base.py` — `ControlMode.run()` at lines **231-652** (the `# ---- shared skeleton ----` comment is on 229). Five helpers are added as siblings; `run()` is rewired to call them.

**Test files that gate every task (never edited — the frozen contract):**

- `tests/characterization/test_modes_golden.py` (27 tests) — primary inner-loop gate.
- `tests/e2e/test_work_cycle_e2e.py` (5 tests) — same scenarios against real SQLite.
- `tests/characterization/test_controller_loop_golden.py` (17 tests) — outer-loop safety net (spies `work_cycle`).
- Support (read-only): `tests/characterization/harness.py` (`run_mode`), `tests/characterization/fixtures.py`.

**Gate command (identical for every task):**
```
timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/characterization/test_modes_golden.py \
  tests/characterization/test_controller_loop_golden.py \
  tests/e2e/test_work_cycle_e2e.py -q
```
**Expected after every task:** `49 passed` (27 + 17 + 5), no failures, no assertion edits.

---

## Task 0 — Branch + baseline green

**Files:** none (setup).

**Steps:**
1. From `massive-reworks-and-new-ui`, create the phase branch:
   ```
   git switch -c refactor/controlmode-run-split
   ```
2. Establish the baseline. Run the gate command. Confirm it is green BEFORE touching anything. Expected **`49 passed`** (27 + 17 + 5) — the FSM merge left `test_modes_golden.py`, `test_controller_loop_golden.py`, and `test_work_cycle_e2e.py` byte-unchanged, so 49 should still hold; but the FSM merged AFTER this plan was written, so **confirm the actual count and freeze whatever you observe** as the baseline. If it is not green at baseline, STOP — the golden is not a stable contract and extraction cannot be verified.
3. Record the baseline count; every later task must reproduce exactly this.

**No commit** (branch creation only).

---

## Task 1 — Extract `_setup_recipe_triggers(control)`

**Files:** `controller/runtime/modes/base.py`.

**Interfaces:**
- **Produces:** `def _setup_recipe_triggers(self, control) -> None`
- **Consumes:** `control` (mutated in place), `self.name` (mode), `self.ctx` (clock + store writes). Does its own `import control as _control` for `eventLogger`.
- **Side effects (must preserve exactly):** mutates `control["notify_data"][*]` and `control["timer"]`; conditionally `ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")`; may log a warning. **No return.**

**Current block (base.py 256-285)** — pre-loop, immediately after `_control.eventLogger.info(f"{mode} Mode started.")`:
```python
        # Pre-Loop Setup Recipe Triggers
        if control["mode"] == "Recipe":
            if mode in ["Smoke", "Hold"]:
                recipe_trigger_set = False
                if control["recipe"]["step_data"]["timer"] > 0:
                    for index, item in enumerate(control["notify_data"]):
                        if item["type"] == "timer":
                            control["notify_data"][index]["req"] = True
                            timer_start = ctx.clock.now()
                            control["timer"]["start"] = timer_start
                            control["timer"]["paused"] = 0
                            control["timer"]["end"] = timer_start + (control["recipe"]["step_data"]["timer"] * 60)
                            control["timer"]["shutdown"] = False
                            control["notify_data"][index]["shutdown"] = False
                            control["notify_data"][index]["keep_warm"] = False
                            recipe_trigger_set = True

                for probe, value in control["recipe"]["step_data"]["trigger_temps"].items():
                    if value > 0:
                        for index, item in enumerate(control["notify_data"]):
                            if item["type"] == "probe" and item["label"] == probe:
                                control["notify_data"][index]["target"] = value
                                control["notify_data"][index]["req"] = True
                                recipe_trigger_set = True
                                break

                if recipe_trigger_set:
                    ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                else:
                    _control.eventLogger.warning("No trigger set for Hold/Smoke mode in recipe.")
```

**Steps:**
1. `insert_after_symbol` on `ControlMode/run` — add the new helper (uses `self.name`/`self.ctx`, its own `_control` import):
   ```python
    def _setup_recipe_triggers(self, control):
        """Pre-loop recipe trigger setup (extracted from run()). Mutates control
        in place and writes it when any trigger was set."""
        import control as _control  # module global: eventLogger

        ctx = self.ctx
        mode = self.name
        if control["mode"] == "Recipe":
            if mode in ["Smoke", "Hold"]:
                recipe_trigger_set = False
                if control["recipe"]["step_data"]["timer"] > 0:
                    for index, item in enumerate(control["notify_data"]):
                        if item["type"] == "timer":
                            control["notify_data"][index]["req"] = True
                            timer_start = ctx.clock.now()
                            control["timer"]["start"] = timer_start
                            control["timer"]["paused"] = 0
                            control["timer"]["end"] = timer_start + (control["recipe"]["step_data"]["timer"] * 60)
                            control["timer"]["shutdown"] = False
                            control["notify_data"][index]["shutdown"] = False
                            control["notify_data"][index]["keep_warm"] = False
                            recipe_trigger_set = True

                for probe, value in control["recipe"]["step_data"]["trigger_temps"].items():
                    if value > 0:
                        for index, item in enumerate(control["notify_data"]):
                            if item["type"] == "probe" and item["label"] == probe:
                                control["notify_data"][index]["target"] = value
                                control["notify_data"][index]["req"] = True
                                recipe_trigger_set = True
                                break

                if recipe_trigger_set:
                    ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                else:
                    _control.eventLogger.warning("No trigger set for Hold/Smoke mode in recipe.")
   ```
2. In `run()`, replace the entire block (the `# Pre-Loop Setup Recipe Triggers` comment through the `warning(...)` line) with the single call:
   ```python
        # Pre-Loop Setup Recipe Triggers
        self._setup_recipe_triggers(control)
   ```
3. `uvx ruff format controller/runtime/modes/base.py` then `uvx ruff check controller/runtime/modes/base.py`.
4. **Gate:** run the gate command. Expect **`49 passed`**, no assertion edits.
5. Commit:
   ```
   git add controller/runtime/modes/base.py
   git commit -F <msgfile>
   ```
   Message: `refactor(modes): extract _setup_recipe_triggers from ControlMode.run` + co-author trailer.

---

## Task 2 — Extract `_process_control_flags(control, now, last, pelletdb)`

**Files:** `controller/runtime/modes/base.py`.

**Interfaces:**
- **Produces:** `def _process_control_flags(self, control, now, last, pelletdb) -> tuple`
  returning **`(last, pelletdb, should_break)`**.
- **Consumes:** `control` (mutated in place), `now`, `last` (reassigned inside → must be returned), `pelletdb` (reassigned inside the hopper block → must be returned), `self.settings` (reassigned in the settings-update branch), `self.state.timers.hopper_toggle`, `self.grill` (input/output status), `self.dist_device`. Own `import control as _control`.
- **Side effects (must preserve exactly):** the four flag blocks in order — **settings_update → distance_update → hopper_check → switch**; conditional `ctx.store.write_control` in each; `self.on_settings_reload()`; `dist_device.update_distances`; `dist_device.get_level`; `ctx.store.write_pellet_db`; may set `control` to Stop and **request a loop break** (switch-off path).

> **Scoping note (refreshed to current code):** the extracted block is base.py **376-417** — the four flag blocks only. Lines 362-375 (`now = ...`, `execute_control_writes()`, `control = read_control()`, `self.control = control`, `process_system_commands(ctx)`, and the `if control["updated"]: break`) **stay in `run()`** because they rebind the `control` loop variable and contain the top-of-loop `updated` break; pulling them in would force the helper to also return `control`. Keeping them in the caller makes the helper mutate `control` in place only.

> **Break handling (post-FSM + enums):** the switch-off block (base.py 409-418, +1 post-enums) contains a `break`. It now performs the Stop transition via the FSM seam — `control["status"] = "active"` then `request_transition(ctx, control, Mode.STOP, kind=TransitionKind.TERMINAL)` (which sets `mode="Stop"`/`updated=True` and does the single `write_control(OVERWRITE)`) — NOT the old inline `mode=/updated=/write` triplet. A helper cannot `break` the caller's loop, so it returns a `should_break` flag; `run()` does `if should_break: break`. The pre-break status write + seam call happen inside the helper exactly as in `run()` today, so the store sees the identical write. `request_transition` is a module global in base.py — call it directly.

**Current block (base.py 376-417):**
```python
            # Check if user changed settings and reload
            if control["settings_update"]:
                control["settings_update"] = False
                ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                self.settings = ctx.store.read_settings()
                if self.settings["globals"]["debug_mode"]:
                    _control.eventLogger.setLevel(logging.DEBUG)
                else:
                    _control.eventLogger.setLevel(logging.INFO)
                self.on_settings_reload()

            # Check if user changed hopper levels and update if required
            if control["distance_update"]:
                empty = self.settings["pelletlevel"]["empty"]
                full = self.settings["pelletlevel"]["full"]
                dist_device.update_distances(empty, full)
                control["distance_update"] = False
                ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")

            # Check hopper level when requested or every 300 seconds
            if control["hopper_check"] or (now - self.state.timers.hopper_toggle) > 60:
                pelletdb = ctx.store.read_pellet_db()
                override = False
                if control["hopper_check"]:
                    control["hopper_check"] = False
                    ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                    override = True
                pelletdb["current"]["hopper_level"] = dist_device.get_level(override=override)
                ctx.store.write_pellet_db(pelletdb)
                self.state.timers.hopper_toggle = now
                _control.eventLogger.info("Hopper Level Checked @ " + str(pelletdb["current"]["hopper_level"]) + "%")

            # Check for update in ON/OFF Switch
            if not self.settings["platform"]["standalone"] and last != grill_platform.get_input_status():
                last = grill_platform.get_input_status()
                if not last:
                    _control.eventLogger.info("Switch set to off, going to monitor mode.")
                    # The seam sets mode="Stop"/updated + writes; status is not part
                    # of the transition, so set it on control first (single OVERWRITE).
                    control["status"] = "active"
                    request_transition(ctx, control, Mode.STOP, kind=TransitionKind.TERMINAL)
                    break
```

**Steps:**
1. `insert_after_symbol` on `ControlMode/run` — add the helper (note the `return (last, pelletdb, True)` in place of the switch-off `break`, and the final `return (last, pelletdb, False)` fall-through):
   ```python
    def _process_control_flags(self, control, now, last, pelletdb):
        """Per-tick settings/distance/hopper/switch flag handling (extracted from
        run()). Mutates control in place; returns (last, pelletdb, should_break)."""
        import control as _control  # module global: eventLogger

        ctx = self.ctx
        grill_platform = self.grill
        dist_device = self.dist_device

        # Check if user changed settings and reload
        if control["settings_update"]:
            control["settings_update"] = False
            ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
            self.settings = ctx.store.read_settings()
            if self.settings["globals"]["debug_mode"]:
                _control.eventLogger.setLevel(logging.DEBUG)
            else:
                _control.eventLogger.setLevel(logging.INFO)
            self.on_settings_reload()

        # Check if user changed hopper levels and update if required
        if control["distance_update"]:
            empty = self.settings["pelletlevel"]["empty"]
            full = self.settings["pelletlevel"]["full"]
            dist_device.update_distances(empty, full)
            control["distance_update"] = False
            ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")

        # Check hopper level when requested or every 300 seconds
        if control["hopper_check"] or (now - self.state.timers.hopper_toggle) > 60:
            pelletdb = ctx.store.read_pellet_db()
            override = False
            if control["hopper_check"]:
                control["hopper_check"] = False
                ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                override = True
            pelletdb["current"]["hopper_level"] = dist_device.get_level(override=override)
            ctx.store.write_pellet_db(pelletdb)
            self.state.timers.hopper_toggle = now
            _control.eventLogger.info("Hopper Level Checked @ " + str(pelletdb["current"]["hopper_level"]) + "%")

        # Check for update in ON/OFF Switch
        if not self.settings["platform"]["standalone"] and last != grill_platform.get_input_status():
            last = grill_platform.get_input_status()
            if not last:
                _control.eventLogger.info("Switch set to off, going to monitor mode.")
                # The seam sets mode="Stop"/updated + writes; status is not part
                # of the transition, so set it on control first (single OVERWRITE).
                control["status"] = "active"
                request_transition(ctx, control, Mode.STOP, kind=TransitionKind.TERMINAL)
                return (last, pelletdb, True)

        return (last, pelletdb, False)
   ```
2. In `run()`, replace the four-block region (from `# Check if user changed settings and reload` through the switch-off `break`) with the call + break check:
   ```python
            # Per-tick settings/distance/hopper/switch flag handling
            last, pelletdb, _should_break = self._process_control_flags(control, now, last, pelletdb)
            if _should_break:
                break
   ```
3. `uvx ruff format` + `uvx ruff check` on the file.
4. **Gate:** run the gate command. Expect **`49 passed`**, no assertion edits. (Scenario coverage: `test_tick_switch_off_triggers_stop`, `test_tick_hopper_check_reads_and_clears`, `test_tick_distance_update...`, `test_tick_settings_update...` in the loop suite exercise the outer analogue; the modes suite exercises the in-loop pelletdb threading.)
5. Commit: `refactor(modes): extract _process_control_flags from ControlMode.run` + co-author trailer.

---

## Task 3 — Extract `_apply_manual_overrides(control, now, current_output_status)`

**Files:** `controller/runtime/modes/base.py`.

**Interfaces:**
- **Produces:** `def _apply_manual_overrides(self, control, now, current_output_status) -> None`
- **Consumes:** `control` (mutated in place), `now`, `current_output_status`, `self.name` (mode), `self.settings`, `self.grill`, `self.state.manual_override` (mutated in place — same dict object seeded at base.py 359-360, so no return needed). Own `import control as _control`.
- **Side effects (must preserve exactly):** the fan/auger/igniter/power/pwm actuation order; `self.state.manual_override[*]` timestamp writes; `control["manual"]["pwm"] = 100` reset; final `control["manual"]["change"]=False` / `["output"]=False` + `ctx.store.write_control`. **No return.**

> **`manual_override` note:** base.py 359-360 stays in `run()` (`manual_override = {...}; self.state.manual_override = manual_override`). The helper reads/writes `self.state.manual_override` — the identical dict object — so in-place mutation is faithful and nothing needs threading back.

**Current block (base.py 421-482):** the `if mode == "Manual" or self.settings["safety"]["allow_manual_changes"]:` block through `ctx.store.write_control(...)`. (Full body captured in live code; extract it verbatim.)

**Steps:**
1. `insert_after_symbol` on `ControlMode/run` — add the helper. Body is the verbatim 421-482 block with these substitutions only: `mode` → `self.name`, `grill_platform` → `self.grill`, `manual_override` → `self.state.manual_override`, and a leading `import control as _control` / `ctx = self.ctx`:
   ```python
    def _apply_manual_overrides(self, control, now, current_output_status):
        """Per-tick manual output overrides (extracted from run()). Mutates control
        and self.state.manual_override in place."""
        import control as _control  # module global: eventLogger

        ctx = self.ctx
        mode = self.name
        grill_platform = self.grill
        manual_override = self.state.manual_override

        if mode == "Manual" or self.settings["safety"]["allow_manual_changes"]:
            if control["manual"]["change"] in ["power", "igniter", "fan", "auger", "pwm"]:
                if mode != "Manual":
                    override_time = now + self.settings["safety"]["manual_override_time"]
                else:
                    override_time = 0

                if control["manual"]["change"] == "fan":
                    if control["manual"]["output"] and not current_output_status["fan"]:
                        grill_platform.fan_on()
                        _control.eventLogger.debug("Fan ON")
                    elif not control["manual"]["output"] and current_output_status["fan"]:
                        grill_platform.fan_off()
                        _control.eventLogger.debug("Fan OFF")
                    manual_override["fan"] = override_time

                if control["manual"]["change"] == "auger":
                    if control["manual"]["output"] and not current_output_status["auger"]:
                        grill_platform.auger_on()
                        _control.eventLogger.debug("Auger ON")
                    elif not control["manual"]["output"] and current_output_status["auger"]:
                        grill_platform.auger_off()
                        _control.eventLogger.debug("Auger OFF")
                    manual_override["auger"] = override_time

                if control["manual"]["change"] == "igniter":
                    if control["manual"]["output"] and not current_output_status["igniter"]:
                        grill_platform.igniter_on()
                        _control.eventLogger.debug("Igniter ON")
                    elif not control["manual"]["output"] and current_output_status["igniter"]:
                        grill_platform.igniter_off()
                        _control.eventLogger.debug("Igniter OFF")
                    manual_override["igniter"] = override_time

                if control["manual"]["change"] == "power":
                    if control["manual"]["output"] and not current_output_status["power"]:
                        grill_platform.power_on()
                        _control.eventLogger.debug("Power ON")
                    elif not control["manual"]["output"] and current_output_status["power"]:
                        grill_platform.power_off()
                        _control.eventLogger.debug("Power OFF")
                    manual_override["power"] = override_time

                if (
                    self.settings["platform"]["dc_fan"]
                    and control["manual"]["change"] == "pwm"
                    and current_output_status["fan"]
                    and not control["manual"]["pwm"] == current_output_status["pwm"]
                ):
                    speed = control["manual"]["pwm"]
                    _control.eventLogger.debug("PWM Speed: " + str(speed) + "%")
                    grill_platform.set_duty_cycle(speed)
                    manual_override["pwm"] = override_time
                    control["manual"]["pwm"] = 100  # Reset PWM

                # Reset to False (not None) to match default_control()'s seed and
                # keep control free of dict-nested nulls: every consumer treats
                # these as falsy (== 'pwm', `in [...]`, truthiness), so behavior is
                # identical, and a null here would be a delete under json_patch merge.
                control["manual"]["change"] = False
                control["manual"]["output"] = False
                ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
   ```
2. In `run()`, replace the whole 421-482 block with the call (it sits right after `current_output_status = grill_platform.get_output_status()` at base.py:419):
   ```python
            self._apply_manual_overrides(control, now, current_output_status)
   ```
3. `uvx ruff format` + `uvx ruff check`.
4. **Gate:** run the gate command. Expect **`49 passed`**, no assertion edits. (Manual-mode scenarios in `test_modes_golden.py` cover fan/auger/igniter/power/pwm actuation + override timestamps.)
5. Commit: `refactor(modes): extract _apply_manual_overrides from ControlMode.run` + co-author trailer.

---

## Task 4 — Extract `_build_status_data(control, pelletdb, start_time) -> dict`

**Files:** `controller/runtime/modes/base.py`.

**Interfaces:**
- **Produces:** `def _build_status_data(self, control, pelletdb, start_time) -> dict` — builds and returns a **fresh** status dict.
- **Consumes:** `control`, `pelletdb`, `start_time`, `self.name` (mode), `self.settings`, `self.state` (startup.timer, metrics, cycle.ratio), `self.grill` (output status), `self.status_fragment()`.
- **Returns:** the fully-populated `status_data` dict (mode-specific fields merged via `status_fragment()`).

> **Fresh-dict faithfulness:** the original block (base.py 553-591) overwrites **every** key of `status_data` each pass and never reads a prior value, so returning a fresh dict and reassigning `status_data = self._build_status_data(...)` is behaviorally identical to the in-place population. The `ctx.store.write_status(status_data)` and `self.state.timers.display_toggle = ctx.clock.now()` calls (base.py 592-593) **stay in `run()`** after the call, and `status_data` remains the persistent loop local so the post-loop `if status_data != {}` check (base.py 648) is unaffected. The `status_data = {}` init at base.py 355 stays.

**Current block (base.py 553-591):** everything from `status_data["notify_data"] = control["notify_data"]` through `status_data.update(self.status_fragment())` (i.e. inside `if (now - self.state.timers.display_toggle) > 0.5:` at base.py:551, up to but NOT including `ctx.store.write_status(status_data)`).

**Steps:**
1. `insert_after_symbol` on `ControlMode/run` — add the helper. It opens a fresh `status_data = {}`, then the verbatim 553-591 body with `mode` → `self.name` and `grill_platform` → `self.grill`, and `return status_data`:
   ```python
    def _build_status_data(self, control, pelletdb, start_time):
        """Build the per-0.5s display status dict (extracted from run()). Returns a
        fresh, fully-populated dict; the caller writes it to the store."""
        mode = self.name
        grill_platform = self.grill
        status_data = {}
        status_data["notify_data"] = control["notify_data"]
        status_data["timer"] = control["timer"]
        status_data["s_plus"] = control["s_plus"]
        status_data["hopper_level_enabled"] = False if self.settings["modules"]["dist"] == "none" else True
        status_data["hopper_level"] = pelletdb["current"]["hopper_level"]
        status_data["units"] = self.settings["globals"]["units"]
        status_data["mode"] = mode
        status_data["recipe"] = True if control["mode"] == "Recipe" else False
        status_data["start_time"] = start_time
        status_data["start_duration"] = self.state.startup.timer
        status_data["shutdown_duration"] = self.settings["shutdown"]["shutdown_duration"]
        status_data["prime_duration"] = 0
        status_data["prime_amount"] = 0
        status_data["lid_open_detected"] = False
        status_data["lid_open_endtime"] = 0
        status_data["p_mode"] = self.state.metrics.get("p_mode", None)
        status_data["startup_timestamp"] = control["startup_timestamp"]
        if control["mode"] == "Recipe":
            status_data["recipe_paused"] = (
                True
                if control["recipe"]["step_data"]["triggered"] and control["recipe"]["step_data"]["pause"]
                else False
            )
        else:
            status_data["recipe_paused"] = False
        status_data["outpins"] = {}
        current = grill_platform.get_output_status()
        for item in self.settings["platform"]["outputs"]:
            try:
                status_data["outpins"][item] = current[item]
            except KeyError:
                continue
        status_data["cycle_ratio"] = round(self.state.cycle.ratio, 2)
        if self.settings["platform"].get("dc_fan"):
            status_data["fan_duty"] = int(control.get("duty_cycle", 0) or 0)
        else:
            status_data["fan_duty"] = 100 if status_data["outpins"].get("fan") else 0
        # ---- mode-specific status fields ----
        status_data.update(self.status_fragment())
        return status_data
   ```
2. In `run()`, replace the 553-591 body with a single assignment, leaving the write + toggle in place:
   ```python
            # Send Current Status / Temperature Data to Display Device every 0.5 second
            if (now - self.state.timers.display_toggle) > 0.5:
                status_data = self._build_status_data(control, pelletdb, start_time)
                ctx.store.write_status(status_data)
                self.state.timers.display_toggle = ctx.clock.now()
   ```
3. `uvx ruff format` + `uvx ruff check`.
4. **Gate:** run the gate command. Expect **`49 passed`**, no assertion edits. (`final_status` assertions in `test_modes_golden.py` + the JSON-clean status assertions in the e2e suite cover this.)
5. Commit: `refactor(modes): extract _build_status_data from ControlMode.run` + co-author trailer.

---

## Task 5 — Extract `_handle_recipe_end(control) -> bool`

**Files:** `controller/runtime/modes/base.py`.

**Interfaces:**
- **Produces:** `def _handle_recipe_end(self, control) -> bool` — returns `True` when the loop must `break`, else `False`.
- **Consumes:** `control` (mutated in place: the pause-branch clears `["recipe"]["step_data"]["notify"]`), `self.ctx` (notifications + store write).
- **Side effects (must preserve exactly):** `ctx.notifications.send("Recipe_Step_Message")` in both triggered branches; in the paused branch, `notify=False` + `ctx.store.write_control`. Returns `True` only in the triggered-and-not-paused branch (the original `break`).

> **Break handling:** original base.py 606-617 has a `break` at 611 (triggered & not paused). The helper returns `True` there; the paused branch and the non-Recipe fall-through return `False`. `run()` does `if self._handle_recipe_end(control): break`.

**Current block (base.py 606-617):**
```python
            # End of Loop Recipe Check
            if control["mode"] == "Recipe":
                if control["recipe"]["step_data"]["triggered"] and not control["recipe"]["step_data"]["pause"]:
                    if control["recipe"]["step_data"]["notify"]:
                        ctx.notifications.send("Recipe_Step_Message")
                    break
                elif control["recipe"]["step_data"]["triggered"] and control["recipe"]["step_data"]["pause"]:
                    if control["recipe"]["step_data"]["notify"]:
                        ctx.notifications.send("Recipe_Step_Message")
                        control["recipe"]["step_data"]["notify"] = False
                        ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                    # Continue until 'pause' variable is cleared
```

**Steps:**
1. `insert_after_symbol` on `ControlMode/run` — add the helper (exact expressions preserved; `break` → `return True`, fall-through → `return False`):
   ```python
    def _handle_recipe_end(self, control):
        """End-of-loop recipe step check (extracted from run()). Returns True when
        the work loop must break."""
        ctx = self.ctx
        if control["mode"] == "Recipe":
            if control["recipe"]["step_data"]["triggered"] and not control["recipe"]["step_data"]["pause"]:
                if control["recipe"]["step_data"]["notify"]:
                    ctx.notifications.send("Recipe_Step_Message")
                return True
            elif control["recipe"]["step_data"]["triggered"] and control["recipe"]["step_data"]["pause"]:
                if control["recipe"]["step_data"]["notify"]:
                    ctx.notifications.send("Recipe_Step_Message")
                    control["recipe"]["step_data"]["notify"] = False
                    ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                # Continue until 'pause' variable is cleared
        return False
   ```
2. In `run()`, replace the 606-617 block with:
   ```python
            # End of Loop Recipe Check
            if self._handle_recipe_end(control):
                break
   ```
3. `uvx ruff format` + `uvx ruff check`.
4. **Gate:** run the gate command. Expect **`49 passed`**, no assertion edits. (Recipe-step scenarios in `test_modes_golden.py` cover both triggered/paused branches.)
5. Commit: `refactor(modes): extract _handle_recipe_end from ControlMode.run` + co-author trailer.

---

## Task 6 — Verify skeleton + final full-suite gate

**Files:** `controller/runtime/modes/base.py` (read-only review; no functional change).

**Steps:**
1. Read the post-extraction `run()`. Confirm it is now a **~130-line skeleton**: pre-loop setup → `while status == "Active"` with the SENSE/SAFETY/ACT/PUBLISH banners intact and the five extracted blocks now single-line `self._*` calls → post-loop cleanup. The SAFETY section still contains the FSM's inline `if evaluate_phase(self, ctx, "pre_act", now, ptemp): break` and the residual `if self.check_safety(now, ptemp): break` — these are NOT extracted and must remain inline; likewise the pre-loop `if evaluate_phase(self, ctx, "pre_loop", start_time, ptemp): ...` stays. Confirm the five helpers sit as siblings immediately after `run()`.
2. Confirm **control-write ordering is unchanged** by eye: every `ctx.store.write_control(..., origin="control")` that existed in the original still fires from the same logical position (now inside the helpers), and no new writes were introduced. The `WriteKind` import and `logging` import are still used (helper 2 uses `logging.DEBUG/INFO`; all helpers use `WriteKind.OVERWRITE`).
3. Optional cosmetic-only cleanup: if `ctx` / `grill_platform` / etc. aliases at the top of `run()` are now unused after extraction, ruff-check will flag them (`F841`). Only remove aliases ruff reports as unused — do not touch anything ruff does not flag. (Expected still-used in `run()`: `ctx`, `mode`, `grill_platform`, `probe_complex`, `_control`, `monitor`, `start_time`, `status_data`, `in_data`, `pelletdb`, `last`, `control`.)
4. `uvx ruff format controller/runtime/modes/base.py` + `uvx ruff check controller/runtime/modes/base.py` → clean.
5. **Final gate — the three golden suites plus a full modes-package run:**
   ```
   timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
     tests/characterization/test_modes_golden.py \
     tests/characterization/test_controller_loop_golden.py \
     tests/e2e/test_work_cycle_e2e.py -q
   ```
   Expect **`49 passed`**. Then run the broader controller/modes suite as a belt-and-suspenders check:
   ```
   timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
     tests/characterization/ tests/e2e/ -q
   ```
   This now also covers the FSM's sibling characterization suites — `test_mode_transitions.py`, `test_outer_transitions.py`, `test_guard_engine.py`, `test_request_transition.py` — which exercise `run()`'s safety/switch-off/recipe paths (the ones Tasks 2 & 5 touch). Expect all green with **zero assertion edits** anywhere.
6. If (and only if) a cosmetic alias cleanup was made in step 3, commit it: `refactor(modes): drop now-unused run() locals after helper extraction` + co-author trailer. Otherwise no commit (verification only).

---

## Self-Review Checklist (complete before declaring the phase done)

- [ ] **Spec coverage:** all five spec helpers extracted — `_setup_recipe_triggers` (T1), `_process_control_flags` (T2), `_apply_manual_overrides` (T3), `_build_status_data` (T4), `_handle_recipe_end` (T5) — plus the skeleton-readability verification (T6). ✔
- [ ] **Placeholder scan:** no `...`, `TODO`, `pass  # fill in`, or `<block>` left in any helper body; every helper contains real, verbatim-lifted code. ✔
- [ ] **Signature/type consistency across tasks:**
  - `_setup_recipe_triggers(self, control) -> None` (mutate + write, no return). ✔
  - `_process_control_flags(self, control, now, last, pelletdb) -> (last, pelletdb, should_break)` — 3-tuple; caller reassigns `last, pelletdb` and breaks on the flag. ✔
  - `_apply_manual_overrides(self, control, now, current_output_status) -> None` — mutates `self.state.manual_override` (same object seeded in `run()`), no return. ✔
  - `_build_status_data(self, control, pelletdb, start_time) -> dict` — fresh dict; caller does `status_data = ...` then `write_status` + toggle. ✔
  - `_handle_recipe_end(self, control) -> bool` — `True` ⇒ break. ✔
- [ ] **Break-in-loop faithfulness:** the two extracted blocks that contained `break` (switch-off in T2, recipe-triggered in T5) return a bool the caller acts on; the pre-break writes/mutations happen inside the helper in the original order. ✔
- [ ] **Loop-local threading:** `last` and `pelletdb` (reassigned inside T2) are returned and rebound in `run()`; `self.settings` reassignment (T2) and `self.state.*` mutations (T3) travel through `self`, so no stale copies. ✔
- [ ] **Write ordering:** every `write_control`/`write_pellet_db`/`write_status`/`write_generic_key` fires from the identical logical position; no writes added or removed. ✔
- [ ] **Frozen golden honored:** no assertion literal edited in `test_modes_golden.py`, `test_controller_loop_golden.py`, or `test_work_cycle_e2e.py`; no golden file regenerated; `49 passed` reproduced after every task. ✔
- [ ] **Per-task hygiene:** `uvx ruff format` + `uvx ruff check` clean before each commit; each commit is one helper; commit messages carry the Co-Authored-By trailer via `-F`. ✔
