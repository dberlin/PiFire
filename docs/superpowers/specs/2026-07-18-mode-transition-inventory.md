# PiFire Controller — Implicit State-Machine Transition Inventory

Ground-truth read of the mode-transition logic as it exists on branch
`refactor/common-split`. All line numbers are live as of this reading.

Files in scope:
- `controller/runtime/controller.py` — outer loop (`tick`, `run`, `setup`, `next_mode`, `recipe_mode`)
- `controller/runtime/modes/base.py` — `ControlMode.run()` shared skeleton + shared helpers
- `controller/runtime/modes/{smoke,hold,startup,reignite,monitor,manual,prime,shutdown}.py`
- `controller/runtime/logic/safety.py` — pure verdict functions (no writes)

---

## 1. State inventory

`control["mode"]` can hold the following strings. "Mode-class" states are
dispatched through `_MODE_HANDLERS` (controller.py:44-53) → `run_work_cycle` →
`ControlMode.run()`. "Pseudo-state" strings are handled ONLY in the outer loop
(`Controller.tick()` / `recipe_mode`) and have **no `ControlMode` subclass**.

| State string | Kind | Backing class / handler location |
|---|---|---|
| `Startup` | Mode-class | `StartupMode` — modes/startup.py; dispatched controller.py:424-457 |
| `Smoke` | Mode-class | `SmokeMode` — modes/smoke.py; dispatched controller.py:460-462 |
| `Hold` | Mode-class | `HoldMode` — modes/hold.py; dispatched controller.py:465-467 |
| `Monitor` | Mode-class **and** pseudo | `MonitorMode` — modes/monitor.py; dispatched controller.py:480-483 (also set by boot-to-monitor / switch semantics) |
| `Manual` | Mode-class | `ManualMode` — modes/manual.py; dispatched controller.py:486-487 |
| `Prime` | Mode-class | `PrimeMode` — modes/prime.py; dispatched controller.py:410-421 |
| `Reignite` | Mode-class | `ReigniteMode(StartupMode)` — modes/reignite.py; dispatched controller.py:494-504 |
| `Shutdown` | Mode-class | `ShutdownMode` — modes/shutdown.py; dispatched controller.py:470-477 |
| `Stop` | **Pseudo-state** | No class. Terminal cleanup handled in the `mode in ("Stop","Error")` block, controller.py:347-407 (Stop-specific 376-389) |
| `Error` | **Pseudo-state** | No class. Terminal cleanup handled controller.py:347-407 (Error-specific 390-405) |
| `Recipe` | **Pseudo-state** | No class. Handled by `Controller.recipe_mode()` controller.py:103-191; also read as an OVERLAY flag inside base.run() (see §5) |

### `status` vs `mode` (they are distinct fields)

`control["status"]` is a separate field from `control["mode"]`:
- `"active"` — set when an update lands and mode ∉ (`Error`) and status ≠ `monitor` (controller.py:343-345)
- `"monitor"` — set when dispatching Monitor mode (controller.py:481); persists to distinguish "monitor-mode error" from a normal error
- `"inactive"` — set during Stop cleanup (controller.py:379, note: overwritten by the flush — see §7 gotcha) and Error cleanup (controller.py:396)
- `""` (empty) — the effective persisted status after Stop, because Stop rebinds control to a fresh `default_control()` AFTER setting `"inactive"` (see §7).

`status["mode"]` (the STATUS blob, not control) is force-set to `"Stop"` in the
Stop/Error cleanup (controller.py:362) regardless of whether the terminal state
was Stop or Error.

The `control["status"] == "monitor"` value is load-bearing at controller.py:343
and 371: a Monitor-mode Error keeps power ON (grill_platform.power_on(),
line 372) instead of powering off.

---

## 2. Complete transition-edge table

Side-effect key (checked per write site):
`U`=sets `control['updated']=True`; `WC`=`write_control(WriteKind.OVERWRITE)`;
`SP`=sets `primary_setpoint`/setpoint; `RR-`=decrements `reigniteretries`;
`RLS`=sets `reignitelaststate`; `N:x`=`notifications.send("x")`;
`D:x`=pushes display command `x`; `M`=writes metrics.

