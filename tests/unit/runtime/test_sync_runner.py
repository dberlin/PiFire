import json

from controller.applied_output import AppliedOutput, OutputSource
from controller.runtime.runner import ControllerUpdateResult, SyncControllerRunner, build_runner, _build_core
from common.control_trace import ActuationMode, ResultStaleState


class _Core:
    def __init__(self):
        self.target = None
        self.period = 5.0

    def set_target(self, sp):
        self.target = sp

    def update(self, temp):
        return {"cycle_ratio": 0.4, "fan": {"duty": 60}}

    def get_control_period(self):
        return self.period

    def actuation_mode(self):
        return ActuationMode.FRAMED_PULSE

    def get_status(self):
        return {"target": self.target}

    def trace_diagnostics(self):
        return None

    def trace_allocation(self):
        return None


def test_sync_runner_normalizes_dict_output():
    r = SyncControllerRunner(_Core())
    r.set_target(225)
    out = r.latest_from(200.0)
    assert isinstance(out, ControllerUpdateResult)
    assert out.cycle_ratio == 0.4
    assert out.fan == {"duty": 60}
    assert out.input_temperature == 200.0


def test_sync_runner_float_output_has_no_fan():
    class FloatCore(_Core):
        def update(self, temp):
            return 0.25

    out = SyncControllerRunner(FloatCore()).latest_from(190.0)
    assert out.cycle_ratio == 0.25 and out.fan is None


def test_sync_runner_preserves_actuation_mode_and_reports_solve_quality():
    class Clock:
        def __init__(self):
            self.value = 0.0

        def __call__(self):
            return self.value

        def advance(self, seconds):
            self.value += seconds

    class TimedCore(_Core):
        def __init__(self, clock):
            super().__init__()
            self.clock = clock
            self.duration = 6.0

        def actuation_mode(self):
            return ActuationMode.FRAMED_PULSE

        def update(self, temp):
            self.clock.advance(self.duration)
            return 0.25

    clock = Clock()
    core = TimedCore(clock)
    runner = SyncControllerRunner(core, monotonic_clock=clock, wall_clock=clock)

    first = runner.latest_from(190.0)
    assert runner.actuation_mode() is ActuationMode.FRAMED_PULSE
    assert first.solve_duration_seconds == 6.0
    assert first.result_age_seconds == 0.0
    assert first.deadline_miss_count == first.consecutive_deadline_miss_count == 1
    assert first.stale_state is ResultStaleState.FRESH

    core.duration = 0.0
    second = runner.latest_from(191.0)
    assert second.revision == first.revision + 1
    assert second.deadline_miss_count == 1
    assert second.consecutive_deadline_miss_count == 0
    assert runner.controller_state()["result_stale_state"] == ResultStaleState.FRESH.value


def test_sync_runner_result_revision_and_status_match_one_completed_update():
    class AtomicCore(_Core):
        def __init__(self):
            super().__init__()
            self.completed = 0

        def update(self, temp):
            self.completed += 1
            return 0.25

        def get_status(self):
            return {"completed": self.completed}

    runner = SyncControllerRunner(AtomicCore())
    first = runner.latest_from(190.0)
    second = runner.latest_from(191.0)

    assert (first.revision, first.status) == (1, {"completed": 1})
    assert (second.revision, second.status) == (2, {"completed": 2})
    assert first.solve_end_monotonic >= first.solve_start_monotonic
    assert first.solve_duration_seconds == first.solve_end_monotonic - first.solve_start_monotonic
    assert (first.input_temperature, second.input_temperature) == (190.0, 191.0)


def test_sync_controller_state_thaws_nested_completed_status_without_mutating_result():
    class NestedStatusCore(_Core):
        def __init__(self):
            super().__init__()
            self.status = {"nested": {"samples": [1.0]}}

        def get_status(self):
            return self.status

    core = NestedStatusCore()
    runner = SyncControllerRunner(core)
    result = runner.latest_from(190.0)
    core.status["nested"]["samples"].append(2.0)

    state = runner.controller_state()
    assert state["nested"] == {"samples": [1.0]}
    assert json.loads(json.dumps(state)) == state
    state["nested"]["samples"].append(3.0)
    assert result.status == {"nested": {"samples": (1.0,)}}
    assert runner.controller_state()["nested"] == {"samples": [1.0]}


class _RecordingLogger:
    def __init__(self):
        self.exceptions = []
        self.errors = []

    def exception(self, msg):
        self.exceptions.append(msg)

    def error(self, msg):
        self.errors.append(msg)


def test_build_runner_logs_on_load_failure_when_logger_given():
    settings = {"controller": {"selected": "does_not_exist", "config": {}}, "globals": {"units": "F"}, "cycle_data": {}}
    control = {"primary_setpoint": 225}
    logger = _RecordingLogger()

    runner, status = build_runner(settings, control, logger=logger)

    assert runner is None
    assert status == "Inactive"
    # Two exceptions, not one: build_runner now ALSO attempts the fallback
    # controller before giving up. This settings dict has no "pid" config
    # either, so the fallback fails too and the cycle really is Inactive.
    assert [
        "Error occurred loading controller module. Trace dump: ",
        "Error occurred building the [pid] controller. Trace dump: ",
    ] == logger.exceptions
    # And the user is told, rather than only the log.
    assert any("neither could the fallback" in msg for msg in logger.errors)


