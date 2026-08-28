"""Characterization ("golden master") tests for the mode-FILE safety-trip
transitions -- the writes that a mode's setup_safety()/check_safety() (and the
base skeleton's inner-loop switch-off) perform when they change control['mode']
from INSIDE a work cycle.

These fill coverage gaps for transition edges that today have NO transition-level
test: Hold setup_safety->Error/Reignite, Hold check_safety->Error/Reignite,
Smoke in-loop check_safety->Error/Reignite, and base inner-loop switch-off->Stop.
They are the safety net for routing those inline writes through
request_transition() -- if a refactor changes any captured value, that is a
regression to investigate.

METHOD: run-then-freeze, reusing the modes-golden harness (`run_mode`) exactly
as test_modes_golden.py does. The harness's CaptureResult already records
notifications (FakeNotifier.sent), display commands, grill calls and the final
persisted control -- so we assert against those directly.

SAFETY: these run a single per-mode work cycle via `run_work_cycle`; they never
touch controller.py's Shutdown->Stop halt path (the only shutdown_system() call
in the controller lives in controller.py, not in any mode file exercised here).
"""

import importlib
import importlib.abc
import sys

import controller.runtime.runner as runtime_runner
from common.modes import Mode
from controller.runtime.modes.smoke import SmokeMode
from controller.runtime.runner import ControllerUpdateResult
from controller.runtime.state import WorkCycleState
from probes.thermocouple_health import ThermocoupleFault, ThermocoupleHealthReport
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization.harness import make_ctx, run_mode
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.probes import FakeProbes
from tests.fakes.runner import FakeControllerRunner


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
# Smoke check_safety (in-loop flameout) -> Error / Reignite The golden Smoke
# tests trip in setup_safety (pre-loop); this pins the distinct in-loop
# check_safety path (smoke.py:133-148).
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
# base inner-loop switch-off -> Stop (base.py:401-409)
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
# Guard-phase / actuation-timing characterization for the declarative phased
# guard engine.
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
    # First in-loop probe is over maxtemp -> pre_act max-temp trips on tick 1
    # before on_tick, so the controller is never submitted a temp and no auger
    # actuation occurs.
    settings = base_settings()
    settings["safety"]["maxtemp"] = 500
    control_data = base_control(mode="Hold")
    control_data["primary_setpoint"] = 225
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 200  # setup_safety OK
    probes = FakeProbes().script([200, 550, 550])
    runner = FakeControllerRunner(period=0.0).script(
        [ControllerUpdateResult(cycle_ratio=0.5, fan=None, input_temperature=0.0)] * 4
    )
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
    # on_tick never ran on the trip tick: no controller submit or auger-on command.
    assert runner.submitted_temps == []
    assert [call for call in result.grill_calls if call[0] == "auger_on"] == []


def test_check_safety_flameout_breaks_before_actuation():
    # setup_safety passes (afterstarttemp 200 >= startuptemp 150); the in-loop
    # probe (100 < 150) trips pre_act check_safety flameout on tick 1 before
    # on_tick, so the controller is never submitted a temp or starts the auger.
    settings = base_settings()
    control_data = base_control(mode="Hold")
    control_data["primary_setpoint"] = 225
    control_data["safety"]["startuptemp"] = 150
    control_data["safety"]["afterstarttemp"] = 200
    control_data["safety"]["reigniteretries"] = 0  # -> ERROR verdict
    probes = FakeProbes().script([200, 100, 100])
    runner = FakeControllerRunner(period=0.0).script(
        [ControllerUpdateResult(cycle_ratio=0.5, fan=None, input_temperature=0.0)] * 4
    )
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
    assert [call for call in result.grill_calls if call[0] == "auger_on"] == []


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
    runner = _StopRecordingRunner(period=0.0).script(
        [ControllerUpdateResult(cycle_ratio=0.5, fan=None, input_temperature=0.0)] * 2
    )
    grill = FakeGrillPlatform()
    result = run_mode(
        "Hold",
        settings=settings,
        control_data=control_data,
        pellet_db=base_pellet_db(),
        probes=probes,
        grill=grill,
        runner=runner,
    )
    assert result.final_control["mode"] == "Error"
    # Loop skipped: on_tick never ran.
    assert runner.submitted_temps == []
    # Teardown still ran: Hold.teardown (which only runs post-loop) stopped the
    # runner. The shared cleanup and Hold's framed-pulse reset leave both
    # combustion outputs safely off; their relative command order is not a
    # safety contract.
    assert runner.stopped is True
    output_status = grill.get_output_status()
    assert output_status["auger"] is False
    assert output_status["igniter"] is False


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