Mechanism: **NM** = via `Controller.next_mode()`; **DW** = direct
`control["mode"]=` write.

| # | from | to | trigger / guard | side effects | source | mech |
|---|---|---|---|---|---|---|
| 1 | any (setup) | Monitor | `settings['globals']['boot_to_monitor']` true, at boot | U, WC | controller.py:216-220 | DW |
| 2 | any active (outer tick) | Stop | on/off switch flips OFF (`not standalone` & input changed) | U, WC | controller.py:245-252 | DW |
| 3 | any (post-update) | Stop | `control['units_change']` true | (no WC here — relies on Stop-change WC downstream; also sets `units_change=False`) | controller.py:332-340 | DW |
| 4 | Smoke | Error | `setup_safety`: `evaluate_flameout(afterstarttemp,startuptemp,retries)==ERROR` (pre-loop) | U, WC, D:"ERROR", N:Grill_Error_02 | smoke.py:71-77 | DW |
| 5 | Smoke | Reignite | `setup_safety`: verdict==REIGNITE (pre-loop) | U, WC, RR-, RLS="Smoke", D:"Re-Ignite", N:Grill_Error_03 | smoke.py:78-86 | DW |
| 6 | Smoke | Error | `check_safety`: in-loop `evaluate_flameout(ptemp,...)==ERROR` | U, WC, D:"ERROR", N:Grill_Error_02, returns True→break | smoke.py:133-139 | DW |
| 7 | Smoke | Reignite | `check_safety`: in-loop verdict==REIGNITE | U, WC, RR-, RLS="Smoke", D:"Re-Ignite", N:Grill_Error_03, break | smoke.py:140-148 | DW |
| 8 | Hold | Error | `setup_safety`: pre-loop flameout ERROR | U, WC, D:"ERROR", N:Grill_Error_02 | hold.py:108-117 | DW |
| 9 | Hold | Reignite | `setup_safety`: pre-loop flameout REIGNITE | U, WC, RR-, RLS="Hold", D:"Re-Ignite", N:Grill_Error_03 | hold.py:118-126 | DW |
| 10 | Hold | Error | `check_safety`: in-loop flameout ERROR | U, WC, D:"ERROR", N:Grill_Error_02, break | hold.py:317-323 | DW |
| 11 | Hold | Reignite | `check_safety`: in-loop flameout REIGNITE | U, WC, RR-, RLS="Hold", D:"Re-Ignite", N:Grill_Error_03, break | hold.py:324-332 | DW |
| 12 | any mode-class | Error | UNIVERSAL max-temp trip: `over_max_temp(ptemp,safety)` in base.run() before actuation | U, WC, D:"ERROR", N:Grill_Error_01, break | base.py:511-517 | DW |
| 13 | any mode-class | Stop | UNIVERSAL inner-loop switch-off (`not standalone` & input OFF) inside base.run() | U, WC, sets `status="active"`, break | base.py:401-409 | DW |
| 14 | Prime | `next_mode` (usu. Startup) | Prime work cycle returns; outer loop advances | via NM: WC+U+SP(=`start_to_mode.primary_setpoint`, but SP only applied if to=="Hold"); sets `next_mode` arg = `control['next_mode']` | controller.py:416-421 | NM |
| 15 | Startup | Prime | `settings['startup']['prime_on_startup']>0` (prime-before-startup) | sets `prime_amount`, WC | controller.py:434-439 | DW |
| 16 | Prime(during Startup) | Startup | after prime work cycle, if mode still in ["Prime","Startup"] | sets `updated=False`, mode="Startup" (in-memory; WC at 450) | controller.py:443-445 | DW |
| 17 | Startup | `next_mode` (`after_startup_mode`, usu. Smoke/Hold) | Startup work cycle returns; `control['next_mode']` = `after_startup_mode` (set 449) | WC(449); via NM: WC+U+SP(=`start_to_mode.primary_setpoint` if to=="Hold") | controller.py:447-457 | NM |
| 18 | Smoke | `control['next_mode']` | Smoke work cycle returns normally (no safety trip) | via NM: WC+U+SP(0) | controller.py:460-462 | NM |
| 19 | Hold | `control['next_mode']` | Hold work cycle returns normally | via NM: WC+U+SP(0 unless to=="Hold") | controller.py:465-467 | NM |
| 20 | Shutdown | Stop | Shutdown work cycle returns; `next_mode` force-set to "Stop" first | WC(472); via NM: WC+U; optionally `os.system("...shutdown -h now")` if `auto_power_off` | controller.py:470-477 | NM |
| 21 | Reignite | `reignitelaststate` (Smoke/Hold/Startup) | Reignite work cycle returns; `next_mode`=`safety.reignitelaststate`, setpoint carried from `primary_setpoint` | WC(502); via NM: WC+U+SP(=carried setpoint if to=="Hold") | controller.py:494-504 | NM |
| 22 | Monitor | (stays Monitor) | dispatch: sets `status="monitor"`, runs cycle; no next_mode | sets status="monitor", WC | controller.py:480-483 | DW(status only) |
| 23 | Manual | (stays Manual) | dispatch: runs cycle; **no** next_mode call | none | controller.py:486-487 | — |
| 24 | Stop | (terminal reset) | mode=="Stop" in cleanup block | outputs OFF; status blob reset; `read_control(flush=True)` → defaults; `updated=False`, `next_mode="Stop"`, `reigniteretries` reset, `startup_timestamp=0`, WC; D:clear; metrics Stop + cookfile (unless last was Prime) | controller.py:347-389, 407 | DW |
| 25 | Error | (terminal reset, mode STAYS Error) | mode=="Error" in cleanup block | outputs OFF; `control=default_control()`; mode="Error"; status="inactive"; `updated=False`; `next_mode="Stop"`; `reigniteretries` reset; WC; clock.sleep(3); D:clear | controller.py:347-405 | DW |
| 26 | Recipe | (per step) step's mode | recipe_mode walks steps; runs `work_cycle(step['mode'])` with mode staying "Recipe" as overlay | sets `recipe.step`, `step_data`, `primary_setpoint`=step hold_temp, `updated=False`, WC | controller.py:138-155 | DW(overlay) |
| 27 | Recipe | Recipe (retry step) | after step, `mode=="Reignite" and updated` → run reignite then retry | sets `updated=False`, mode="Recipe", WC, then `work_cycle("Reignite")` | controller.py:160-164 | DW |
| 28 | Recipe | Stop | recipe normal end (all steps done, or no pending update) | `updated=True`, mode="Stop", WC | controller.py:184-189 | DW |
| 29 | Recipe | (break→whatever mode was requested) | recipe cancel: `mode != "Recipe" and updated` (or reignite path yielded non-Recipe) | break; leaves the requested mode in control for the OUTER tick to dispatch | controller.py:166-174 | (indirect) |
| 30 | any mode-class | (loop break, no mode write) | base.run(): `control['updated']` already True at top of tick | break only (transition already written elsewhere) | base.py:365-366 | (consume) |
| 31 | Smoke/Hold (Recipe overlay) | (loop break) | end-of-loop recipe trigger fired & not paused | break; may N:Recipe_Step_Message | base.py:600-604 | (consume) |

