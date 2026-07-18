"""Characterization ("golden master") tests for the mode-FILE safety-trip
transitions -- the writes that a mode's setup_safety()/check_safety() (and the
base skeleton's inner-loop switch-off) perform when they change control['mode']
from INSIDE a work cycle.

These fill inventory coverage gaps #1-#6 (Hold setup_safety->Error/Reignite,
Hold check_safety->Error/Reignite, Smoke in-loop check_safety->Error/Reignite,
base inner-loop switch-off->Stop): today these edges have NO transition-level
test. They are the safety net for repointing those inline writes onto the
request_transition() seam -- if a refactor changes any captured value, that is a
regression to investigate.

METHOD: run-then-freeze, reusing the modes-golden harness (`run_mode`) exactly
as test_modes_golden.py does. The harness's CaptureResult already records
notifications (FakeNotifier.sent), display commands, grill calls and the final
persisted control -- so we assert against those directly.

SAFETY: these run a single per-mode work cycle via `run_work_cycle`; they never
touch controller.py's Shutdown->Stop os.system path (the only os.system call in
the controller lives in controller.py, not in any mode file exercised here).
"""

from tests.characterization.harness import run_mode
from tests.characterization.fixtures import base_settings, base_control, base_pellet_db
from tests.fakes.probes import FakeProbes
from tests.fakes.grill import FakeGrillPlatform


class _SwitchOffGrill(FakeGrillPlatform):
    """Reads the ON/OFF switch as ON exactly once (the pre-loop `last =
    get_input_status()` at base.py:288), then OFF forever -- so the first
    in-loop switch check (base.py:401) sees a change to OFF and trips the
    inner-loop switch-off -> Stop edge."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._input_script = [True]

    def get_input_status(self):
        if self._input_script:
            return self._input_script.pop(0)
        return False


# --------------------------------------------------------------------------
# Hold setup_safety (pre-loop flameout) -> Error / Reignite  (gaps #1, #2)
# --------------------------------------------------------------------------


def test_hold_setup_safety_flameout_error():
    # afterstarttemp (100) < startuptemp (150) with retries == 0 -> evaluate_flameout
    # returns ERROR before the loop even starts. Mirrors the Smoke setup_safety
    # golden but for Hold.
    settings = base_settings()
    control_data = base_control(mode="Hold")
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 100
    control_data["safety"]["reigniteretries"] = 0
    probes = FakeProbes().script([100, 100, 100])
    result = run_mode("Hold", settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes)
    out = result.final_control
    assert out["mode"] == "Error"
    assert out["updated"] is True
    assert "Grill_Error_02" in result.notifications
    assert ("text", "ERROR") in result.display_commands
    # Error branch does NOT decrement reigniteretries.
    assert out["safety"]["reigniteretries"] == 0


def test_hold_setup_safety_flameout_reignite():
    # afterstarttemp (100) < startuptemp (150) with retries == 1 -> REIGNITE.
    settings = base_settings()
    control_data = base_control(mode="Hold")
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 100
    control_data["safety"]["reigniteretries"] = 1
    probes = FakeProbes().script([100, 100, 100])
    result = run_mode("Hold", settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes)
    out = result.final_control
    assert out["mode"] == "Reignite"
    assert out["updated"] is True
    assert out["safety"]["reigniteretries"] == 0  # decremented from 1
    assert out["safety"]["reignitelaststate"] == "Hold"
    assert "Grill_Error_03" in result.notifications
    assert ("text", "Re-Ignite") in result.display_commands


# --------------------------------------------------------------------------
# Hold check_safety (in-loop flameout) -> Error / Reignite  (gaps #3, #4)
# --------------------------------------------------------------------------


def test_hold_check_safety_inloop_flameout_error():
    # setup_safety PASSES (afterstarttemp 200 >= startuptemp 150) so the loop
    # runs; then the in-loop probe read (100 < 150) trips check_safety ERROR
    # with retries == 0, before any actuation.
    settings = base_settings()
    control_data = base_control(mode="Hold")
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 200
    control_data["safety"]["reigniteretries"] = 0
    probes = FakeProbes().script([200, 100, 100, 100])
    result = run_mode("Hold", settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes)
    out = result.final_control
    assert out["mode"] == "Error"
    assert out["updated"] is True
    assert "Grill_Error_02" in result.notifications
    assert ("text", "ERROR") in result.display_commands
    assert out["safety"]["reigniteretries"] == 0


def test_hold_check_safety_inloop_flameout_reignite():
    settings = base_settings()
    control_data = base_control(mode="Hold")
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 200
    control_data["safety"]["reigniteretries"] = 1
    probes = FakeProbes().script([200, 100, 100, 100])
    result = run_mode("Hold", settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes)
    out = result.final_control
    assert out["mode"] == "Reignite"
    assert out["updated"] is True
    assert out["safety"]["reigniteretries"] == 0  # decremented from 1
    assert out["safety"]["reignitelaststate"] == "Hold"
    assert "Grill_Error_03" in result.notifications
    assert ("text", "Re-Ignite") in result.display_commands


# --------------------------------------------------------------------------
# Smoke check_safety (in-loop flameout) -> Error / Reignite  (gap #5)
# The golden Smoke tests trip in setup_safety (pre-loop); this pins the
# distinct in-loop check_safety path (smoke.py:133-148).
# --------------------------------------------------------------------------


def test_smoke_check_safety_inloop_flameout_error():
    settings = base_settings()
    control_data = base_control(mode="Smoke")
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 200
    control_data["safety"]["reigniteretries"] = 0
    probes = FakeProbes().script([200, 100, 100, 100])
    result = run_mode("Smoke", settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes)
    out = result.final_control
    assert out["mode"] == "Error"
    assert out["updated"] is True
    assert "Grill_Error_02" in result.notifications
    assert ("text", "ERROR") in result.display_commands
    assert out["safety"]["reigniteretries"] == 0


def test_smoke_check_safety_inloop_flameout_reignite():
    settings = base_settings()
    control_data = base_control(mode="Smoke")
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 200
    control_data["safety"]["reigniteretries"] = 1
    probes = FakeProbes().script([200, 100, 100, 100])
    result = run_mode("Smoke", settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes)
    out = result.final_control
    assert out["mode"] == "Reignite"
    assert out["updated"] is True
    assert out["safety"]["reigniteretries"] == 0  # decremented from 1
    assert out["safety"]["reignitelaststate"] == "Smoke"
    assert "Grill_Error_03" in result.notifications
    assert ("text", "Re-Ignite") in result.display_commands


# --------------------------------------------------------------------------
# base inner-loop switch-off -> Stop  (gap #6)  base.py:401-409
# --------------------------------------------------------------------------


def test_base_inloop_switch_off_triggers_stop():
    # Non-standalone platform, switch flips OFF during the work cycle: the base
    # skeleton writes mode="Stop", status="active", updated=True and breaks.
    settings = base_settings()
    settings["platform"]["standalone"] = False
    control_data = base_control(mode="Smoke")
    # Keep setup_safety / in-loop safety OK so switch-off is the only trip.
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 200
    grill = _SwitchOffGrill(standalone=False, outputs=tuple(settings["platform"]["outputs"]))
    probes = FakeProbes().script([200, 200, 200])
    result = run_mode(
        "Smoke", settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes, grill=grill
    )
    out = result.final_control
    assert out["mode"] == "Stop"
    assert out["status"] == "active"
    assert out["updated"] is True