class _LearningTrajectoryHookSpy:
    def __init__(self):
        self.events = []
        self.samples = []

    def mode_entered(self, event):
        self.events.append(("entered", event))

    def mode_exited(self, event):
        self.events.append(("exited", event))

    def observe_temperature(self, sample):
        self.samples.append(sample)
        self.events.append(("sample", sample))

    def observe_hold_frame(self, observation):
        self.events.append(("hold-frame", observation))

    def intervention(self, boundary):
        self.events.append(("intervention", boundary))

    def configuration_changed(self, boundary):
        self.events.append(("configuration", boundary))

    def status(self):
        return {}

    def barrier(self, timeout=2.0):
        self.events.append(("barrier", timeout))
        return True

    def close(self):
        self.events.append(("close", None))


def test_shared_smoke_skeleton_emits_one_canonical_sample_per_post_entry_probe_read():
    recorder = _LearningTrajectoryHookSpy()
    probes = FakeProbes().script([200])
    settings = base_settings()
    settings["cycle_data"]["SmokeOnCycleTime"] = 0.1
    settings["cycle_data"]["SmokeOffCycleTime"] = 0.1
    settings["cycle_data"]["PMode"] = 0

    run_mode(
        "Smoke",
        settings=settings,
        control_data=base_control(mode="Smoke"),
        pellet_db=base_pellet_db(),
        probes=probes,
        probe_cap=8,
        learning_trajectory=recorder,
    )

    assert recorder.events[0][0] == "entered"
    assert recorder.events[0][1].effective_mode == "Smoke"
    assert recorder.events[0][1].persisted_mode == "Smoke"
    exits = [
        (index, event)
        for index, (kind, event) in enumerate(recorder.events)
        if kind == "exited"
    ]
    assert len(exits) == 1
    exit_index, exit_event = exits[0]
    assert exit_event.effective_mode == "Smoke"
    assert all(
        index < exit_index
        for index, (kind, _event) in enumerate(recorder.events)
        if kind == "sample"
    )
    assert len(recorder.samples) == len(probes.read_calls)
    assert all(sample.chamber_temperature == 200 for sample in recorder.samples)
    assert all(
        left.monotonic_ms <= right.monotonic_ms
        for left, right in zip(recorder.samples, recorder.samples[1:])
    )



def test_recipe_exit_emits_actual_next_effective_handler() -> None:
    recorder = _LearningTrajectoryHookSpy()
    settings = base_settings()
    control = base_control(mode="Recipe")
    control["recipe"]["step"] = 0
    ctx, _grill, _notifier = make_ctx(
        settings,
        control,
        base_pellet_db(),
        FakeProbes().script([200]),
    )
    ctx.learning_trajectory = recorder
    ctx.trajectory_next_effective_mode = Mode.HOLD
    handler = SmokeMode(ctx, WorkCycleState())
    handler.settings = settings
    handler.control = control

    handler._emit_trajectory_mode_exited(control, 100, 1_000_100)

    exit_event = recorder.events[-1][1]
    assert exit_event.effective_mode == "Smoke"
    assert exit_event.next_effective_mode == "Hold"
    assert exit_event.reason is None


