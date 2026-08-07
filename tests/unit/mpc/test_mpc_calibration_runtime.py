from dataclasses import replace

import numpy as np

from controller.mpc import CalibrationCommand, Controller
from controller.runtime.runner import SyncControllerRunner


class _Estimator:
    def update(self, load, temperature):
        return np.array([20.0, 0.0])


class _Policy:
    def make_step(self, state):
        return np.array([[0.4]])


def _controller(monkeypatch):
    monkeypatch.setattr(Controller, "_build_for", lambda self, cfg: (_Estimator(), None, object(), _Policy()))
    controller = Controller({"n_delay": 0, "enable_fan_input": False}, "C", {"u_max": 0.9})
    controller.set_target(110.0)
    return controller


def _start(revision=1):
    return CalibrationCommand(
        action="start",
        command_revision=revision,
        maximum_temperature_c=130.0,
        ambient_c=20.0,
        ambient_source="configured",
        empty_grill_confirmed=True,
        pellets_confirmed=True,
    )


def test_ordinary_mpc_result_has_zero_probe_and_identical_allocations(monkeypatch):
    controller = _controller(monkeypatch)
    result = SyncControllerRunner(controller).latest_from(100.0)

    assert result.calibration.probe_q == 0.0
    assert result.baseline_allocation == result.allocation


def test_calibration_overlay_returns_baseline_and_combined_allocations(monkeypatch):
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start())
    result = runner.latest_from(100.0)

    assert result.calibration.active is True
    assert result.calibration.probe_q > 0.0
    assert result.baseline_allocation.normalized_combustion_load < result.allocation.normalized_combustion_load
    assert result.allocation.normalized_combustion_load == result.baseline_allocation.normalized_combustion_load + result.calibration.probe_q


def test_duplicate_calibration_revision_is_idempotent_and_stop_returns_baseline(monkeypatch):
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start())
    runner.request_calibration(_start())
    active = runner.latest_from(100.0)
    runner.request_calibration(replace(_start(2), action="stop"))
    stopped = runner.latest_from(100.0)

    assert active.calibration.probe_q > 0.0
    assert stopped.calibration.probe_q == 0.0
    assert stopped.allocation == stopped.baseline_allocation
