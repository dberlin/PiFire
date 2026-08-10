from __future__ import annotations

import collections

import pytest
from types import SimpleNamespace

from common.control_trace import AllocationClampReason, ControllerBranch, MpcFailureState
from controller import mpc, pid, pid_sp
from controller.base import MpcTraceDiagnostics, PidSpTraceDiagnostics, PidTraceDiagnostics
from controller.mpc_allocator import AllocationResult

CYCLE_DATA = {"u_min": 0.1, "u_max": 0.9}


def test_pid_trace_diagnostics_reproduce_completed_update(monkeypatch):
    clock = iter((0.0, 0.0, 0.0, 100.0, 102.0))
    monkeypatch.setattr(pid.time, "time", lambda: next(clock))
    core = pid.Controller({"PB": 20.0, "Ti": 10.0, "Td": 5.0, "center": 0.5}, "F", dict(CYCLE_DATA))
    core.set_target(200.0)
    core.last = 190.0
    core.last_update = 98.0

    assert core.update(198.0) == pytest.approx(-0.38)

    diagnostic = core.trace_diagnostics()
    assert isinstance(diagnostic, PidTraceDiagnostics)
    assert diagnostic.error == pytest.approx(-2.0)
    assert diagnostic.proportional_term == pytest.approx(0.6)
    assert diagnostic.integral_accumulator == pytest.approx(-4.0)
    assert diagnostic.integral_term == pytest.approx(0.02)
    assert diagnostic.derivative_input == pytest.approx(8.0)
    assert diagnostic.derivative_state == pytest.approx(4.0)
    assert diagnostic.raw_output == pytest.approx(-0.38)
    assert diagnostic.final_output == pytest.approx(-0.38)
    assert diagnostic.previous_temperature == pytest.approx(190.0)
    assert diagnostic.previous_update_time == pytest.approx(98.0)


def test_pid_sp_trace_diagnostics_reproduce_completed_update(monkeypatch):
    clock = iter((0.0, 0.0, 0.0, 0.0, 100.0))
    monkeypatch.setattr(pid_sp.time, "time", lambda: next(clock))
    # tau/theta are no longer configuration: the identifier learns them, so a
    # controller built here carries no model at all.
    core = pid_sp.Controller(
        {"PB": 20.0, "Ti": 10.0, "Td": 5.0, "stable_window": 12.0},
        "F",
        dict(CYCLE_DATA),
    )
    core.set_target(200.0)
    core.last = 190.0
    core.last_update = 98.0
    core.last_set_time = 0.0
    core.new_target = False

    core.update(198.0)

    diagnostic = core.trace_diagnostics()
    assert isinstance(diagnostic, PidSpTraceDiagnostics)
    assert diagnostic.error == pytest.approx(-2.0)
    assert diagnostic.measured_rate == pytest.approx(4.0)
    # This controller no longer extrapolates a future temperature from a
    # configured tau/theta. The Smith predictor removes identified dead time
    # from the reading instead, and nothing is identified on a fresh core, so
    # the temperature it selects is the measured one and the error taken from it
    # is the plain error. tau/theta read zero for the same reason.
    assert diagnostic.predicted_temperature == pytest.approx(198.0)
    assert diagnostic.predicted_error == pytest.approx(-2.0)
    assert diagnostic.tau_seconds == pytest.approx(0.0)
    assert diagnostic.theta_seconds == pytest.approx(0.0)
    assert diagnostic.stable_window_seconds == pytest.approx(12.0)
    assert diagnostic.branch is ControllerBranch.NONE
    assert diagnostic.previous_temperature == pytest.approx(190.0)
    assert diagnostic.previous_update_time == pytest.approx(98.0)


class _Estimator:
    def update(self, applied_load, measured_temperature):
        assert applied_load == 0.7
        assert measured_temperature == 100.0
        return mpc.np.array([1.0, 2.0, 3.0, 4.0])


class _Solver:
    def solve(self, x_hat, *, setpoint_c, q_previous, equilibrium_q):
        assert tuple(x_hat) == (1.0, 2.0, 3.0, 4.0)
        assert setpoint_c == 120.0
        assert q_previous == 0.7
        assert equilibrium_q == 0.0
        diagnostics = SimpleNamespace(
            status=0,
            backend_status=0,
            iterations=1,
            solve_time_s=0.001,
            objective=1.0,
            kkt_residual=0.0,
            constraint_residual=0.0,
            warm_started=True,
        )
        return SimpleNamespace(
            sequence_q=mpc.np.array([1.0, 1.0]),
            sequence_residual=mpc.np.array([1.0, 1.0]),
            objective=1.0,
            diagnostics=diagnostics,
        )


def _bare_mpc_controller():
    core = object.__new__(mpc.Controller)
    core.units = "C"
    core.cfg = {
        "n_delay": 2,
        "n_horizon": 2,
        "h_amb": 2.0,
        "T_amb": 20.0,
        "sigma": 0.0,
        "K_Q": 400.0,
        "fan_min_pct": 40.0,
        "fan_max_pct": 100.0,
        "enable_fan_input": False,
    }
    core._set_point_c = 120.0
    core._applied_combustion_load = 0.7
    core._last_combustion_load = 0.8
    core._last_raw_combustion_load = 0.8
    core._consecutive_policy_failures = 0
    core._history = collections.deque(maxlen=2)
    core._model_revision = 3
    core._model_meta = None
    core._calibration_feedback = collections.deque()
    core._calibration_operations = collections.deque()
    core._trace_calibration = mpc.CalibrationDecision(False, 0.0, None, mpc.CalibrationProgress())
    core.mpc = _Solver()
    core._activation_output_authorized = True
    core._native_failure_diagnostics = None
    core._active_activation_record = None
    core.estimator = _Estimator()
    core.u_max = 0.9
    return core


