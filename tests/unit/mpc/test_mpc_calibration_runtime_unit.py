from dataclasses import FrozenInstanceError, dataclass, replace
from types import MappingProxyType
import operator

import numpy as np
import pytest

from controller.applied_output import AppliedOutput, FrameFeedbackDisposition, OutputSource
from controller.model_learning.calibration import CalibrationDecision
from controller.mpc_allocator import AllocationResult, allocate
from controller.mpc_calibration import CalibrationCommand, MpcCalibrationRuntime


class _Clock:
    def __init__(self) -> None:
        self.now = 10.0
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.now


class _Forecast:
    def __init__(self, temperatures: tuple[float, ...] = (101.0,)) -> None:
        self.temperatures = temperatures
        self.calls: list[tuple[np.ndarray, np.ndarray]] = []

    def __call__(self, q_future: np.ndarray, ambient_future: np.ndarray) -> np.ndarray:
        self.calls.append((q_future.copy(), ambient_future.copy()))
        if len(self.temperatures) == 1:
            return np.full(q_future.size, self.temperatures[0])
        return np.asarray(self.temperatures, dtype=np.float64)


@dataclass(frozen=True, slots=True)
class _CompletedResult:
    revision: int
    calibration: CalibrationDecision | None
    baseline_allocation: AllocationResult | None


def _runtime(*, ceiling_c: float = 260.0) -> tuple[MpcCalibrationRuntime, _Clock]:
    clock = _Clock()
    runtime = MpcCalibrationRuntime(horizon_steps=4, u_max=0.9, clock=clock)
    runtime.set_target_c(110.0)
    runtime.set_safety_ceiling_c(ceiling_c)
    return runtime, clock


def _command(revision: int = 1, action: str = "start") -> CalibrationCommand:
    return CalibrationCommand(action, revision, 20.0, "configured", True, True)


def _advance(
    runtime: MpcCalibrationRuntime,
    forecast: _Forecast | None = None,
    *,
    baseline_q: float = 0.4,
    temperature_c: float = 100.0,
) -> CalibrationDecision:
    return runtime.advance(baseline_q, temperature_c, forecast or _Forecast())


def _registered_result(revision: int, decision: CalibrationDecision, baseline_q: float = 0.4) -> _CompletedResult:
    baseline = allocate(
        baseline_q,
        u_max=0.9,
        fan_min_pct=0.0,
        fan_max_pct=100.0,
        enable_fan=False,
    )
    return _CompletedResult(revision, decision, baseline)


def _output(
    result: _CompletedResult,
    *,
    realized_q: float,
    timestamp: float,
    disposition: FrameFeedbackDisposition,
    sample_complete: bool,
    source: OutputSource = OutputSource.CONTROLLER,
    provenance: CalibrationDecision | None = None,
) -> AppliedOutput:
    decision = provenance or result.calibration
    assert decision is not None
    realized = allocate(
        realized_q,
        u_max=0.9,
        fan_min_pct=0.0,
        fan_max_pct=100.0,
        enable_fan=False,
    )
    return AppliedOutput(
        realized.auger_duty,
        source,
        timestamp,
        producing_result_revision=result.revision,
        producing_calibration_revision=decision.command_revision,
        producing_calibration_action=decision.command_action,
        producing_calibration_generation=decision.command_generation,
        feedback_disposition=disposition,
        sample_complete=sample_complete,
    )


def test_public_command_has_one_named_module_without_legacy_reexport() -> None:
    import controller.mpc as legacy_module

    assert not hasattr(legacy_module, "CalibrationCommand")


def test_command_validation_requires_revision_confirmations_and_finite_ambient() -> None:
    with pytest.raises(ValueError, match="invalid calibration action"):
        _command(action="unknown")
    with pytest.raises(ValueError, match="revision must be positive"):
        _command(0)
    with pytest.raises(ValueError, match="temperatures must be finite"):
        replace(_command(), ambient_c=float("nan"))
    with pytest.raises(ValueError, match="ambient source"):
        replace(_command(), ambient_source="guess")
    with pytest.raises(ValueError, match="confirmations are required"):
        replace(_command(), pellets_confirmed=False)