def test_preflight_probe_sample_and_fault_exit_are_emitted_without_hardware_reorder() -> None:
    recorder = _LearningTrajectoryHookSpy()
    fault = ThermocoupleHealthReport.confirmed_hardware(
        (ThermocoupleFault.OPEN,),
        now=0.0,
        status=0x10,
    )
    probes = FakeProbes().script([200]).script_health([{"Grill": fault}])

    result = run_mode(
        "Smoke",
        settings=base_settings(),
        control_data=base_control(mode="Smoke"),
        pellet_db=base_pellet_db(),
        probes=probes,
        learning_trajectory=recorder,
    )

    assert [kind for kind, _event in recorder.events] == [
        "entered",
        "sample",
        "intervention",
        "exited",
    ]
    assert result.grill_calls[:4] == [
        ("igniter_off", ()),
        ("auger_off", ()),
        ("fan_off", ()),
        ("power_off", ()),
    ]

class _ForbiddenMpcFinder(importlib.abc.MetaPathFinder):
    def __init__(self) -> None:
        self.attempts: list[str] = []

    def find_spec(self, fullname, path=None, target=None):
        del path, target
        if fullname == "controller.mpc" or fullname.startswith(
            ("controller.mpc.", "controller.mpc_")
        ):
            self.attempts.append(fullname)
            raise AssertionError(f"Smoke imported forbidden MPC dependency {fullname}")


def test_smoke_has_no_mpc_import_build_or_call_and_mode_hardware_order_is_unchanged(monkeypatch):
    finder = _ForbiddenMpcFinder()
    with monkeypatch.context() as scoped:
        for loaded_name in tuple(sys.modules):
            if (
                loaded_name in {
                    "controller.runtime.modes.smoke",
                    "controller.mpc",
                }
                or loaded_name.startswith(
                    ("controller.mpc.", "controller.mpc_")
                )
            ):
                scoped.delitem(sys.modules, loaded_name, raising=False)
        scoped.setattr(sys, "meta_path", [finder, *sys.meta_path])
        loaded = importlib.import_module("controller.runtime.modes.smoke")
    assert loaded.SmokeMode is not None
    assert finder.attempts == []

    def forbidden_build(*_args, **_kwargs):
        raise AssertionError("Smoke attempted to construct or call an MPC/controller runner")

    settings = base_settings()
    settings["cycle_data"]["SmokeOnCycleTime"] = 0.1
    settings["cycle_data"]["SmokeOffCycleTime"] = 0.1
    settings["cycle_data"]["PMode"] = 0
    with monkeypatch.context() as scoped:
        scoped.setattr(runtime_runner, "build_runner", forbidden_build)
        smoke = run_mode(
            "Smoke",
            settings=settings,
            control_data=base_control(mode="Smoke"),
            pellet_db=base_pellet_db(),
            probes=FakeProbes().script([200]),
            probe_cap=8,
        )

    hold_control = base_control(mode="Hold")
    hold_control["primary_setpoint"] = 225
    hold_control["safety"]["startuptemp"] = 150
    hold_control["safety"]["afterstarttemp"] = 200
    hold_runner = FakeControllerRunner(period=0.0).script(
        [ControllerUpdateResult(cycle_ratio=0.25, fan=None, input_temperature=200.0)] * 12
    )
    hold = run_mode(
        "Hold",
        settings=base_settings(),
        control_data=hold_control,
        pellet_db=base_pellet_db(),
        probes=FakeProbes().script([200]),
        runner=hold_runner,
        probe_cap=8,
    )

    expected_setup = [
        ("igniter_off", ()),
        ("auger_off", ()),
        ("fan_on", (None,)),
        ("power_on", ()),
    ]
    assert smoke.grill_calls[:4] == expected_setup
    assert hold.grill_calls[:4] == expected_setup
    assert smoke.grill_calls[-2:] == [("auger_off", ()), ("igniter_off", ())]
    assert hold.grill_calls[-4:] == [
        ("auger_off", ()),
        ("fan_off", ()),
        ("igniter_off", ()),
        ("power_off", ()),
    ]