def _allocation(_load, **_):
    return AllocationResult(
        normalized_combustion_load=1.0,
        auger_duty=0.5,
        fan_duty=None,
        u_max=0.9,
        fan_min_pct=40.0,
        fan_max_pct=100.0,
        fan_enabled=False,
        auger_clamp_reason=AllocationClampReason.NONE,
        fan_clamp_reason=AllocationClampReason.NONE,
    )


def test_mpc_trace_diagnostics_capture_one_solve_without_recomputing_policy(monkeypatch):
    core = _bare_mpc_controller()
    monotonic = iter((10.0, 10.25))
    monkeypatch.setattr(mpc.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(mpc.time, "time", lambda: 1000.0)
    monkeypatch.setattr(mpc, "allocate", _allocation)

    assert core.update(100.0) == {"cycle_ratio": 0.5, "fan": {"duty": None}}

    diagnostic = core.trace_diagnostics()
    assert isinstance(diagnostic, MpcTraceDiagnostics)
    assert diagnostic.state_names == ("q0", "q1", "T_c", "d")
    assert diagnostic.state_values == (1.0, 2.0, 3.0, 4.0)
    assert diagnostic.disturbance_estimate == pytest.approx(4.0)
    assert diagnostic.raw_policy_firing_load == pytest.approx(1.0)
    assert diagnostic.bounded_firing_load == pytest.approx(1.0)
    assert diagnostic.model_revision == 3
    assert diagnostic.model_provenance == "configured"
    assert diagnostic.failure_state is MpcFailureState.SUCCESS
    assert diagnostic.solve_start_monotonic == pytest.approx(10.0)
    assert diagnostic.solve_end_monotonic == pytest.approx(10.25)
    assert diagnostic.solve_duration_seconds == pytest.approx(0.25)
    assert diagnostic.applied_combustion_load == pytest.approx(0.7)


def test_mpc_failure_diagnostics_omit_unknown_raw_policy_components(monkeypatch):
    class _FailingSolver:
        def solve(self, *_args, **_kwargs):
            raise RuntimeError("solve failed")

    core = _bare_mpc_controller()
    core.mpc = _FailingSolver()
    core._last_combustion_load = 0.8
    monotonic = iter((10.0, 10.25))
    monkeypatch.setattr(mpc.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(mpc.time, "time", lambda: 1000.0)
    monkeypatch.setattr(mpc, "allocate", _allocation)

    core.update(100.0)

    diagnostic = core.trace_diagnostics()
    assert diagnostic.failure_state is MpcFailureState.POLICY_EXCEPTION
    assert diagnostic.raw_policy_firing_load is None
    assert diagnostic.equilibrium_feed_forward is None
    assert diagnostic.residual_move is None
    assert diagnostic.bounded_firing_load == pytest.approx(0.8)


def test_mpc_policy_timing_excludes_failure_logging(monkeypatch):
    class _FailingSolver:
        def solve(self, *_args, **_kwargs):
            raise RuntimeError("solve failed")

    core = _bare_mpc_controller()
    core.mpc = _FailingSolver()
    events = []
    monotonic = iter((10.0, 10.25))
    monkeypatch.setattr(mpc.time, "monotonic", lambda: (events.append("clock"), next(monotonic))[1])
    monkeypatch.setattr(mpc.time, "time", lambda: 1000.0)
    monkeypatch.setattr(mpc, "allocate", _allocation)
    monkeypatch.setattr("builtins.print", lambda *_: events.append("log"))

    core.update(100.0)

    assert events[:2] == ["clock", "clock"]
    assert events[2:] == ["log"]


@pytest.mark.parametrize(
    ("current", "new_target", "last", "expected"),
    [
        (198.0, True, 0.0, ControllerBranch.INITIALIZATION),
        (150.0, False, 190.0, ControllerBranch.FULL_HEAT),
        (199.0, True, 190.0, ControllerBranch.TARGET_REACHED),
        (185.0, False, 190.0, ControllerBranch.RESET),
        (230.0, False, 190.0, ControllerBranch.OVERSHOOT),
    ],
)
def test_pid_sp_trace_diagnostics_identify_each_control_branch(monkeypatch, current, new_target, last, expected):
    clock = iter((0.0, 0.0, 0.0, 0.0, 100.0))
    monkeypatch.setattr(pid_sp.time, "time", lambda: next(clock))
    core = pid_sp.Controller({"PB": 20.0, "tau": 10.0, "theta": 5.0, "stable_window": 12.0}, "F", dict(CYCLE_DATA))
    core.set_target(200.0)
    core.last = last
    core.last_update = 98.0
    core.last_set_time = 0.0
    core.new_target = new_target

    core.update(current)

    diagnostic = core.trace_diagnostics()
    assert diagnostic.branch is expected
    assert diagnostic.new_target_before is new_target
    assert diagnostic.new_target_after is core.new_target
