from dataclasses import replace

import numpy as np

from controller.applied_output import AppliedOutput, OutputSource
from controller.mpc import CalibrationCommand, Controller
from controller.runtime.runner import SyncControllerRunner


class _Estimator:
    def update(self, load, temperature):
        return np.array([20.0, 0.0])


class _Policy:
    def make_step(self, state):
        return np.array([[0.4]])

def _controller(monkeypatch, *, safe_forecast=True):
    monkeypatch.setattr(Controller, "_build_for", lambda self, cfg: (_Estimator(), None, object(), _Policy()))
    if safe_forecast:
        class SafeForecast:
            def forecast(self, q_future, ambient_future):
                return np.full(len(q_future), 101.0)

        monkeypatch.setattr(
            "controller.mpc.GreyBoxPredictionAdapter.from_controller",
            lambda controller: SafeForecast(),
        )
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



def test_fifo_commands_are_all_consumed_before_one_result_and_keep_provenance(monkeypatch):
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start(1))
    runner.request_calibration(replace(_start(2), action="pause"))

    result = runner.latest_from(100.0)

    assert result.calibration.command_revision == 2
    assert result.calibration.command_action == "pause"
    assert result.calibration.active
    assert result.calibration.probe_q == 0.0
    assert result.calibration.events[-1].kind == "paused"


def test_delayed_grey_box_overshoot_fails_closed_without_querying_challenger(monkeypatch):
    class DelayedOvershoot:
        def forecast(self, q_future, ambient_future):
            assert q_future[0] > 0.4
            return np.concatenate((np.array((101.0,)), np.full(len(q_future) - 1, 131.0)))

    monkeypatch.setattr(
        "controller.mpc.GreyBoxPredictionAdapter.from_controller",
        lambda controller: DelayedOvershoot(),
    )
    controller = _controller(monkeypatch, safe_forecast=False)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start())

    result = runner.latest_from(100.0)

    assert not result.calibration.active
    assert result.calibration.probe_q == 0.0
    assert result.calibration.events[-1].reasons == ("overshoot_prediction",)


def test_calibration_advances_once_per_delivered_output_not_per_solve(monkeypatch):
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start())
    active = runner.latest_from(100.0)
    skipped = runner.latest_from(100.0)

    controller.set_output(
        AppliedOutput(active.allocation.auger_duty, OutputSource.CONTROLLER, 1.0)
    )
    feedback = runner.latest_from(100.0)

    assert skipped.calibration.progress.eligible_observations == 0
    assert feedback.calibration.progress.eligible_observations == 1
    assert feedback.calibration.progress.positive_observations == 1
    assert feedback.calibration.command_revision == 0
    assert feedback.calibration.command_action == "none"


def test_delivered_frames_realize_both_probe_polarities_in_fifo_order(monkeypatch):
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start())
    result = runner.latest_from(100.0)

    for frame in range(3):
        controller.set_output(
            AppliedOutput(result.allocation.auger_duty, OutputSource.CONTROLLER, frame + 1.0)
        )
        result = runner.latest_from(100.0)

    assert result.calibration.progress.eligible_observations == 3
    assert result.calibration.progress.positive_observations == 2
    assert result.calibration.progress.negative_observations == 1


def test_safety_cancellation_uses_no_operator_revision_and_later_command_is_consumed(monkeypatch):
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start())
    runner.latest_from(100.0)

    runner.cancel_calibration("lid-open")
    cancelled = runner.latest_from(100.0)
    runner.request_calibration(_start(2))
    restarted = runner.latest_from(100.0)

    assert cancelled.calibration.command_revision == 0
    assert cancelled.calibration.command_action == "safety-cancel"
    assert restarted.calibration.command_revision == 2
    assert restarted.calibration.command_action == "start"