Notes on the `next_mode` target rows (14, 17, 18, 19, 20, 21): the literal
destination is whatever `control["next_mode"]` holds at that moment. Normal
happy-path chain is **Prime→Startup→(Smoke|Hold)→…→Shutdown→Stop**, with
`after_startup_mode` and the user's Hold/Smoke choice determining the middle.

---

## 3. The two mechanisms compared

### `Controller.next_mode(next_mode, setpoint=0)` — controller.py:88-98

```
ctx.store.execute_control_writes()          # flush any deferred writes first
control = ctx.store.read_control()           # re-read fresh
if not control["updated"]:                   # GUARD: only transition if nobody else set updated
    control["mode"] = next_mode
    control["primary_setpoint"] = setpoint if next_mode == "Hold" else 0
    control["updated"] = True
    ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
return control
```

Fields it touches: `mode`, `primary_setpoint` (setpoint only if target is Hold,
else forced to 0), `updated=True`, one `write_control(OVERWRITE)`.
Does **NOT**: send notifications, push display, touch reignite fields, write
metrics. Crucially it is **guarded**: if `control["updated"]` is already True
(because a mode file already requested a transition, e.g. flameout→Reignite),
`next_mode` is a **no-op** — the mode-file's requested transition wins. This is
how a safety trip inside a work cycle survives the outer loop's post-cycle
`next_mode(control["next_mode"])` call.

