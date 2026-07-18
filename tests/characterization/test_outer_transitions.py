"""Characterization ("golden master") tests for the OUTER-loop + recipe
transitions -- the control['mode'] writes performed by Controller.next_mode(),
Controller.recipe_mode(), and the tick() units_change / prime-on-startup /
reignite-dispatch branches.

Fills inventory gaps #7-#12. UNLIKE test_controller_loop_golden.py, these do
NOT spy next_mode()/recipe_mode() -- they call the REAL methods and assert what
they WRITE (the next_mode updated-guard, the `setpoint if=="Hold" else 0` rule,
and every recipe_mode internal edge). Per-mode dispatch (work_cycle) IS spied so
a scenario exercises only the transition bookkeeping, not a full work cycle.

SAFETY: build_controller() neutralizes controller.py's module-level os.system
(via the loop-golden _neutralize_externals helper) AND sets
shutdown.auto_power_off=False. None of these scenarios drive the Shutdown->Stop
edge, so os.system is never on any path exercised here; the recorder confirms it
is never invoked.
"""

from common.common import WriteKind
from tests.characterization.fixtures import base_settings, base_control, base_pellet_db
from tests.characterization.test_controller_loop_golden import (
    make_controller,
    _neutralize_externals,
    _spy_dispatch,
)


def build_controller(monkeypatch, *, mode="Stop", settings=None, control_over=None):
    """Construct a real Controller with os.system neutralized and
    auto_power_off disabled. Returns (controller, store)."""
    _neutralize_externals(monkeypatch)  # patches controller_mod.os -> recorder, plus notify/cookfile
    settings = settings if settings is not None else base_settings()
    settings["shutdown"]["auto_power_off"] = False
    control_data = base_control(mode=mode)
    for key, value in (control_over or {}).items():
        control_data[key] = value
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    c.setup()
    return c, store


# --------------------------------------------------------------------------
# Step 1: next_mode() field semantics (gap #9) -- the REAL method, not spied
# --------------------------------------------------------------------------


def test_next_mode_transitions_when_not_updated(monkeypatch):
    c, store = build_controller(monkeypatch)
    control = store.read_control()
    control["updated"] = False
    store.write_control(control, WriteKind.OVERWRITE, origin="test")
    c.next_mode("Hold", setpoint=225)
    out = store.read_control()
    assert out["mode"] == "Hold"
    assert out["primary_setpoint"] == 225  # Hold => setpoint applied
    assert out["updated"] is True


def test_next_mode_is_noop_when_already_updated(monkeypatch):
    c, store = build_controller(monkeypatch)
    control = store.read_control()
    control["updated"] = True
    control["mode"] = "Error"
    store.write_control(control, WriteKind.OVERWRITE, origin="test")
    c.next_mode("Smoke")  # guard: must NOT overwrite an already-requested transition
    out = store.read_control()
    assert out["mode"] == "Error"  # safety trip survives


def test_next_mode_forces_setpoint_zero_when_not_hold(monkeypatch):
    c, store = build_controller(monkeypatch)
    control = store.read_control()
    control["updated"] = False
    control["primary_setpoint"] = 300
    store.write_control(control, WriteKind.OVERWRITE, origin="test")
    c.next_mode("Smoke", setpoint=225)
    out = store.read_control()
    assert out["mode"] == "Smoke"
    assert out["primary_setpoint"] == 0  # non-Hold target forces setpoint to 0


# --------------------------------------------------------------------------
# Step 2: recipe_mode() internal edges (gap #11)
# --------------------------------------------------------------------------


def _step(mode="Smoke", hold_temp=225):
    return {
        "mode": mode,
        "hold_temp": hold_temp,
        "trigger_temps": {"primary": 0, "food": []},
        "timer": 0,
        "pause": False,
        "notify": False,
    }


