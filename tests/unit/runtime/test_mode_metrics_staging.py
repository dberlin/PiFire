"""Pin tests for the smoke-cycle metrics staged onto the run's metrics row.

`p_mode` and `auger_cycle_time` were assigned in Startup/Smoke's
`_init_smoke_cycle()`, which `setup()` calls BEFORE `ControlMode.run()` stamps
the row (`append_metric()` then `self.state.metrics = read_metrics()`). That
reassignment replaced the dict wholesale, so both values were discarded and the
run reported the fresh row's zeros for its whole life:

  * `status_data['p_mode']` -- the attached display's P-MODE pill and
    `/api/get/status` -- read P-0 no matter what `cycle_data.PMode` was set to,
    while the web dashboard reported the setting and so disagreed with the
    display sitting on the grill.
  * the metrics row, and the cookfile built from it, recorded `p_mode` and
    `auger_cycle_time` as 0 for every session.

The auger timing itself was never affected: `state.cycle.*` is derived
separately in the same method and was always correct. This is a reporting bug.

Staging now runs from the `on_metrics_stamped()` hook, which fires after the
row exists. SmartStart still wins where it applies -- `setup_safety()` runs
later still and overwrites `p_mode` from the selected profile, which is the
number actually driving the cycle during a ramp.

ReigniteMode inherits StartupMode's hook verbatim (controller/runtime/modes/
reignite.py overrides only the startup timestamp and the MQTT publish), so it
is covered by the Startup cases.
"""

import controller.runtime.store as store_mod

from common.defaults import default_metrics
from controller.runtime.store import InMemoryStore
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization.harness import run_mode
from tests.fakes.probes import FakeProbes

# Distinct from the shipped defaults (PMode 2 / SmokeOnCycleTime 15) and from
# the 0 a fresh metrics row carries, so neither a default nor the bug can make
# an assertion pass by accident.
PMODE = 4
ON_CYCLE = 23


def _run(mode, settings):
    return run_mode(
        mode,
        settings=settings,
        control_data=base_control(mode=mode),
        pellet_db=base_pellet_db(),
        probes=FakeProbes().script([200]),
        probe_cap=15,
    )


def _settings(smartstart_p_mode=None):
    settings = base_settings()
    settings["cycle_data"]["PMode"] = PMODE
    settings["cycle_data"]["SmokeOnCycleTime"] = ON_CYCLE
    # base_settings shrinks this to 0.2s, which lets Startup exit on its own
    # timer inside four ManualClock ticks -- before the 0.5s display publish
    # gate has ever fired, leaving nothing to assert about. Termination stays
    # bounded by probe_cap, the harness's sanctioned bound for a mode with no
    # natural exit in reach.
    settings["startup"]["duration"] = 5
    if smartstart_p_mode is not None:
        settings["startup"]["smartstart"]["enabled"] = True
        settings["startup"]["smartstart"]["profiles"] = [
            {"startuptime": 300, "augerontime": 10, "p_mode": smartstart_p_mode},
        ]
        settings["startup"]["smartstart"]["temp_range_list"] = []
    return settings


def test_first_mode_persists_a_new_cook_session_identity(monkeypatch):
    generated_ids = iter(("cook-session-7", "smoke-row-id"))
    monkeypatch.setattr(store_mod, "generate_uuid", lambda: next(generated_ids))

    result = _run("Smoke", _settings())

    assert result.final_control["cook_id"] == "cook-session-7"
    assert result.final_metrics["id"] == "smoke-row-id"


def test_later_mode_reuses_persisted_cook_session_identity(monkeypatch):
    monkeypatch.setattr(store_mod, "generate_uuid", lambda: "smoke-row-id")
    control = base_control(mode="Smoke")
    control["cook_id"] = "existing-cook-session"

    result = run_mode(
        "Smoke",
        settings=_settings(),
        control_data=control,
        pellet_db=base_pellet_db(),
        probes=FakeProbes().script([200]),
        probe_cap=15,
    )

    assert result.final_control["cook_id"] == "existing-cook-session"
    assert result.final_metrics["id"] == "smoke-row-id"


def test_startup_recovers_legacy_prime_carry_over_identity(monkeypatch):
    settings = _settings()
    control = base_control(mode="Startup")
    control["cook_id"] = None
    store = InMemoryStore(control=control, settings=settings, pellet_db=base_pellet_db())
    generated_ids = iter(("prime-row-id", "startup-row-id"))
    monkeypatch.setattr(store_mod, "generate_uuid", lambda: next(generated_ids))
    store.append_metric(dict(default_metrics(), mode="Prime"))
    prime_identity = store.read_metrics()["id"]

    result = run_mode(
        "Startup",
        settings=settings,
        control_data=control,
        pellet_db=base_pellet_db(),
        probes=FakeProbes().script([200]),
        probe_cap=15,
        store=store,
    )

    assert result.final_control["cook_id"] == prime_identity
    assert result.final_metrics["id"] == "startup-row-id"


def test_smoke_publishes_the_configured_p_mode_to_the_display():
    result = _run("Smoke", _settings())

    assert result.final_status["p_mode"] == PMODE


def test_startup_publishes_the_configured_p_mode_to_the_display():
    result = _run("Startup", _settings())

    assert result.final_status["p_mode"] == PMODE


def test_smoke_records_the_cycle_settings_on_its_metrics_row():
    result = _run("Smoke", _settings())

    assert result.final_metrics["p_mode"] == PMODE
    assert result.final_metrics["auger_cycle_time"] == ON_CYCLE


def test_startup_records_the_cycle_settings_on_its_metrics_row():
    result = _run("Startup", _settings())

    assert result.final_metrics["p_mode"] == PMODE
    assert result.final_metrics["auger_cycle_time"] == ON_CYCLE


def test_smartstart_profile_still_wins_over_the_configured_p_mode():
    # The profile is what drives the cycle during a SmartStart ramp, so the
    # number reported has to be the profile's -- staging the setting must not
    # land AFTER setup_safety() and undo that.
    result = _run("Smoke", _settings(smartstart_p_mode=7))

    assert result.final_status["p_mode"] == 7
    assert result.final_metrics["p_mode"] == 7