### Inline direct writes — e.g. smoke.py:71-86 / hold.py:108-126

The safety-write pattern (repeated 8× across smoke/hold setup_safety +
check_safety) does, in order:
1. push a display command (`display_commands().push(("text","ERROR"|"Re-Ignite"))`)
2. `control["mode"] = "Error" | "Reignite"`
3. (Reignite only, FIRST) `control["safety"]["reigniteretries"] -= 1`
4. (Reignite only) `control["safety"]["reignitelaststate"] = self.name`
5. `control["updated"] = True`
6. `ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")`
7. `ctx.notifications.send("Grill_Error_02" | "Grill_Error_03")`
8. (check_safety variants) `return True` → base.run() breaks the loop

The base-skeleton direct writes are narrower:
- max-temp (base.py:511-517): D:"ERROR" → mode="Error" → updated=True → WC → N:Grill_Error_01 → break
- switch-off (base.py:401-409): mode="Stop" → status="active" → updated=True → WC → break

**Implication for a unified `request_transition()` seam:** to faithfully
reproduce BOTH, the seam must accept and optionally perform: target mode,
setpoint (with the "only-if-Hold-else-0" quirk — this is specific to `next_mode`
and NOT done by the inline writes), `updated=True`, reignite-retry decrement,
reignite-last-state capture, a notification key, a display command, and the
`updated`-guard (present in `next_mode`, ABSENT in the inline writes — inline
writes unconditionally set `updated=True`). The inline writes never touch
`primary_setpoint`; `next_mode` always does. That asymmetry is the trap.

---

## 4. How transitions are CONSUMED

The outer loop and the inner loop each read `control["updated"]` as the
"something changed, re-dispatch" signal.

**Inner loop (base.run()), base.py:358-366** — top of every work-cycle tick:
```
ctx.store.execute_control_writes()
control = ctx.store.read_control()
self.control = control
process_system_commands(ctx)
if control["updated"]:      # a new mode/setting was requested
    break                    # leave the work cycle immediately
```
So any write that sets `updated=True` (a safety trip, a user mode change landing
in the store, a switch-off) causes the current mode's cycle to exit at the next
tick. The mode string itself is NOT re-read for dispatch here — the inner loop
only cares that *something* changed.

**Outer loop (Controller.tick())** — the gate at controller.py:322:
```
if self.control["updated"] and not self.control["critical_error"]:
    ...
    self.control["updated"] = False           # CLEAR the flag (line 327)
    store.write_control(...)                   # persist the clear
    ...
    if self.control["mode"] in ("Stop","Error"): ...      # 347
    elif self.control["mode"] == "Prime": ...             # 410
    elif self.control["mode"] == "Startup": ...           # 424
    elif ... "Smoke" / "Hold" / "Shutdown" / "Monitor" / "Manual" / "Recipe" / "Reignite"
```
So the outer loop only dispatches a mode when `updated` is True; it clears
`updated` first, then reads `control["mode"]` to pick the branch. Each mode
branch runs its work cycle (`self.work_cycle(mode)`), and the work cycle
internally re-reads control every tick. When the work cycle returns, the
cycling modes call `self.next_mode(self.control["next_mode"])` to write the NEXT
transition (which sets `updated=True` again), so the *next* outer-loop iteration
re-enters the gate and dispatches the new mode.

Role of `updated`: it is the single edge-trigger for both loops. `True` means
"a transition/settings change is pending; act on it." The outer loop consumes it
(clears to False, dispatches), the inner loop respects it (breaks). It is
re-armed every time any code writes a new `mode`.

