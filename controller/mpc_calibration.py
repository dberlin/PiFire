"""Calibration command, transition, provenance, and output-feedback ownership."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import math
import time
from types import MappingProxyType
from typing import Protocol, TypeAlias

import numpy as np
import numpy.typing as npt

from controller.applied_output import AppliedOutput, FrameFeedbackDisposition
from controller.model_learning.calibration import (
    CalibrationCommand as _CoordinatorCalibrationCommand,
    CalibrationCoordinator,
    CalibrationDecision,
    CalibrationProgress,
    CalibrationRuntimeContext,
)
from controller.mpc_allocator import AllocationResult, normalized_load_from_auger_duty

FloatArray: TypeAlias = npt.NDArray[np.float64]
CalibrationClock = Callable[[], float]


def _forecast_unavailable(
    _q_future: FloatArray,
    _ambient_future: FloatArray,
) -> FloatArray:
    raise RuntimeError("calibration forecast is unavailable")


class TemperatureForecast(Protocol):
    """Forecast temperatures from explicit future load and ambient vectors."""

    def __call__(self, q_future: FloatArray, ambient_future: FloatArray) -> FloatArray: ...


class CompletedCalibrationResult(Protocol):
    """Immutable completed-frame fields consumed by calibration feedback."""

    @property
    def revision(self) -> int: ...

    @property
    def calibration(self) -> CalibrationDecision | None: ...

    @property
    def baseline_allocation(self) -> AllocationResult | None: ...


@dataclass(frozen=True, slots=True)
class CalibrationCommand:
    """One revisioned operator calibration request at the runtime boundary."""

    action: str
    command_revision: int
    ambient_c: float
    ambient_source: str
    empty_grill_confirmed: bool
    pellets_confirmed: bool
    seed: int = 0

    def __post_init__(self) -> None:
        if self.action not in {"start", "pause", "resume", "stop", "reset-progress"}:
            raise ValueError("invalid calibration action")
        if (
            isinstance(self.command_revision, bool)
            or not isinstance(self.command_revision, int)
            or self.command_revision < 1
        ):
            raise ValueError("calibration command revision must be positive")
        if (
            isinstance(self.ambient_c, bool)
            or not isinstance(self.ambient_c, int | float)
            or not math.isfinite(self.ambient_c)
        ):
            raise ValueError("calibration temperatures must be finite")
        if self.ambient_source not in {"measured", "manual", "weather", "configured"}:
            raise ValueError("invalid calibration ambient source")
        if self.empty_grill_confirmed is not True or self.pellets_confirmed is not True:
            raise ValueError("calibration confirmations are required")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("calibration seed must be an integer")


@dataclass(frozen=True, slots=True)
class _Cancellation:
    reason: str


@dataclass(frozen=True, slots=True)
class _Feedback:
    baseline_q: float
    realized_q: float
    continuous: bool
    actuation_known: bool
    disposition: FrameFeedbackDisposition
    result_revision: int
    command_revision: int
    command_action: str
    command_generation: int


class MpcCalibrationRuntime:
    """Sole owner of calibration commands, state, provenance, and feedback."""

    def __init__(
        self,
        *,
        horizon_steps: int,
        u_max: float,
        clock: CalibrationClock = time.monotonic,
    ) -> None:
        if isinstance(horizon_steps, bool) or not isinstance(horizon_steps, int) or horizon_steps < 1:
            raise ValueError("horizon_steps must be a positive integer")
        if isinstance(u_max, bool) or not isinstance(u_max, int | float) or not math.isfinite(u_max) or u_max <= 0.0:
            raise ValueError("u_max must be finite and positive")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._horizon_steps = horizon_steps
        self._u_max = float(u_max)
        self._clock = clock
        self._coordinator = CalibrationCoordinator(predict_max_c=self._predict_max_c)
        self._operations: deque[CalibrationCommand | _Cancellation] = deque()
        self._feedback: deque[_Feedback] = deque()
        self._frame_results: dict[int, tuple[float, CalibrationDecision]] = {}
        self._last_revision = 0
        self._ambient_c = 0.0
        self._safety_ceiling_c = 0.0
        self._target_c = 0.0
        self._realized_q = 0.0
        self._generation = 0
        self._last_feedback_timestamp: float | None = None
        self._forecast: TemperatureForecast = _forecast_unavailable
        self._decision = CalibrationDecision(False, 0.0, None, CalibrationProgress())

    @property
    def decision(self) -> CalibrationDecision:
        return self._decision

    def set_target_c(self, target_c: float) -> None:
        value = float(target_c)
        if not math.isfinite(value):
            raise ValueError("calibration target must be finite Celsius")
        self._target_c = value

    def set_safety_ceiling_c(self, ceiling_c: float) -> None:
        value = float(ceiling_c)
        if not math.isfinite(value):
            raise ValueError("safety ceiling must be finite Celsius")
        self._safety_ceiling_c = value

    def request(self, command: CalibrationCommand) -> None:
        """Queue each strictly newer operator command for FIFO consumption."""
        if not isinstance(command, CalibrationCommand):
            raise TypeError("command must be CalibrationCommand")
        if command.command_revision < self._last_revision:
            raise ValueError("calibration command revision must be monotonic")
        if command.command_revision == self._last_revision:
            return
        self._operations.append(command)
        self._last_revision = command.command_revision

    def cancel(self, reason: str) -> None:
        """Queue a safety abort without consuming an operator revision."""
        if not isinstance(reason, str) or not reason:
            raise ValueError("calibration cancellation reason must be a non-empty string")
        self._operations.append(_Cancellation(reason))

    def advance(
        self,
        baseline_q: float,
        temperature_c: float,
        forecast: TemperatureForecast,
    ) -> CalibrationDecision:
        """Consume feedback then commands and return the immutable latest decision."""
        if not callable(forecast):
            raise TypeError("forecast must be callable")
        self._forecast = forecast
        try:
            decision: CalibrationDecision | None = None
            while self._feedback:
                feedback = self._feedback.popleft()
                provenance = self._decision
                if not provenance.active or (
                    provenance.command_revision,
                    provenance.command_action,
                    provenance.command_generation,
                ) != (
                    feedback.command_revision,
                    feedback.command_action,
                    feedback.command_generation,
                ):
                    continue
                if feedback.disposition is FrameFeedbackDisposition.DISCARDED:
                    decision = self._coordinator.cancel_probe("discarded_frame")
                else:
                    decision = replace(
                        self._coordinator.advance(
                            self._runtime_context(
                                feedback.baseline_q,
                                temperature_c,
                                realized_q=feedback.realized_q,
                                continuous=feedback.continuous,
                                actuation_known=feedback.actuation_known,
                            )
                        ),
                        command_revision=provenance.command_revision,
                        command_action=provenance.command_action,
                        command_generation=provenance.command_generation,
                    )
                self._decision = decision

            while self._operations:
                operation = self._operations.popleft()
                if isinstance(operation, _Cancellation):
                    decision = self._coordinator.cancel_probe(operation.reason)
                else:
                    command = operation
                    self._ambient_c = float(command.ambient_c)
                    runtime = self._runtime_context(baseline_q, temperature_c)
                    if command.action == "start":
                        self._generation += 1
                        command_generation = self._generation
                        decision = self._coordinator.start(
                            _CoordinatorCalibrationCommand(command.command_revision, command.seed),
                            runtime,
                        )
                    else:
                        command_generation = self._decision.command_generation
                        if command.action == "pause":
                            decision = self._coordinator.pause()
                        elif command.action == "resume":
                            decision = self._coordinator.resume(runtime)
                        elif command.action == "stop":
                            decision = self._coordinator.stop(runtime)
                        else:
                            decision = self._coordinator.reset_progress(runtime)
                    decision = replace(
                        decision,
                        command_revision=command.command_revision,
                        command_action=command.action,
                        command_generation=command_generation,
                    )
                self._decision = decision

            if decision is None:
                decision = replace(self._decision, events=())
            self._decision = decision
            return decision
        finally:
            self._forecast = _forecast_unavailable

    def register_result(self, result: CompletedCalibrationResult) -> None:
        """Associate a completed runner result with the frame that may latch it."""
        calibration = result.calibration
        baseline = result.baseline_allocation
        if calibration is None or baseline is None or result.revision <= 0:
            return
        self._frame_results[result.revision] = (
            baseline.normalized_combustion_load,
            calibration,
        )

    def register_output(self, applied: AppliedOutput) -> None:
        """Record physical output and terminalize only explicit frame feedback."""
        if not isinstance(applied, AppliedOutput):
            raise TypeError("applied must be AppliedOutput")
        self._realized_q = normalized_load_from_auger_duty(applied.ratio, u_max=self._u_max)
        if applied.feedback_disposition is FrameFeedbackDisposition.PROGRESS:
            return
        revision = applied.producing_result_revision
        produced = self._frame_results.pop(revision, None) if revision > 0 else None
        if produced is None:
            return
        for stale_revision in tuple(self._frame_results):
            if stale_revision < revision:
                del self._frame_results[stale_revision]
        baseline_q, decision = produced
        if not decision.active or (
            decision.command_revision,
            decision.command_action,
            decision.command_generation,
        ) != (
            applied.producing_calibration_revision,
            applied.producing_calibration_action,
            applied.producing_calibration_generation,
        ):
            return
        previous = self._last_feedback_timestamp
        continuous = previous is None or applied.timestamp > previous
        self._last_feedback_timestamp = applied.timestamp
        disposition = applied.feedback_disposition
        if disposition is FrameFeedbackDisposition.COMPLETE and not applied.sample_complete:
            disposition = FrameFeedbackDisposition.DISCARDED
        self._feedback.append(
            _Feedback(
                baseline_q,
                self._realized_q,
                continuous,
                applied.controller_commanded,
                disposition,
                revision,
                decision.command_revision,
                decision.command_action,
                decision.command_generation,
            )
        )

    def status(self) -> Mapping[str, object]:
        """Return an immutable diagnostic view of runtime-owned state."""
        return MappingProxyType(
            {
                "decision": self._decision,
                "last_revision": self._last_revision,
                "generation": self._generation,
                "safety_ceiling_c": self._safety_ceiling_c,
                "target_c": self._target_c,
                "pending_operations": len(self._operations),
                "pending_feedback": len(self._feedback),
                "registered_frames": len(self._frame_results),
            }
        )

    def _runtime_context(
        self,
        baseline_q: float,
        temperature_c: float,
        *,
        realized_q: float | None = None,
        continuous: bool = True,
        actuation_known: bool = True,
    ) -> CalibrationRuntimeContext:
        return CalibrationRuntimeContext(
            now_s=self._clock(),
            temp_c=temperature_c,
            target_c=self._target_c,
            baseline_q=baseline_q,
            realized_q=self._realized_q if realized_q is None else realized_q,
            safety_ceiling_c=self._safety_ceiling_c,
            allocator_headroom=1.0,
            error_rate_headroom=1.0,
            capability_headroom=1.0,
            saturation_headroom=1.0,
            rank_progress=1.0,
            coverage_progress=1.0,
            continuous=continuous,
            actuation_known=actuation_known,
        )

    def _predict_max_c(
        self,
        baseline_q: float,
        probe_q: float,
        _runtime: CalibrationRuntimeContext,
    ) -> float:
        forecast = self._forecast
        requested_q = float(np.clip(baseline_q + probe_q, 0.0, 1.0))
        temperatures = np.asarray(
            forecast(
                np.full(self._horizon_steps, requested_q, dtype=np.float64),
                np.full(self._horizon_steps, self._ambient_c, dtype=np.float64),
            ),
            dtype=np.float64,
        )
        if temperatures.shape != (self._horizon_steps,) or not np.isfinite(temperatures).all():
            raise FloatingPointError("grey-box calibration forecast is non-finite")
        return float(np.max(temperatures))
