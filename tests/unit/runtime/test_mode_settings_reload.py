"""Pin tests for the on_settings_reload SmartStart-clobber bug.

StartupMode.setup_safety()/SmokeMode.setup_safety() derive
`self.state.cycle.*` (and the `p_mode`/`auger_cycle_time` metrics) from a
SmartStart profile via `profile_cycle()` when SmartStart is enabled. But
`on_settings_reload()` (called whenever a settings save flips
`control['settings_update']`, e.g. an unrelated web-UI edit mid-Startup/
Reignite/Smoke) used to unconditionally recompute from
`smoke_cycle_times(cycle_data)` instead, silently discarding the
profile-derived timing. These tests pin the fixed behavior: reload re-derives
from the ALREADY-selected profile (never re-selects), falls back to
`smoke_cycle_times` when SmartStart is disabled (regression pin for the old
behavior), and clamps + logs instead of crashing when `profile_selected` is
out of range (e.g. the profiles list was shortened by the very settings save
being reloaded).

ReigniteMode inherits StartupMode's on_settings_reload verbatim (no override
-- see controller/runtime/modes/reignite.py), so fixing/pinning StartupMode
covers it; no separate Reignite test is needed.
"""

from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import InMemoryStore
from controller.runtime.clock import ManualClock
from controller.runtime.state import WorkCycleState
from controller.runtime.modes.startup import StartupMode
from controller.runtime.modes.smoke import SmokeMode
from controller.runtime.logic.cycle import smoke_cycle_times
from controller.runtime.logic.smartstart import profile_cycle
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.distance import FakeDistance
from tests.fakes.notifier import FakeNotifier
from tests.fakes.probes import FakeProbes
from tests.characterization.fixtures import base_settings, base_control, base_pellet_db


def _make_mode(mode_cls, control_mode_name, settings):
    control = base_control(mode=control_mode_name)
    pellet_db = base_pellet_db()
    probes = FakeProbes().script([120])
    store = InMemoryStore(control=control, settings=settings, pellet_db=pellet_db)
    grill = FakeGrillPlatform(outputs=tuple(settings["platform"]["outputs"]))
    notifier = FakeNotifier()
    ctx = ControllerContext(
        devices=Devices(grill_platform=grill, probe_complex=probes, dist_device=FakeDistance()),
        store=store,
        notifications=notifier,
        clock=ManualClock(),
    )
    mode = mode_cls(ctx, WorkCycleState())
    mode.settings = settings
    mode.control = control
    mode.state.metrics = {}
    return mode


def _smartstart_settings(profile_selected=1):
    settings = base_settings()
    settings["startup"]["smartstart"]["enabled"] = True
    # Distinct per-profile values so a smoke_cycle_times() fallback is
    # trivially distinguishable from the profile-derived result.
    settings["startup"]["smartstart"]["profiles"] = [
        {"startuptime": 300, "augerontime": 10, "p_mode": 1},
        {"startuptime": 200, "augerontime": 22, "p_mode": 4},
        {"startuptime": 100, "augerontime": 30, "p_mode": 6},
    ]
    return settings


def _stale_generic_cycle(mode):
    """Seed state.cycle with values that differ from BOTH the profile-derived
    and the smoke_cycle_times()-derived results, so a passing assertion can't
    be an accident of unrelated defaults."""
    mode.state.cycle.on_time = -1
    mode.state.cycle.off_time = -1
    mode.state.cycle.cycle_time = -1
    mode.state.cycle.ratio = -1
    mode.state.cycle.raw_ratio = -1
    mode.state.metrics["p_mode"] = -1
    mode.state.metrics["auger_cycle_time"] = -1


def test_startup_reload_smartstart_enabled_preserves_profile_timing():
    settings = _smartstart_settings(profile_selected=1)
    mode = _make_mode(StartupMode, "Startup", settings)
    mode.control["smartstart"]["profile_selected"] = 1
    _stale_generic_cycle(mode)

    mode.on_settings_reload()

    profile = settings["startup"]["smartstart"]["profiles"][1]
    expected_ct, expected_timer, expected_mbits = profile_cycle(profile, settings["cycle_data"])
    generic_ct = smoke_cycle_times(settings["cycle_data"])

    assert mode.state.cycle.on_time == expected_ct.on_time
    assert mode.state.cycle.off_time == expected_ct.off_time
    assert mode.state.cycle.cycle_time == expected_ct.cycle_time
    assert mode.state.cycle.ratio == expected_ct.cycle_ratio
    assert mode.state.cycle.raw_ratio == expected_ct.cycle_ratio
    assert mode.state.startup.timer == expected_timer

    # Must NOT match the generic cycle_data-derived values (the bug).
    assert mode.state.cycle.on_time != generic_ct.on_time
    assert mode.state.cycle.cycle_time != generic_ct.cycle_time

    assert mode.state.metrics["p_mode"] == expected_mbits["p_mode"]
    assert mode.state.metrics["auger_cycle_time"] == expected_mbits["auger_cycle_time"]

    # profile_selected is a mode-entry decision: reload must not re-select.
    assert mode.control["smartstart"]["profile_selected"] == 1


