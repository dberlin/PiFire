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
from tests.fakes.runner import FakeControllerRunner
from controller.runtime.runner import NormalizedOutput


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


# ==========================================================================
# Task 12 -- guard-phase / actuation-timing characterization (Phase 2 net).
#
# These pin the behaviors a phased guard-engine rewrite could disturb:
#   (a) an in-loop max-temp trip breaks BEFORE actuation (on_tick never runs on
#       the trip tick);
#   (b) a check_safety flameout trip breaks before on_tick (same);
#   (c) setup_safety returning "Inactive" skips the loop entirely but STILL runs
#       teardown (post-loop cleanup + mode teardown);
#   (d) intra-phase priority within pre_act: max-temp is evaluated BEFORE
#       check_safety, so on a tick where BOTH would trip, max-temp (Error /
#       Grill_Error_01) wins over the flameout reignite verdict.
#
# DISCRIMINATOR: Hold.on_tick's FIRST action is self._runner.submit(ptemp)
# (before any auger/fan actuation), so runner.submitted_temps == [] is a
# rigorous "on_tick did not execute this tick" proof -- on_tick is the sole
# in-loop actuator, so no auger/fan cycling happened either. (The existing
# test_modes_golden.test_hold_over_maxtemp_does_not_submit... pins the same
# order via submitted_temps; these re-pin it as the FSM actuation-timing
# contract with explicit no-actuation assertions.)
# ==========================================================================


class _StopRecordingRunner(FakeControllerRunner):
    """FakeControllerRunner that records whether teardown called stop()."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_maxtemp_trip_breaks_before_actuation():
    # First in-loop probe is over maxtemp -> pre_act max-temp trips on tick 1,
    # before on_tick, so the controller is never submitted a temp and the auger
    # is never cycled on the trip tick.
    settings = base_settings()
    settings["safety"]["maxtemp"] = 500
    control_data = base_control(mode="Hold")
    control_data["primary_setpoint"] = 225
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 200  # setup_safety OK
    probes = FakeProbes().script([200, 550, 550])
    runner = FakeControllerRunner(period=0.0).script([NormalizedOutput(cycle_ratio=0.5, fan=None)] * 4)
    result = run_mode(
        "Hold",
        settings=settings,
        control_data=control_data,
        pellet_db=base_pellet_db(),
        probes=probes,
        grill=FakeGrillPlatform(),
        runner=runner,
    )
    assert result.final_control["mode"] == "Error"
    assert "Grill_Error_01" in result.notifications
    # on_tick never ran on the trip tick: no controller submit, no in-loop auger_on.
    assert runner.submitted_temps == []
    assert [c for c in result.grill_calls if c[0] == "auger_on"] == [("auger_on", ())]  # Hold setup only


def test_check_safety_flameout_breaks_before_actuation():
    # setup_safety passes (afterstarttemp 200 >= startuptemp 150); the in-loop
    # probe (100 < 150) trips pre_act check_safety flameout on tick 1, before
    # on_tick -- so again the controller is never submitted a temp.
    settings = base_settings()
    control_data = base_control(mode="Hold")
    control_data["primary_setpoint"] = 225
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 200
    control_data["safety"]["reigniteretries"] = 0  # -> ERROR verdict
    probes = FakeProbes().script([200, 100, 100])
    runner = FakeControllerRunner(period=0.0).script([NormalizedOutput(cycle_ratio=0.5, fan=None)] * 4)
    result = run_mode(
        "Hold",
        settings=settings,
        control_data=control_data,
        pellet_db=base_pellet_db(),
        probes=probes,
        grill=FakeGrillPlatform(),
        runner=runner,
    )
    assert result.final_control["mode"] == "Error"
    assert "Grill_Error_02" in result.notifications
    assert runner.submitted_temps == []  # on_tick never ran on the trip tick
    assert [c for c in result.grill_calls if c[0] == "auger_on"] == [("auger_on", ())]


def test_setup_safety_inactive_skips_loop_but_runs_teardown():
    # A pre-loop (setup_safety) flameout returns "Inactive" -> the work loop is
    # skipped entirely (on_tick never runs), but post-loop cleanup + the
    # mode-specific teardown STILL run.
    settings = base_settings()
    control_data = base_control(mode="Hold")
    control_data["primary_setpoint"] = 225
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 100  # < startuptemp -> flameout at setup
    control_data["safety"]["reigniteretries"] = 0  # -> ERROR verdict, Inactive
    probes = FakeProbes().script([100, 100, 100])
    runner = _StopRecordingRunner(period=0.0).script([NormalizedOutput(cycle_ratio=0.5, fan=None)] * 2)
    result = run_mode(
        "Hold",
        settings=settings,
        control_data=control_data,
        pellet_db=base_pellet_db(),
        probes=probes,
        grill=FakeGrillPlatform(),
        runner=runner,
    )
    assert result.final_control["mode"] == "Error"
    # Loop skipped: on_tick never ran.
    assert runner.submitted_temps == []
    # Teardown still ran: Hold.teardown (which only runs post-loop) stopped the
    # runner, and the post-loop universal cleanup turned the auger/igniter off.
    # (endtime metric stays 0.0 here because the skipped loop never advanced the
    # ManualClock, so it is not a usable teardown discriminator.)
    assert runner.stopped is True
    assert result.grill_calls[-2:] == [("auger_off", ()), ("igniter_off", ())]


def test_pre_act_priority_maxtemp_beats_check_safety_on_same_tick():
    # A single in-loop ptemp that is BOTH over maxtemp AND below startuptemp:
    # pre_act evaluates max-temp BEFORE check_safety, so max-temp wins -> Error
    # via Grill_Error_01, and the flameout reignite verdict never fires (retries
    # not decremented, no Grill_Error_02/03).
    settings = base_settings()
    settings["safety"]["maxtemp"] = 100
    control_data = base_control(mode="Smoke")
    control_data["safety"]["startuptemp"] = 200
    control_data["safety"]["afterstarttemp"] = 250  # setup_safety OK
    control_data["safety"]["reigniteretries"] = 1  # would be REIGNITE if flameout won
    probes = FakeProbes().script([150, 150, 150])  # 150 > maxtemp(100) AND < startuptemp(200)
    result = run_mode("Smoke", settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes)
    out = result.final_control
    assert out["mode"] == "Error"
    assert "Grill_Error_01" in result.notifications  # max-temp trip
    assert "Grill_Error_02" not in result.notifications
    assert "Grill_Error_03" not in result.notifications
    assert out["safety"]["reigniteretries"] == 1  # flameout reignite never fired
