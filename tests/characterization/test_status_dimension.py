"""Characterization tests for `control["status"]` -- the controller's second
state axis (orthogonal to `control["mode"]`) -- BEFORE it is formalized as a
`StatusState(StrEnum)` + `should_keep_power_on(mode, status)` predicate.

These pin the CURRENT string values and the one mode x status coupling
(a Monitor-mode error keeps the OEM controller powered on) so the refactor
in Task 2/3 can be verified byte-identical: zero assertion edits here after
the enum + predicate land.

See docs/superpowers/specs/2026-07-18-status-second-dimension-design.md and
docs/superpowers/plans/2026-07-18-status-dimension.md.
"""

from tests.characterization.fixtures import base_settings, base_control, base_pellet_db
from tests.characterization.test_controller_loop_golden import make_controller, _spy_dispatch, _neutralize_externals


# --------------------------------------------------------------------------
# status-value transitions
# --------------------------------------------------------------------------


def test_stop_persists_inactive(monkeypatch):
    # Mirrors test_tick_stop_mode_cleanup, focused on the status axis.
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()
    assert store.read_control()["status"] == "inactive"


def test_error_persists_inactive(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Error")
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()
    assert store.read_control()["status"] == "inactive"


def test_monitor_dispatch_sets_monitor(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Monitor")
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()
    assert store.read_control()["status"] == "monitor"


def test_active_set_when_operating(monkeypatch):
    # A normal in-progress mode (not Monitor, not Error) sets status active.
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Smoke")
    control_data["updated"] = True
    control_data["next_mode"] = "Stop"
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()
    assert store.read_control()["status"] == "active"


# --------------------------------------------------------------------------
# the mode x status power coupling (THE point)
# --------------------------------------------------------------------------


def test_monitor_error_keeps_power_on(monkeypatch):
    # A Monitor-mode error (status=="monitor" and mode=="Error") keeps the OEM
    # controller powered ON -- the one real 2D interaction between the axes.
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Error")
    control_data["status"] = "monitor"
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()
    names = [name for name, _ in grill.calls]
    assert "power_on" in names
    assert "power_off" not in names


def test_normal_error_powers_off(monkeypatch):
    # A non-Monitor error (status=="active") powers off as usual.
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Error")
    control_data["status"] = "active"
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()
    names = [name for name, _ in grill.calls]
    assert "power_off" in names
    assert "power_on" not in names