def test_command_validation_rejects_non_integer_seed() -> None:
    with pytest.raises(ValueError, match="seed must be an integer"):
        replace(_command(), seed=True)
    with pytest.raises(ValueError, match="seed must be an integer"):
        replace(_command(), seed=1.5)


@pytest.mark.parametrize("horizon_steps", (0, True, 1.5))
def test_runtime_rejects_invalid_horizon(horizon_steps) -> None:
    with pytest.raises(ValueError, match="horizon_steps must be a positive integer"):
        MpcCalibrationRuntime(horizon_steps=horizon_steps, u_max=0.9)


@pytest.mark.parametrize("u_max", (0.0, True, float("nan")))
def test_runtime_rejects_invalid_output_ceiling(u_max) -> None:
    with pytest.raises(ValueError, match="u_max must be finite and positive"):
        MpcCalibrationRuntime(horizon_steps=4, u_max=u_max)


def test_runtime_rejects_non_callable_clock() -> None:
    with pytest.raises(TypeError, match="clock must be callable"):
        MpcCalibrationRuntime(horizon_steps=4, u_max=0.9, clock=0)


def test_runtime_rejects_nonfinite_dynamic_temperatures() -> None:
    runtime, _clock = _runtime()
    with pytest.raises(ValueError, match="target must be finite"):
        runtime.set_target_c(float("inf"))
    with pytest.raises(ValueError, match="safety ceiling must be finite"):
        runtime.set_safety_ceiling_c(float("nan"))


def test_runtime_rejects_wrong_public_boundary_types() -> None:
    runtime, _clock = _runtime()
    with pytest.raises(TypeError, match="command must be CalibrationCommand"):
        runtime.request("start")
    with pytest.raises(TypeError, match="forecast must be callable"):
        runtime.advance(0.4, 100.0, 7)
    with pytest.raises(TypeError, match="applied must be AppliedOutput"):
        runtime.register_output("output")


def test_revision_ordering_rejects_stale_and_ignores_duplicate_commands() -> None:
    runtime, _clock = _runtime()
    runtime.request(_command(2))
    runtime.request(_command(2))

    with pytest.raises(ValueError, match="revision must be monotonic"):
        runtime.request(_command(1))

    decision = _advance(runtime)
    assert decision.command_revision == 2
    assert decision.command_generation == 1
    assert [event.kind for event in decision.events].count("start_accepted") == 1


def test_fifo_start_pause_resume_stop_and_reset_preserve_command_provenance() -> None:
    runtime, _clock = _runtime()
    runtime.request(_command(1))
    runtime.request(_command(2, "pause"))
    paused = _advance(runtime)
    assert paused.active is True
    assert paused.probe_q == 0.0
    assert paused.command_revision == 2
    assert paused.command_generation == 1
    assert paused.events[-1].kind == "paused"

    runtime.request(_command(3, "resume"))
    resumed = _advance(runtime)
    assert resumed.active is True
    assert resumed.probe_q > 0.0
    assert resumed.events[-1].kind == "resumed"

    runtime.request(_command(4, "stop"))
    stopped = _advance(runtime)
    assert stopped.active is False
    assert stopped.command_action == "stop"
    assert stopped.outcome == "stopped"

    runtime.request(_command(5, "reset-progress"))
    reset = _advance(runtime)
    assert reset.command_revision == 5
    assert reset.command_action == "reset-progress"
    assert reset.command_generation == 1
    assert reset.progress.eligible_observations == 0
    assert reset.events[-1].kind == "progress_reset"


