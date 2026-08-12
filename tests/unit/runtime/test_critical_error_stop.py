"""A latched `critical_error` must never take the Stop button away.

`build_devices()` sets `control["critical_error"]` when the grill platform fails
to import or construct, and nothing in the running process ever clears it. The
outer dispatch in `Controller.tick()` is gated on that flag, so while it is set
the loop consumes NOTHING -- not a Stop, not a Shutdown, not even the clearing of
`updated`. A grill whose platform driver is missing or intermittent could have a
fire in it and no way to be commanded down.

THE REQUEST PATH these tests model, key by key:

    POST /api/set/mode/stop
      -> blueprints/api/routes.py: process_command(action="set",
                                                  arglist=["mode","stop",...])
      -> common/api_commands.py _cmd_set_mode: control_delta(
             set_values={"mode": Mode.STOP, "updated": True})
      -> enqueue_control_delta(delta)   [queue_control_write]
      -> controller.tick(): store.execute_control_writes()   (controller.py:297)
      -> controller.tick(): self.control = store.read_control()          (:298)
      -> the dispatch gate reads self.control["mode"]                    (:394)

So the operator's requested mode is readable at the gate as `control["mode"]`;
`next_mode` is NOT part of this path (it is the controller's own "where this
cycling mode goes next" field, seeded to Stop by default_control()). These tests
push the same DELTA envelope `_cmd_set_mode` builds and let tick()'s own drain
apply it, so both ends of that seam are exercised rather than assumed.
"""

import pytest

from common.control_delta import control_delta
from common.modes import Mode
from controller.runtime.clock import ManualClock
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization._controller_harness import _neutralize_externals, make_controller


def _controller(monkeypatch, *, mode, critical_error):
    """A booted controller whose platform failed to build, mid-cook in `mode`."""
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    # `os.system("sleep 3 && sudo shutdown -h now &")` lives at the tail of the
    # Shutdown dispatch; _neutralize_externals already swapped controller.os for
    # a recorder, and this keeps the branch unreached as well.
    settings["shutdown"]["auto_power_off"] = False
    control_data = base_control(mode=mode)
    control_data["critical_error"] = critical_error
    control_data["updated"] = False
    c, ctx, store, grill, dist, notifier = make_controller(
        settings, control_data, base_pellet_db(), clock=ManualClock()
    )
    calls = []
    c.work_cycle = lambda mode: calls.append(("work_cycle", mode))
    c.next_mode = lambda next_mode, setpoint=0: calls.append(("next_mode", next_mode, setpoint))
    c.recipe_mode = lambda start_step=0: calls.append(("recipe_mode", start_step))
    c.setup()
    return c, store, grill, calls


def _request_mode(store, mode):
    """Queue exactly what `_cmd_set_mode` queues for `/api/set/mode/<mode>`."""
    store.enqueue_control_delta(
        control_delta(set_values={"mode": mode, "updated": True}),
        origin="api",
    )


# --------------------------------------------------------------------------
# 1 & 2. The regression: safe modes dispatch through a latched critical_error
# --------------------------------------------------------------------------


def test_stop_is_dispatched_while_critical_error_is_latched(monkeypatch):
    """THE REGRESSION. A lit grill (Manual) on a platform that failed to build
    must still be stoppable."""
    c, store, grill, calls = _controller(monkeypatch, mode=Mode.MANUAL, critical_error=True)
    _request_mode(store, Mode.STOP)

    c.tick()

    control = store.read_control()
    # `updated` is the discriminator: the drain at tick():297 applies the delta
    # regardless, so `mode` reads Stop either way -- only a dispatch that
    # actually RAN consumes the request. Reverting the gate trips THIS line.
    assert control["updated"] is False
    assert control["mode"] == Mode.STOP
    # ...and the Stop cleanup really executed, not just the flag bookkeeping.
    names = [name for name, _ in grill.calls]
    assert "auger_off" in names and "igniter_off" in names and "fan_off" in names
    assert store.read_status()["mode"] == Mode.STOP
    assert ("clear", None) in store.display_commands().list()


def test_shutdown_is_dispatched_while_critical_error_is_latched(monkeypatch):
    c, store, grill, calls = _controller(monkeypatch, mode=Mode.SMOKE, critical_error=True)
    _request_mode(store, Mode.SHUTDOWN)

    c.tick()

    # The Shutdown work cycle is the discriminator here -- it is reached only
    # from inside the gated block. Reverting the gate trips THIS line.
    assert ("work_cycle", Mode.SHUTDOWN) in calls
    assert store.read_control()["updated"] is False
    assert store.read_control()["mode"] == Mode.SHUTDOWN


