from functools import partial
from types import SimpleNamespace

import numpy as np

from controller.applied_output import AppliedOutput, FrameFeedbackDisposition, OutputSource
from controller.acados import GreyBoxMPCConfig
from controller.mpc import CalibrationCommand, Controller
from controller.mpc_factory import MpcPairFactory
from controller.runtime.runner import SyncControllerRunner


class _Estimator:
    def update(self, load, temperature):
        delay_states = np.full(8, float(load))
        return np.concatenate((delay_states, np.array([float(temperature), 0.0])))


class _Policy:
    def __init__(self, config: GreyBoxMPCConfig):
        self.config = config
        self.horizon = config.horizon_steps

    def solve(self, _state, **_kwargs):
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
            sequence_q=np.full(self.horizon, 0.4),
            sequence_residual=np.full(self.horizon, 0.4),
            objective=1.0,
            diagnostics=diagnostics,
        )


def _controller(monkeypatch, *, safe_forecast=True):
    monkeypatch.setattr(
        "controller.mpc.MpcPairFactory",
        partial(
            MpcPairFactory,
            ekf_factory=lambda **_parameters: _Estimator(),
            kf_factory=lambda **_parameters: _Estimator(),
            solver_factory=_Policy,
        ),
    )
    if safe_forecast:

        class SafeForecast:
            def forecast(self, q_future, ambient_future):
                return np.full(len(q_future), 101.0)

        monkeypatch.setattr(
            "controller.mpc.GreyBoxPredictionAdapter.from_controller",
            lambda controller: SafeForecast(),
        )
    controller = Controller({"n_delay": 8, "enable_fan_input": False}, "C", {"u_max": 0.9})
    controller.set_target(110.0)
    # Hold publishes the grill's configured maximum every tick; without it the
    # controller has no ceiling and fails closed on every probe.
    controller.set_safety_ceiling_c(260.0)
    return controller


def _start(revision=1):
    return CalibrationCommand(
        action="start",
        command_revision=revision,
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
    assert (
        result.allocation.normalized_combustion_load
        == result.baseline_allocation.normalized_combustion_load + result.calibration.probe_q
    )


def test_duplicate_calibration_revision_is_idempotent_and_stop_returns_baseline(monkeypatch):
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start())
    first = runner.latest_from(100.0).calibration
    first_generation = first.command_generation
    first_progress = first.progress
    first_events = first.events

    runner.request_calibration(_start())
    duplicate = runner.latest_from(100.0).calibration

    assert duplicate.command_generation == first_generation
    assert duplicate.progress == first_progress
    assert first_events + duplicate.events == first_events

    runner.request_calibration(replace(_start(2), action="stop"))
    stopped = runner.latest_from(100.0)

    assert first.probe_q > 0.0
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
    runner.set_safety_ceiling_c(130.0)  # the forecast peaks past this, later in the horizon
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
        AppliedOutput(
            active.allocation.auger_duty,
            OutputSource.CONTROLLER,
            1.0,
            producing_result_revision=active.revision,
            producing_calibration_revision=active.calibration.command_revision,
            producing_calibration_action=active.calibration.command_action,
            producing_calibration_generation=active.calibration.command_generation,
            feedback_disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        )
    )
    feedback = runner.latest_from(100.0)

    assert skipped.calibration.progress.eligible_observations == 0
    assert feedback.calibration.progress.eligible_observations == 1
    assert feedback.calibration.progress.positive_observations == 1
    assert feedback.calibration.command_revision == 1
    assert feedback.calibration.command_action == "start"


def test_routine_frame_reports_preserve_the_latched_probe_until_one_completed_frame(monkeypatch):
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start())
    active = runner.latest_from(100.0)

    for timestamp in (1.0, 2.0, 3.0):
        controller.set_output(
            AppliedOutput(
                active.allocation.auger_duty,
                OutputSource.CONTROLLER,
                timestamp,
                producing_result_revision=active.revision,
                producing_calibration_revision=active.calibration.command_revision,
                producing_calibration_action=active.calibration.command_action,
                producing_calibration_generation=1,
                feedback_disposition=FrameFeedbackDisposition.PROGRESS,
                sample_complete=True,
            )
        )
        progress = runner.latest_from(100.0)
        assert progress.calibration.active is True
        assert progress.calibration.progress.eligible_observations == 0

    controller.set_output(
        AppliedOutput(
            active.allocation.auger_duty,
            OutputSource.CONTROLLER,
            20.0,
            producing_result_revision=active.revision,
            producing_calibration_revision=active.calibration.command_revision,
            producing_calibration_action=active.calibration.command_action,
            producing_calibration_generation=1,
            feedback_disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        )
    )
    advanced = runner.latest_from(100.0)

    assert advanced.calibration.progress.eligible_observations == 1


def test_old_completed_frame_cannot_advance_a_newer_start(monkeypatch):
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start(1))
    old = runner.latest_from(100.0)
    runner.request_calibration(_start(2))
    newer = runner.latest_from(100.0)

    controller.set_output(
        AppliedOutput(
            old.allocation.auger_duty,
            OutputSource.CONTROLLER,
            20.0,
            producing_result_revision=old.revision,
            producing_calibration_revision=old.calibration.command_revision,
            producing_calibration_action=old.calibration.command_action,
            producing_calibration_generation=1,
            feedback_disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        )
    )
    after_old_completion = runner.latest_from(100.0)

    assert newer.calibration.command_revision == 2
    assert after_old_completion.calibration.command_revision == 2
    assert after_old_completion.calibration.active is True
    assert after_old_completion.calibration.progress.eligible_observations == 0


def test_delivered_frames_realize_both_probe_polarities_in_fifo_order(monkeypatch):
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start())
    result = runner.latest_from(100.0)

    for frame in range(3):
        controller.set_output(
            AppliedOutput(
                result.allocation.auger_duty,
                OutputSource.CONTROLLER,
                frame + 1.0,
                producing_result_revision=result.revision,
                producing_calibration_revision=result.calibration.command_revision,
                producing_calibration_action=result.calibration.command_action,
                producing_calibration_generation=result.calibration.command_generation,
                feedback_disposition=FrameFeedbackDisposition.COMPLETE,
                sample_complete=True,
            )
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


def test_partial_frame_feedback_cancels_without_counting_an_observation(monkeypatch):
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.request_calibration(_start())
    active = runner.latest_from(100.0)
    controller.set_output(
        AppliedOutput(
            active.allocation.auger_duty,
            OutputSource.CONTROLLER,
            1.0,
            producing_result_revision=active.revision,
            producing_calibration_revision=active.calibration.command_revision,
            producing_calibration_action=active.calibration.command_action,
            producing_calibration_generation=active.calibration.command_generation,
            feedback_disposition=FrameFeedbackDisposition.DISCARDED,
            sample_complete=False,
        )
    )
    cancelled = runner.latest_from(100.0)

    assert cancelled.calibration.progress.eligible_observations == 0
    assert cancelled.calibration.command_action == "safety-cancel"


def test_probing_binds_to_the_grill_maximum_in_force_now_not_the_one_a_command_carried(monkeypatch):
    """The grill maximum is the ONLY ceiling, and it is read every tick: lowering
    it mid-cook must bind the next probe, not the next operator action."""
    controller = _controller(monkeypatch)
    runner = SyncControllerRunner(controller)
    runner.set_safety_ceiling_c(100.0)
    runner.request_calibration(_start())

    rejected = runner.latest_from(100.0)

    assert not rejected.calibration.active
    assert "overshoot_prediction" in rejected.calibration.events[-1].reasons