def test_safety_cancel_does_not_consume_an_operator_revision() -> None:
    runtime, _clock = _runtime()
    runtime.request(_command(1))
    _advance(runtime)
    runtime.cancel("lid-open")

    cancelled = _advance(runtime)
    assert cancelled.active is False
    assert cancelled.command_revision == 0
    assert cancelled.command_action == "safety-cancel"
    assert cancelled.outcome_reasons == ("lid-open",)

    runtime.request(_command(2))
    restarted = _advance(runtime)
    assert restarted.active is True
    assert restarted.command_revision == 2
    assert restarted.command_action == "start"
    assert restarted.command_generation == 2

    with pytest.raises(ValueError, match="non-empty string"):
        runtime.cancel("")


def test_forecast_uses_explicit_horizon_and_ambient_and_checks_maximum() -> None:
    runtime, _clock = _runtime(ceiling_c=130.0)
    forecast = _Forecast((101.0, 102.0, 131.0, 103.0))
    runtime.request(_command())

    decision = _advance(runtime, forecast)

    assert decision.active is False
    assert decision.outcome_reasons == ("overshoot_prediction",)
    assert len(forecast.calls) == 1
    q_future, ambient_future = forecast.calls[0]
    assert q_future.shape == (4,)
    assert np.all(q_future > 0.4)
    assert np.array_equal(ambient_future, np.full(4, 20.0))


def test_forecast_and_clock_are_lazy_explicit_dependencies() -> None:
    runtime, clock = _runtime()
    forecast = _Forecast()

    idle = _advance(runtime, forecast)
    assert idle.active is False
    assert forecast.calls == []
    assert clock.calls == 0

    runtime.request(_command())
    _advance(runtime, forecast)
    assert len(forecast.calls) == 1
    assert clock.calls == 1


def test_forecast_failure_and_dynamic_safety_ceiling_fail_closed() -> None:
    runtime, _clock = _runtime()
    runtime.set_safety_ceiling_c(100.0)
    runtime.request(_command())
    unsafe = _advance(runtime)
    assert unsafe.outcome_reasons == ("overshoot_prediction",)

    runtime.request(_command(2))
    malformed = _advance(runtime, _Forecast((101.0, 102.0)))
    assert malformed.outcome_reasons == ("prediction_invalid",)


def test_progress_feedback_preserves_frame_until_complete_then_advances_once() -> None:
    runtime, _clock = _runtime()
    runtime.request(_command())
    active = _advance(runtime)
    result = _registered_result(7, active)
    runtime.register_result(result)

    runtime.register_output(
        _output(
            result,
            realized_q=0.4 + active.probe_q,
            timestamp=1.0,
            disposition=FrameFeedbackDisposition.PROGRESS,
            sample_complete=True,
        )
    )
    unchanged = _advance(runtime)
    assert unchanged.progress.eligible_observations == 0

    runtime.register_output(
        _output(
            result,
            realized_q=0.4 + active.probe_q,
            timestamp=2.0,
            disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        )
    )
    advanced = _advance(runtime)
    assert advanced.progress.eligible_observations == 1
    assert advanced.progress.positive_observations == 1

    runtime.register_output(
        _output(
            result,
            realized_q=0.4 + active.probe_q,
            timestamp=3.0,
            disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        )
    )
    assert _advance(runtime).progress.eligible_observations == 1


def test_discarded_and_incomplete_feedback_cancel_without_observation() -> None:
    for disposition, sample_complete in (
        (FrameFeedbackDisposition.DISCARDED, False),
        (FrameFeedbackDisposition.COMPLETE, False),
    ):
        runtime, _clock = _runtime()
        runtime.request(_command())
        active = _advance(runtime)
        result = _registered_result(3, active)
        runtime.register_result(result)
        runtime.register_output(
            _output(
                result,
                realized_q=0.4,
                timestamp=1.0,
                disposition=disposition,
                sample_complete=sample_complete,
            )
        )

        cancelled = _advance(runtime)

        assert cancelled.active is False
        assert cancelled.command_action == "safety-cancel"
        assert cancelled.progress.eligible_observations == 0
        assert cancelled.outcome_reasons == ("discarded_frame",)