Role of `next_mode` (the control FIELD, distinct from the method): it is the
staged destination for cycling modes. Prime/Startup/Smoke/Hold/Shutdown/Reignite
don't hardcode where they go — they read `control["next_mode"]` and pass it to
`self.next_mode(...)`. It is (re)written at Stop cleanup (`="Stop"`, line 384),
Shutdown dispatch (`="Stop"`, 471), Startup dispatch (`=after_startup_mode`,
449), Reignite dispatch (`=reignitelaststate`, 500).

`critical_error` gates the whole dispatch block (line 322) — if set, no
dispatch happens at all.

---

## 5. The `Recipe` sub-machine (`Controller.recipe_mode`, controller.py:103-191)

`Recipe` is a pseudo-state: the outer tick dispatches it to `recipe_mode()`
(controller.py:490-491) with `start_step = control["recipe"]["start_step"]`.
`recipe_mode` runs a NESTED loop over recipe steps; within each step it invokes
a REAL work cycle for that step's mode while `control["mode"]` stays `"Recipe"`
(the mode string is used as an OVERLAY: base.run() reads `control["mode"]=="Recipe"`
at base.py:257, 553, 563, 600 to set up recipe timers/triggers and to break on
step completion — the actual actuation mode comes from `recipe["steps"][n]["mode"]`).

Recipe-internal transition edges:

| from | to | trigger | side effects | source |
|---|---|---|---|---|
| Recipe step N | work_cycle(step mode) | loop body per step | writes `recipe.step`, `recipe.step_data` (incl. mapped `trigger_temps`, `triggered=False`), `primary_setpoint`=step `hold_temp`, `updated=False`, WC | controller.py:138-155 |
| Recipe (after step) | Recipe + Reignite retry | after step, `mode=="Reignite" and updated` | `updated=False`, mode="Recipe", WC, then `work_cycle("Reignite")`; then re-reads control | controller.py:158-164 |
| Recipe (reignite done) | break/cancel | after reignite, `updated and mode!="Recipe"` | logs "cancelled due to mode change", `break` (does NOT increment step) | controller.py:165-169 |
| Recipe (after step) | break/cancel | `mode!="Recipe" and updated` (safety trip or user change during step) | logs cancel, `break` — leaves requested mode in control for outer tick | controller.py:171-174 |
| Recipe (after step) | next step | else (no pending update) | `step_num += 1` | controller.py:175-177 |
| Recipe | Stop | normal end: `not updated` OR `step_num == num_steps` (loop exhausted) | clears `recipe.step/step_data/filename`, `updated=True`, mode="Stop", WC | controller.py:179-189 |
| Recipe (early) | (silent return) | recipe file missing, or metadata/recipe read != "OK" | returns `()` with NO mode change (leaves control as-is) | controller.py:113-128 |

Note the cancel edges (`break`) don't themselves write a mode — they rely on the
requested mode (written by the safety trip or user) already being in control, so
the OUTER tick's next iteration dispatches it. The early-return path
(controller.py:113-128) is a silent no-op: a missing recipe file leaves the
controller in `Recipe` mode with `updated` presumably still set from entry — a
latent stuck-state risk.

---

## 6. Existing test coverage of transitions

Test files that assert transition behavior:
- `tests/characterization/test_modes_golden.py` — inner work-cycle (InMemoryStore)
- `tests/e2e/test_work_cycle_e2e.py` — same scenarios over real SQLite
- `tests/characterization/test_controller_loop_golden.py` — outer loop, dispatch **spied** (work_cycle/next_mode/recipe_mode replaced with recorders)

### Edges WITH coverage