def _install_recipe(monkeypatch, c, store, steps, *, units="F", exists=True):
    """Point recipe_mode's file access at an in-memory 2-part recipe."""
    import controller.runtime.controller as controller_mod

    control = store.read_control()
    control["recipe"]["filename"] = "/tmp/fake_recipe.json"
    store.write_control(control, WriteKind.OVERWRITE, origin="test")

    monkeypatch.setattr(controller_mod, "exists", lambda p: exists)

    def _fake_read(recipe_file, key):
        if key == "metadata":
            return {"units": units}, "OK"
        return {"steps": steps}, "OK"

    monkeypatch.setattr(controller_mod, "read_json_file_data", _fake_read)


def test_recipe_step_dispatch_and_normal_end_to_stop(monkeypatch):
    # Two steps, each work cycle a no-op (leaves control untouched): recipe_mode
    # walks both steps then, with steps exhausted, ends into Stop.
    c, store = build_controller(monkeypatch, mode="Recipe")
    steps = [_step("Smoke", 225), _step("Hold", 250)]
    _install_recipe(monkeypatch, c, store, steps)

    seen = []

    def _wc(mode):
        cur = store.read_control()
        seen.append((mode, cur["recipe"]["step"], cur["primary_setpoint"], cur["updated"]))

    c.work_cycle = _wc
    c.recipe_mode(start_step=0)

    # Per-step: control was written with recipe.step, primary_setpoint=hold_temp,
    # updated=False BEFORE the step work cycle ran.
    assert seen == [("Smoke", 0, 225, False), ("Hold", 1, 250, False)]
    out = store.read_control()
    assert out["mode"] == "Stop"
    assert out["updated"] is True


def test_recipe_reignite_during_step_handshake_then_retries_step(monkeypatch):
    # Step work cycle returns Reignite+updated -> recipe performs the reignite
    # HANDSHAKE (clear updated, set mode="Recipe", write), runs a Reignite work
    # cycle, then RE-ENTERS the same step (step_num NOT incremented) and re-runs it.
    #
    # This used to raise KeyError on the retry -- control['recipe']['step_data']
    # aliased the recipe's step dict, and remapping trigger_temps in place
    # corrupted the source so the retry's ['trigger_temps']['primary'] lookup
    # failed. Fixed by deep-copying the step into step_data; the source recipe
    # stays pristine, so the retry succeeds. The second Smoke pass does not
    # re-trip, the step completes, and the recipe ends normally in Stop.
    c, store = build_controller(monkeypatch, mode="Recipe")
    steps = [_step("Smoke", 225)]
    _install_recipe(monkeypatch, c, store, steps)

    calls = []
    handshake = {}
    state = {"tripped": False}

    def _wc(mode):
        calls.append(mode)
        cur = store.read_control()
        if mode == "Smoke" and not state["tripped"]:
            state["tripped"] = True
            cur["mode"] = "Reignite"
            cur["updated"] = True
            store.write_control(cur, WriteKind.OVERWRITE, origin="test")
        elif mode == "Reignite":
            # Captured AFTER the handshake write (clear updated, set Recipe), BEFORE retry.
            handshake["mode"] = cur["mode"]
            handshake["updated"] = cur["updated"]

    c.work_cycle = _wc
    c.recipe_mode(start_step=0)

    # trip -> reignite handshake -> retry the step (no crash, no re-trip)
    assert calls == ["Smoke", "Reignite", "Smoke"]
    assert handshake == {"mode": "Recipe", "updated": False}  # handshake cleared updated + set Recipe
    assert store.read_control()["mode"] == "Stop"  # step completes -> recipe ends in Stop