def test_unknown_actuation_cancels_completed_feedback() -> None:
    runtime, _clock = _runtime()
    runtime.request(_command())
    active = _advance(runtime)
    result = _registered_result(1, active)
    runtime.register_result(result)
    runtime.register_output(
        _output(
            result,
            realized_q=0.4,
            timestamp=1.0,
            disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
            source=OutputSource.MANUAL_OVERRIDE,
        )
    )

    cancelled = _advance(runtime)

    assert cancelled.active is False
    assert cancelled.outcome_reasons == ("unknown_actuation",)


def test_old_or_wrong_provenance_frames_cannot_advance_a_new_generation() -> None:
    runtime, _clock = _runtime()
    runtime.request(_command(1))
    old = _advance(runtime)
    old_result = _registered_result(1, old)
    runtime.register_result(old_result)
    runtime.request(_command(2))
    current = _advance(runtime)

    runtime.register_output(
        _output(
            old_result,
            realized_q=0.4 + old.probe_q,
            timestamp=1.0,
            disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        )
    )
    assert _advance(runtime).progress.eligible_observations == 0

    current_result = _registered_result(2, current)
    runtime.register_result(current_result)
    runtime.register_output(
        _output(
            current_result,
            realized_q=0.4 + current.probe_q,
            timestamp=2.0,
            disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
            provenance=old,
        )
    )
    after_wrong_provenance = _advance(runtime)
    assert after_wrong_provenance.command_revision == 2
    assert after_wrong_provenance.progress.eligible_observations == 0


def test_nonincreasing_completed_frame_timestamps_fail_closed_as_discontinuous() -> None:
    runtime, _clock = _runtime()
    runtime.request(_command())
    first = _advance(runtime)
    first_result = _registered_result(1, first)
    runtime.register_result(first_result)
    runtime.register_output(
        _output(
            first_result,
            realized_q=0.4 + first.probe_q,
            timestamp=2.0,
            disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        )
    )
    current = _advance(runtime)

    current_result = _registered_result(2, current)
    runtime.register_result(current_result)
    runtime.register_output(
        _output(
            current_result,
            realized_q=0.4 + current.probe_q,
            timestamp=1.0,
            disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        )
    )

    cancelled = _advance(runtime)
    assert cancelled.active is False
    assert cancelled.outcome_reasons == ("discontinuity",)


def test_result_registration_ignores_incomplete_frames_and_purges_stale_frames() -> None:
    runtime, _clock = _runtime()
    runtime.request(_command())
    active = _advance(runtime)
    baseline = _registered_result(1, active).baseline_allocation
    runtime.register_result(_CompletedResult(0, active, baseline))
    runtime.register_result(_CompletedResult(1, None, baseline))
    runtime.register_result(_CompletedResult(1, active, None))
    stale = _registered_result(1, active)
    current = _registered_result(2, active)
    runtime.register_result(stale)
    runtime.register_result(current)

    runtime.register_output(
        _output(
            current,
            realized_q=0.4 + active.probe_q,
            timestamp=1.0,
            disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        )
    )
    runtime.register_output(
        _output(
            stale,
            realized_q=0.4 + active.probe_q,
            timestamp=2.0,
            disposition=FrameFeedbackDisposition.COMPLETE,
            sample_complete=True,
        )
    )

    assert _advance(runtime).progress.eligible_observations == 1


def test_command_decision_and_status_are_immutable_owned_state() -> None:
    runtime, _clock = _runtime()
    command = _command()
    runtime.request(command)
    decision = _advance(runtime)
    status = runtime.status()

    with pytest.raises(FrozenInstanceError):
        setattr(command, "seed", 4)
    with pytest.raises(FrozenInstanceError):
        setattr(decision, "probe_q", 0.0)
    with pytest.raises(TypeError):
        operator.setitem(status, "generation", 99)

    assert isinstance(status, MappingProxyType)
    assert status["decision"] is decision
    assert runtime.advance(0.4, 100.0, _Forecast()).events == ()