| Edge | Test |
|---|---|
| Smoke→Error (setup_safety flameout, retries==0) | test_modes_golden `test_smoke_flameout_without_retries_triggers_error`; e2e `test_e2e_smoke_flameout_without_retries...` |
| Smoke→Reignite (setup_safety flameout, retries>0; asserts RR- to 1 and RLS="Smoke") | golden `test_smoke_flameout_with_retries_triggers_reignite`; e2e equivalent |
| Smoke→Error (max-temp, Grill_Error_01) | golden `test_smoke_over_maxtemp...`; e2e `test_e2e_smoke_over_maxtemp...` |
| Hold→Error (max-temp) | golden `test_hold_over_maxtemp_does_not_submit_controller...` |
| Startup exits (stays Startup; timer & exit_temp) | golden `test_startup_exits_on_timer`, `test_startup_exits_on_exit_temp` |
| Reignite exits (timer & exit_temp) | golden `test_reignite_exits_on_timer`, `test_reignite_exits_on_exit_temp` |
| Prime elapses (stays Prime, teardown fan/power off) | golden + e2e `test_prime_elapses...` |
| Shutdown elapses (stays Shutdown) | golden `test_shutdown_elapses...` |
| Monitor idles powered off | golden `test_monitor_idles...` |
| Recipe overlay step-trigger break (no pause / with pause) | golden `test_recipe_overlay_triggered_without_pause...`, `..._with_pause...` |
| boot_to_monitor → Monitor | loop-golden `test_setup_boot_to_monitor_requests_monitor_mode` |
| Outer dispatch: Smoke→work_cycle→next_mode("Stop") | loop-golden `test_tick_smoke_dispatches_work_cycle_then_next_mode` |
| Outer dispatch: Hold→work_cycle→next_mode | loop-golden `test_tick_hold_dispatches...` |
| Outer dispatch: Monitor sets status="monitor" | loop-golden `test_tick_monitor_sets_status_monitor...` |
| Outer dispatch: Manual runs cycle, NO next_mode | loop-golden `test_tick_manual_runs_cycle_without_next_mode` |
| Outer dispatch: Recipe→recipe_mode(start_step) | loop-golden `test_tick_recipe_dispatches_recipe_mode` (spied — start_step only) |
| Outer dispatch: Shutdown sets next_mode="Stop" | loop-golden `test_tick_shutdown_sets_next_mode_stop...` |
| Stop cleanup (outputs off, reset, next_mode="Stop") | loop-golden `test_tick_stop_mode_cleanup` |
| Error cleanup (mode stays Error, status inactive, 3s dwell) | loop-golden `test_tick_error_mode_cleanup` |
| Outer switch-off → Stop | loop-golden `test_tick_switch_off_triggers_stop` |

Important caveat: the loop-golden tests **spy** `next_mode`/`work_cycle`/
`recipe_mode`, so they verify the outer loop CALLS them with the right args, but
NOT what `next_mode()` actually writes (the `updated`-guard, the
`setpoint if=="Hold"` rule) nor anything inside `recipe_mode`.

### Edges with NO transition-level coverage (gaps a characterization pass must fill)

1. **Hold→Error via setup_safety flameout** (hold.py:111-117) — no hold-flameout test exists.
2. **Hold→Reignite via setup_safety flameout** (hold.py:118-126) — RR-/RLS="Hold" never asserted.
3. **Hold→Error via check_safety (in-loop)** (hold.py:317-323).
4. **Hold→Reignite via check_safety (in-loop)** (hold.py:324-332).
5. **Smoke→Error/Reignite via check_safety (in-loop)** (smoke.py:133-148) — the golden smoke tests trip in setup_safety (pre-loop) via afterstarttemp, so the identical-but-distinct in-loop path is not exercised.
6. **base inner-loop switch-off → Stop** (base.py:401-409) — only the OUTER switch-off is tested; the in-work-cycle switch break is not.
7. **units_change → Stop** (controller.py:332-340).
8. **Startup → Prime (prime_on_startup)** (controller.py:434-439) and the **Prime→Startup restore** (controller.py:443-445).
9. **`next_mode()` field semantics** — the `updated`-guard no-op behavior and `primary_setpoint = setpoint if "Hold" else 0` are not unit-tested (spied away).
10. **Reignite outer dispatch** — `next_mode=reignitelaststate` + setpoint carry (controller.py:494-504) has no test.
11. **All of `recipe_mode`'s internal edges** (§5): step→step-mode, reignite-during-recipe→Recipe retry, recipe cancel→break, normal end→Stop, missing-file silent return. Only the dispatch *into* recipe_mode is (spy-)tested.
12. **Stop cookfile / metrics side-branch** (controller.py:351-359, "last was Prime" skip) — partially observable but not asserted.