def test_build_runner_does_not_require_logger():
    settings = {"controller": {"selected": "does_not_exist", "config": {}}, "globals": {"units": "F"}, "cycle_data": {}}
    control = {"primary_setpoint": 225}

    runner, status = build_runner(settings, control)

    assert runner is None
    assert status == "Inactive"


def test_build_core_logs_on_load_failure_when_logger_given():
    settings = {"controller": {"selected": "does_not_exist", "config": {}}, "globals": {"units": "F"}, "cycle_data": {}}
    control = {"primary_setpoint": 225}
    logger = _RecordingLogger()

    core, status = _build_core(settings, control, logger=logger)

    assert core is None
    assert status == "Inactive"
    assert len(logger.exceptions) == 1


def test_build_core_never_returns_active_when_set_target_raises():
    """Pins the contract ControllerBase.get_status() (controller/base.py) relies
    on: a core only reaches "Active" -- and only then gets wrapped in a runner
    that might call get_status() -- if set_target() already succeeded on it,
    because construction and set_target() share the same try/except here."""
    import sys
    import types

    class _RaisesOnSetTarget:
        def __init__(self, config, units, cycle_data):
            pass

        def set_target(self, sp):
            raise RuntimeError("boom")

    fake_module = types.ModuleType("controller.faketype")
    fake_module.Controller = _RaisesOnSetTarget
    sys.modules["controller.faketype"] = fake_module
    try:
        settings = {
            "controller": {"selected": "faketype", "config": {"faketype": {}}},
            "globals": {"units": "F"},
            "cycle_data": {},
        }
        control = {"primary_setpoint": 225}

        core, status = _build_core(settings, control)

        assert core is None
        assert status == "Inactive"
    finally:
        del sys.modules["controller.faketype"]


def test_sync_runner_wants_async_reflects_core_and_stop_is_noop():
    from controller.runtime.runner import SyncControllerRunner

    class _Core:
        def __init__(self, wants):
            self._wants = wants

        def wants_async(self):
            return self._wants

        def actuation_mode(self):
            return ActuationMode.FRAMED_PULSE

    # Delegates to the core (not hardcoded): True core -> True, False core -> False.
    assert SyncControllerRunner(_Core(True)).wants_async() is True
    assert SyncControllerRunner(_Core(False)).wants_async() is False
    SyncControllerRunner(_Core(False)).stop()  # exists + harmless no-op for the sync runner


class _RecordingCore:
    def __init__(self, status=None):
        self.applied = []
        self._status = status
        self.restored = None
        self.snapshot = {"revision": 3, "K": 700.0}

    def update(self, temp):
        return 0.5

    def set_target(self, sp):
        pass

    def get_control_period(self):
        return None

    def commands_fan(self):
        return False

    def wants_async(self):
        return False

    def actuation_mode(self):
        return ActuationMode.FRAMED_PULSE

    def set_output(self, applied):
        self.applied.append(applied)

    def get_status(self):
        return self._status

    def get_model_snapshot(self):
        return self.snapshot

    def restore_model(self, snapshot):
        self.restored = snapshot
        return True

    def trace_diagnostics(self):
        return None

    def trace_allocation(self):
        return None


def test_sync_runner_forwards_set_output():
    core = _RecordingCore()
    runner = SyncControllerRunner(core)
    applied = AppliedOutput(0.4, OutputSource.CONTROLLER, 12.0, requested=0.4)
    runner.set_output(applied)
    assert core.applied == [applied]


def test_sync_runner_forwards_snapshot_and_restore():
    core = _RecordingCore()
    runner = SyncControllerRunner(core)
    assert runner.get_model_snapshot() == {"revision": 3, "K": 700.0}
    assert runner.restore_model({"revision": 9}) is True
    assert core.restored == {"revision": 9}


def test_sync_runner_restore_model_propagates_rejection():
    class _RejectingCore(_RecordingCore):
        def restore_model(self, snapshot):
            self.restored = snapshot
            return False

    core = _RejectingCore()
    assert SyncControllerRunner(core).restore_model({"revision": 1}) is False
    assert core.restored == {"revision": 1}


def test_controller_state_prefers_get_status():
    runner = SyncControllerRunner(_RecordingCore(status={"K": 700.0}))
    assert runner.controller_state() == {"K": 700.0}


def test_controller_state_treats_empty_status_dict_as_present():
    # {} is falsy but not None: get_status() answered, so it wins over the
    # dunder-dict fallback rather than being mistaken for "no answer".
    core = _RecordingCore(status={})
    core.p = 0.25
    assert SyncControllerRunner(core).controller_state() == {}


def test_controller_state_does_not_expose_core_internals_when_status_absent():
    core = _RecordingCore(status=None)
    core.p = 0.25
    assert SyncControllerRunner(core).controller_state() == {}


def test_controller_state_from_get_status_is_a_copy():
    # _RecordingCore.get_status() returns the same cached dict on every call,
    # so a runner that handed back that dict as-is would let this mutation
    # leak into the next read -- a fresh-dict-per-call get_status() could not
    # fail this test.
    core = _RecordingCore(status={"K": 700.0})
    runner = SyncControllerRunner(core)
    state = runner.controller_state()
    state["K"] = 999
    assert runner.controller_state() == {"K": 700.0}