def test_recipe_cancel_on_mode_change_leaves_requested_mode(monkeypatch):
    # A non-Recipe mode requested (updated) during a step cancels the recipe:
    # recipe_mode breaks and leaves the requested mode in control (NOT Stop).
    c, store = build_controller(monkeypatch, mode="Recipe")
    steps = [_step("Smoke", 225), _step("Hold", 250)]
    _install_recipe(monkeypatch, c, store, steps)

    def _wc(mode):
        cur = store.read_control()
        cur["mode"] = "Monitor"
        cur["updated"] = True
        store.write_control(cur, WriteKind.OVERWRITE, origin="test")

    c.work_cycle = _wc
    c.recipe_mode(start_step=0)

    out = store.read_control()
    assert out["mode"] == "Monitor"  # requested mode preserved for the outer tick
    assert out["updated"] is True


def test_recipe_missing_file_transitions_to_stop(monkeypatch):
    # Missing recipe file -> recipe_mode routes to Stop before returning (bug fix,
    # gotcha #9). Previously it returned () with no mode write, leaving the
    # controller stuck idling in Recipe forever.
    c, store = build_controller(monkeypatch, mode="Recipe")
    _install_recipe(monkeypatch, c, store, [_step()], exists=False)

    called = []
    c.work_cycle = lambda mode: called.append(mode)
    result = c.recipe_mode(start_step=0)

    assert result == ()
    assert called == []  # never entered the step loop
    out = store.read_control()
    assert out["mode"] == "Stop"  # recovers from the stuck Recipe state
    assert out["updated"] is True  # terminal transition re-arms the outer dispatch


# --------------------------------------------------------------------------
# Step 3: units_change->Stop (#7), prime-on-startup handshake (#8),
#         reignite dispatch (#10)
# --------------------------------------------------------------------------


def test_units_change_forces_stop(monkeypatch):
    # tick() with units_change set drives the mode to Stop (via the Stop-cleanup
    # write) and consumes the units_change flag.
    c, store = build_controller(monkeypatch, mode="Smoke", control_over={"updated": True, "units_change": True})
    _spy_dispatch(c)  # isolate: the Stop terminal block itself uses no work_cycle/next_mode
    c.tick()
    out = store.read_control()
    assert out["next_mode"] == "Stop"  # Stop cleanup ran
    assert out["units_change"] is False
    assert store.read_status()["mode"] == "Stop"


def test_prime_on_startup_handshake(monkeypatch):
    # Startup dispatch with prime_on_startup>0: prime first (mode Prime), restore
    # to Startup, set next_mode=after_startup_mode, then next_mode(setpoint).
    settings = base_settings()
    settings["startup"]["prime_on_startup"] = 10
    settings["startup"]["start_to_mode"]["after_startup_mode"] = "Smoke"
    settings["startup"]["start_to_mode"]["primary_setpoint"] = 225
    c, store = build_controller(monkeypatch, mode="Startup", settings=settings, control_over={"updated": True})
    calls = _spy_dispatch(c)
    c.tick()
    out = store.read_control()
    assert out["prime_amount"] == 10  # prime amount seeded from settings
    assert out["mode"] == "Startup"  # restored after the Prime handshake
    assert out["next_mode"] == "Smoke"  # after_startup_mode staged
    assert ("work_cycle", "Prime") in calls
    assert ("work_cycle", "Startup") in calls
    assert calls.index(("work_cycle", "Prime")) < calls.index(("work_cycle", "Startup"))
    assert ("next_mode", "Smoke", 225) in calls


def test_reignite_dispatch_carries_last_state_and_setpoint(monkeypatch):
    # Reignite dispatch: next_mode <- safety.reignitelaststate, setpoint carried
    # from primary_setpoint.
    c, store = build_controller(
        monkeypatch,
        mode="Reignite",
        control_over={"updated": True, "primary_setpoint": 250},
    )
    control = store.read_control()
    control["safety"]["reignitelaststate"] = "Hold"
    store.write_control(control, WriteKind.OVERWRITE, origin="test")
    calls = _spy_dispatch(c)
    c.tick()
    out = store.read_control()
    assert out["next_mode"] == "Hold"
    assert ("work_cycle", "Reignite") in calls
    assert ("next_mode", "Hold", 250) in calls  # carried setpoint