def test_smoke_reload_smartstart_enabled_preserves_profile_timing():
    settings = _smartstart_settings(profile_selected=2)
    mode = _make_mode(SmokeMode, "Smoke", settings)
    mode.control["smartstart"]["profile_selected"] = 2
    _stale_generic_cycle(mode)

    mode.on_settings_reload()

    profile = settings["startup"]["smartstart"]["profiles"][2]
    expected_ct, expected_timer, expected_mbits = profile_cycle(profile, settings["cycle_data"])
    generic_ct = smoke_cycle_times(settings["cycle_data"])

    assert mode.state.cycle.on_time == expected_ct.on_time
    assert mode.state.cycle.off_time == expected_ct.off_time
    assert mode.state.cycle.cycle_time == expected_ct.cycle_time
    assert mode.state.cycle.ratio == expected_ct.cycle_ratio
    assert mode.state.cycle.raw_ratio == expected_ct.cycle_ratio
    assert mode.state.startup.timer == expected_timer

    assert mode.state.cycle.on_time != generic_ct.on_time
    assert mode.state.cycle.cycle_time != generic_ct.cycle_time

    assert mode.state.metrics["p_mode"] == expected_mbits["p_mode"]
    assert mode.state.metrics["auger_cycle_time"] == expected_mbits["auger_cycle_time"]

    assert mode.control["smartstart"]["profile_selected"] == 2


def test_startup_reload_smartstart_disabled_uses_generic_cycle_times():
    settings = base_settings()
    settings["startup"]["smartstart"]["enabled"] = False
    mode = _make_mode(StartupMode, "Startup", settings)
    _stale_generic_cycle(mode)

    mode.on_settings_reload()

    expected_ct = smoke_cycle_times(settings["cycle_data"])
    assert mode.state.cycle.on_time == expected_ct.on_time
    assert mode.state.cycle.off_time == expected_ct.off_time
    assert mode.state.cycle.cycle_time == expected_ct.cycle_time
    assert mode.state.cycle.ratio == expected_ct.cycle_ratio
    assert mode.state.cycle.raw_ratio == expected_ct.cycle_ratio
    assert mode.state.metrics["p_mode"] == settings["cycle_data"]["PMode"]
    assert mode.state.metrics["auger_cycle_time"] == settings["cycle_data"]["SmokeOnCycleTime"]


def test_smoke_reload_smartstart_disabled_uses_generic_cycle_times():
    settings = base_settings()
    settings["startup"]["smartstart"]["enabled"] = False
    mode = _make_mode(SmokeMode, "Smoke", settings)
    _stale_generic_cycle(mode)

    mode.on_settings_reload()

    expected_ct = smoke_cycle_times(settings["cycle_data"])
    assert mode.state.cycle.on_time == expected_ct.on_time
    assert mode.state.cycle.off_time == expected_ct.off_time
    assert mode.state.cycle.cycle_time == expected_ct.cycle_time
    assert mode.state.cycle.ratio == expected_ct.cycle_ratio
    assert mode.state.cycle.raw_ratio == expected_ct.cycle_ratio
    assert mode.state.metrics["p_mode"] == settings["cycle_data"]["PMode"]
    assert mode.state.metrics["auger_cycle_time"] == settings["cycle_data"]["SmokeOnCycleTime"]


def test_startup_reload_clamps_out_of_range_profile_selected():
    # Simulate the profiles list having been SHORTENED by the very settings
    # save that triggered this reload: profile_selected (2) is now
    # out-of-range for a 1-profile list.
    settings = _smartstart_settings()
    settings["startup"]["smartstart"]["profiles"] = [
        settings["startup"]["smartstart"]["profiles"][0],
    ]
    mode = _make_mode(StartupMode, "Startup", settings)
    mode.control["smartstart"]["profile_selected"] = 2
    _stale_generic_cycle(mode)

    mode.on_settings_reload()  # must not raise (IndexError)

    last_valid = 0
    profile = settings["startup"]["smartstart"]["profiles"][last_valid]
    expected_ct, expected_timer, expected_mbits = profile_cycle(profile, settings["cycle_data"])

    assert mode.control["smartstart"]["profile_selected"] == last_valid
    assert mode.state.cycle.on_time == expected_ct.on_time
    assert mode.state.cycle.cycle_time == expected_ct.cycle_time
    assert mode.state.startup.timer == expected_timer
    assert mode.state.metrics["p_mode"] == expected_mbits["p_mode"]


def test_smoke_reload_clamps_out_of_range_profile_selected():
    settings = _smartstart_settings()
    settings["startup"]["smartstart"]["profiles"] = [
        settings["startup"]["smartstart"]["profiles"][0],
    ]
    mode = _make_mode(SmokeMode, "Smoke", settings)
    mode.control["smartstart"]["profile_selected"] = 5
    _stale_generic_cycle(mode)

    mode.on_settings_reload()  # must not raise (IndexError)

    last_valid = 0
    profile = settings["startup"]["smartstart"]["profiles"][last_valid]
    expected_ct, expected_timer, expected_mbits = profile_cycle(profile, settings["cycle_data"])

    assert mode.control["smartstart"]["profile_selected"] == last_valid
    assert mode.state.cycle.on_time == expected_ct.on_time
    assert mode.state.cycle.cycle_time == expected_ct.cycle_time
    assert mode.state.startup.timer == expected_timer
    assert mode.state.metrics["p_mode"] == expected_mbits["p_mode"]