def test_error_is_dispatched_while_critical_error_is_latched(monkeypatch):
    """`common/process_mon.py:108` writes exactly this triple -- mode=Error,
    critical_error=True, updated=True -- on a heartbeat timeout. Before this
    change that write gated ITSELF out: the flag it set in the same breath
    stopped its own Error mode from ever being dispatched."""
    c, store, grill, calls = _controller(monkeypatch, mode=Mode.SMOKE, critical_error=True)
    _request_mode(store, Mode.ERROR)

    c.tick()

    control = store.read_control()
    assert control["updated"] is False
    assert control["mode"] == Mode.ERROR
    names = [name for name, _ in grill.calls]
    assert "auger_off" in names and "igniter_off" in names and "fan_off" in names


# --------------------------------------------------------------------------
# 3. The gate still bites: a broken platform must refuse to light a fire
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cook_mode", [Mode.STARTUP, Mode.SMOKE, Mode.HOLD, Mode.PRIME])
def test_cook_modes_are_still_refused_while_critical_error_is_latched(monkeypatch, cook_mode):
    c, store, grill, calls = _controller(monkeypatch, mode=Mode.STOP, critical_error=True)
    _request_mode(store, cook_mode)

    c.tick()

    control = store.read_control()
    # The request is NOT consumed: it stays pending, exactly as before.
    assert control["updated"] is True
    assert calls == []
    names = [name for name, _ in grill.calls]
    assert "auger_on" not in names and "igniter_on" not in names


# --------------------------------------------------------------------------
# 4. No behaviour change when the flag is clear
# --------------------------------------------------------------------------


def test_stop_dispatches_unchanged_when_critical_error_is_clear(monkeypatch):
    c, store, grill, calls = _controller(monkeypatch, mode=Mode.MANUAL, critical_error=False)
    _request_mode(store, Mode.STOP)

    c.tick()

    control = store.read_control()
    assert control["updated"] is False
    assert control["mode"] == Mode.STOP
    assert control["status"] == "inactive"
    assert control["next_mode"] == Mode.STOP
    names = [name for name, _ in grill.calls]
    assert "auger_off" in names and "igniter_off" in names and "fan_off" in names


def test_startup_dispatches_unchanged_when_critical_error_is_clear(monkeypatch):
    c, store, grill, calls = _controller(monkeypatch, mode=Mode.STOP, critical_error=False)
    _request_mode(store, Mode.STARTUP)

    c.tick()

    assert ("work_cycle", Mode.STARTUP) in calls
    assert store.read_control()["updated"] is False


# --------------------------------------------------------------------------
# 5. The flag itself is never cleared as a side effect
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "requested",
    [Mode.STOP, Mode.SHUTDOWN, Mode.ERROR, Mode.STARTUP, Mode.SMOKE],
)
def test_critical_error_survives_every_dispatch_outcome(monkeypatch, requested):
    """The flag describes the hardware, not the cook. Both cleanup branches
    rebind control to a fresh default_control() (critical_error False), so
    without the re-stamp in tick() a Stop would ANSWER "can this controller
    drive its platform?" by forgetting the question -- and the very next
    Startup would sail through the gate onto hardware that never built."""
    c, store, grill, calls = _controller(monkeypatch, mode=Mode.MANUAL, critical_error=True)
    _request_mode(store, requested)

    c.tick()

    assert store.read_control()["critical_error"] is True


def test_a_stopped_grill_still_refuses_to_light_on_broken_hardware(monkeypatch):
    """The end-to-end safety property: Stop is reachable, and reaching it does
    not hand back the ability to start a fire."""
    c, store, grill, calls = _controller(monkeypatch, mode=Mode.MANUAL, critical_error=True)

    _request_mode(store, Mode.STOP)
    c.tick()
    assert store.read_control()["mode"] == Mode.STOP
    assert store.read_control()["updated"] is False

    _request_mode(store, Mode.STARTUP)
    c.tick()
    # Still latched, so Startup is still refused and still pending.
    assert store.read_control()["critical_error"] is True
    assert store.read_control()["updated"] is True
    assert calls == []