---

## 7. Risks / gotchas for formalizing

1. **Two mechanisms encode the "same" edge differently.** Smoke and Hold each
   have their OWN copy of the flameout→Error / flameout→Reignite writes
   (smoke.py:71-86 & 133-148, hold.py:108-126 & 317-332). They are
   byte-for-byte parallel except `RLS=self.name`. A unified table must confirm
   they truly match (they do today) — but a future edit to one and not the other
   would silently diverge. Startup/Reignite share via inheritance instead.

2. **`next_mode()` is guarded; the inline writes are not.** `next_mode` only
   transitions `if not control["updated"]`. The inline safety writes
   unconditionally set `updated=True`. This asymmetry is the mechanism by which
   a mid-cycle safety trip beats the post-cycle `next_mode(control["next_mode"])`
   — the safety write set `updated`, so `next_mode` becomes a no-op and the
   Error/Reignite target survives. Any `request_transition()` seam MUST preserve
   both the guarded and unguarded variants, or it will clobber safety trips.

3. **`primary_setpoint` coupling.** `next_mode` forces `primary_setpoint =
   setpoint if next_mode=="Hold" else 0`. The inline writes never touch it.
   `recipe_mode` sets it per step (hold_temp). Startup/Prime/Reignite pass a
   setpoint drawn from `settings['startup']['start_to_mode']['primary_setpoint']`
   (14,17) or the carried `control['primary_setpoint']` (21). This field is
   entangled with the transition, not orthogonal to it.

4. **`status` vs `mode` are different axes.** Monitor dispatch writes
   `status="monitor"` (not a mode change). The Stop/Error cleanup keys off BOTH
   (`status=="monitor" and mode=="Error"` → keep power ON, controller.py:371).
   A pure `mode` FSM will miss the power-on-vs-off decision unless `status` is
   modeled as a second dimension / guard.

5. **Pseudo-states have no class.** `Stop`, `Error`, `Recipe` are handled
   entirely in the outer loop. `Recipe` additionally lives as an OVERLAY inside
   base.run() (mode stays "Recipe" while a real work cycle for the step's mode
   runs). Formalizing must treat `Recipe` as both a top-level state AND an
   overlay flag — it is not a peer of Smoke/Hold.

6. **Stop cleanup has a known dead assignment.** controller.py:379 sets
   `status="inactive"` then line 381 rebinds `control = read_control(flush=True)`
   → fresh `default_control()`, discarding the "inactive". The persisted status
   after Stop is therefore `""`, and `test_tick_stop_mode_cleanup` PINS this
   (`assert control["status"] == ""`). Do not "fix" it during formalization
   without regenerating that golden.

7. **Ordering dependencies inside the transition write.** The Reignite inline
   write decrements `reigniteretries` and sets `reignitelaststate` BEFORE setting
   `updated`/writing. If a `request_transition()` reorders these (e.g. computes
   the target after clearing retries), it changes what a concurrent reader sees.
   Preserve the exact field-write order.

8. **`Error` mode persists across cleanup; `Stop` resets to defaults.** The
   terminal block (controller.py:347-407) treats them together for output-off but
   diverges: Stop → `read_control(flush=True)` (full default reset,
   `next_mode="Stop"`); Error → `default_control()` then re-stamps `mode="Error"`
   and dwells 3s. Both reset `reigniteretries`. Model these as two distinct
   terminal transitions, not one.

9. **Recipe silent no-op on missing file** (controller.py:113-128) leaves the
   controller in `Recipe` with no mode written — a potential stuck state the FSM
   should make explicit (e.g. an implicit `Recipe→Stop` on load failure).

10. **`critical_error` gates ALL dispatch** (controller.py:322). It is an
    orthogonal kill-switch that suppresses every outer transition; the FSM guard
    layer must include it or the table will over-predict transitions.
