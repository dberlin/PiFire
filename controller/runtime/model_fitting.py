"""Isolated grey-box fitting and passive candidate preparation.

This module deliberately imports only the standard library at module load time.
The spawned worker fixes native math-library thread limits before it imports the
existing NumPy/SciPy fitting kernel.
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import math
import multiprocessing
import os
import queue
import threading
import time
from numbers import Integral, Real
from typing import TYPE_CHECKING, Any, Callable, Sequence

if TYPE_CHECKING:
    from controller.model_learning.contracts import FitRequest


FITTED_PARAMETERS = ("C_c", "K_Q", "theta")
FIT_LOG_BOUNDS = {
    "C_c": (math.log(1.0), math.log(1e6)),
    "K_Q": (math.log(1e-3), math.log(1e4)),
    "theta": (math.log(1e-9), math.log(1200.0)),
}
MAX_FIT_OBSERVATIONS = 8640
_THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)
_SPAWN_ENVIRONMENT_LOCK = threading.Lock()
_UNSET = object()


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be finite")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return int(value)


def _positive_int(value: object, name: str) -> int:
    normalized = _nonnegative_int(value, name)
    if normalized == 0:
        raise ValueError(f"{name} must be positive")
    return normalized


class FitSubmission(StrEnum):
    ACCEPTED = "accepted"
    BUSY = "busy"


class FitErrorCode(StrEnum):
    FIT_EXCEPTION = "fit-exception"
    INVALID_RESULT = "invalid-result"
    PROCESS_EXIT = "process-exit"


@dataclass(frozen=True, slots=True)
class GreyFitJob:
    """One owned, immutable fitting snapshot."""

    request: Any
    observations: tuple[Any, ...]
    config: Any

    def __post_init__(self) -> None:
        from controller.acados.contracts import GreyBoxMPCConfig
        from controller.model_learning.contracts import FitRequest, FrameObservation

        if not isinstance(self.request, FitRequest):
            raise ValueError("request must be a FitRequest")
        observations = tuple(self.observations)
        if not observations:
            raise ValueError("observations must not be empty")
        if len(observations) > MAX_FIT_OBSERVATIONS:
            raise ValueError(f"observations must be bounded to {MAX_FIT_OBSERVATIONS}")
        if not all(isinstance(frame, FrameObservation) for frame in observations):
            raise ValueError("observations must contain FrameObservation values")
        if not isinstance(self.config, GreyBoxMPCConfig):
            raise ValueError("config must be a GreyBoxMPCConfig")
        object.__setattr__(self, "observations", observations)


@dataclass(frozen=True, slots=True)
class GreyFitSuccess:
    request: Any
    config: Any
    rmse_c: float
    max_error_c: float
    identifiability: float
    sample_count: int
    temperature_band_c: tuple[float, float]
    nfev: int

    def __post_init__(self) -> None:
        from controller.acados.contracts import GreyBoxMPCConfig
        from controller.model_learning.contracts import FitRequest

        if not isinstance(self.request, FitRequest):
            raise ValueError("request must be a FitRequest")
        if not isinstance(self.config, GreyBoxMPCConfig):
            raise ValueError("config must be a GreyBoxMPCConfig")
        for name in ("rmse_c", "max_error_c", "identifiability"):
            normalized = _finite(getattr(self, name), name)
            if normalized < 0.0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, normalized)
        object.__setattr__(self, "sample_count", _positive_int(self.sample_count, "sample_count"))
        band = tuple(self.temperature_band_c)
        if len(band) != 2:
            raise ValueError("temperature_band_c must contain two values")
        low, high = (_finite(value, "temperature_band_c") for value in band)
        if high < low:
            raise ValueError("temperature_band_c must be increasing")
        object.__setattr__(self, "temperature_band_c", (low, high))
        object.__setattr__(self, "nfev", _nonnegative_int(self.nfev, "nfev"))


@dataclass(frozen=True, slots=True)
class GreyFitError:
    request: Any
    code: FitErrorCode
    error_type: str
    detail: str

    def __post_init__(self) -> None:
        from controller.model_learning.contracts import FitRequest

        if not isinstance(self.request, FitRequest):
            raise ValueError("request must be a FitRequest")
        if not isinstance(self.code, FitErrorCode):
            raise ValueError("code must be a FitErrorCode")
        for name in ("error_type", "detail"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-blank")


@dataclass(frozen=True, slots=True)
class GreyFitMessage:
    request: Any
    outcome: GreyFitSuccess | GreyFitError
    worker_start_method: str
    worker_thread_environment: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if self.request != self.outcome.request:
            raise ValueError("message and outcome requests must match")
        if self.worker_start_method != "spawn":
            raise ValueError("grey fitting must use spawn")
        environment = tuple(self.worker_thread_environment)
        if dict(environment) != {name: "1" for name in _THREAD_VARIABLES}:
            raise ValueError("worker thread environment is incomplete")
        object.__setattr__(self, "worker_thread_environment", environment)


def _fit_grey_job(job: GreyFitJob) -> GreyFitSuccess:
    """Run the canonical grey simulator fitter with Task 7's strict bounds."""
    from controller.acados.contracts import GreyBoxMPCConfig
    from controller.update_mpc import fit_params, fit_quality, identifiability

    frames = job.observations
    origin_s = frames[0].frame_end_s
    times = tuple(frame.frame_end_s - origin_s for frame in frames)
    temperatures = tuple(frame.temp_c for frame in frames)
    # Temperatures are sampled at frame ends.  The interval from end[i] to
    # end[i + 1] was driven by frame[i + 1], while the simulator reads Q[i].
    # The final load is unused because there is no following integration span.
    realized_loads = tuple(frame.realized_q for frame in frames[1:]) + (frames[-1].realized_q,)
    config = job.config
    initial = {
        "C_c": config.C_c,
        "h_amb": config.h_amb,
        "K_Q": config.K_Q,
        "theta": config.theta,
    }
    fitted = fit_params(
        times,
        temperatures,
        realized_loads,
        T_amb=config.T_amb,
        init=initial,
        sigma=config.sigma,
        n_delay=config.delay_states,
        log_bounds=FIT_LOG_BOUNDS,
    )
    if not fitted["converged"]:
        raise FloatingPointError("bounded grey fit did not converge")
    rmse_c, max_error_c = fit_quality(times, temperatures, realized_loads, fitted, T_amb=config.T_amb)
    score = identifiability(
        times,
        realized_loads,
        fitted,
        T_amb=config.T_amb,
        T0=temperatures[0],
    )
    if score is None:
        raise FloatingPointError("bounded grey fit is not identifiable")
    values = {name: getattr(config, name) for name in config.__dataclass_fields__}
    values.update({name: fitted[name] for name in FITTED_PARAMETERS})
    candidate = GreyBoxMPCConfig(**values)
    return GreyFitSuccess(
        request=job.request,
        config=candidate,
        rmse_c=rmse_c,
        max_error_c=max_error_c,
        identifiability=score,
        sample_count=len(frames),
        temperature_band_c=(min(temperatures), max(temperatures)),
        nfev=int(fitted["nfev"]),
    )


def _worker_main(requests: Any, results: Any, kernel: Callable[[GreyFitJob], GreyFitSuccess]) -> None:
    for name in _THREAD_VARIABLES:
        os.environ[name] = "1"
    environment = tuple((name, os.environ[name]) for name in _THREAD_VARIABLES)
    while True:
        job = requests.get()
        if job is None:
            return
        try:
            outcome = kernel(job)
            if not isinstance(outcome, GreyFitSuccess) or outcome.request != job.request:
                raise TypeError("fit kernel must return GreyFitSuccess for the exact request")
        except Exception as error:
            code = FitErrorCode.INVALID_RESULT if isinstance(error, TypeError) else FitErrorCode.FIT_EXCEPTION
            outcome = GreyFitError(
                request=job.request,
                code=code,
                error_type=type(error).__name__,
                detail=str(error) or repr(error),
            )
        results.put(
            GreyFitMessage(
                request=job.request,
                outcome=outcome,
                worker_start_method="spawn",
                worker_thread_environment=environment,
            )
        )


class GreyFitWorker:
    """One persistent spawned process with one drain-before-reuse request slot."""

    def __init__(self, kernel: Callable[[GreyFitJob], GreyFitSuccess] | None = None) -> None:
        self._kernel = _fit_grey_job if kernel is None else kernel
        if not callable(self._kernel):
            raise ValueError("kernel must be callable")
        self._context = multiprocessing.get_context("spawn")
        self._requests: Any = None
        self._results: Any = None
        self._process: Any = None
        self._pending: GreyFitJob | None = None
        self._closed = False

    @property
    def start_method(self) -> str:
        return self._context.get_start_method()

    @property
    def process_count(self) -> int:
        return 1 if self._process is not None else 0

    @property
    def alive(self) -> bool:
        return bool(self._process is not None and self._process.is_alive())

    @property
    def busy(self) -> bool:
        return self._pending is not None

    def start(self) -> GreyFitWorker:
        if self._closed:
            raise RuntimeError("GreyFitWorker is closed")
        if self._process is not None:
            return self
        self._requests = self._context.Queue(maxsize=1)
        self._results = self._context.Queue(maxsize=1)
        self._process = self._context.Process(
            target=_worker_main,
            args=(self._requests, self._results, self._kernel),
            name="pifire-grey-fit",
        )
        # A spawned interpreter can import NumPy while reconstructing a custom
        # top-level kernel.  Set limits for the inherited child environment
        # across start(), then restore the parent exactly.
        with _SPAWN_ENVIRONMENT_LOCK:
            previous = {name: os.environ.get(name) for name in _THREAD_VARIABLES}
            try:
                for name in _THREAD_VARIABLES:
                    os.environ[name] = "1"
                self._process.start()
            finally:
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
        return self

    def submit(self, job: GreyFitJob) -> FitSubmission:
        if not isinstance(job, GreyFitJob):
            raise ValueError("job must be a GreyFitJob")
        self.start()
        if self._pending is not None:
            return FitSubmission.BUSY
        if not self.alive:
            raise RuntimeError("grey fitting process is not alive")
        self._requests.put_nowait(job)
        self._pending = job
        return FitSubmission.ACCEPTED

    def receive(self, timeout_s: float | None = None) -> GreyFitMessage:
        if self._pending is None:
            raise RuntimeError("no grey fit result is outstanding")
        if timeout_s is not None:
            timeout_s = _finite(timeout_s, "timeout_s")
            if timeout_s < 0.0:
                raise ValueError("timeout_s must be non-negative")
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            wait_s = 0.1 if deadline is None else max(0.0, min(0.1, deadline - time.monotonic()))
            try:
                message = self._results.get(timeout=wait_s)
                break
            except queue.Empty as error:
                if not self.alive:
                    job = self._pending
                    self._pending = None
                    return GreyFitMessage(
                        request=job.request,
                        outcome=GreyFitError(
                            request=job.request,
                            code=FitErrorCode.PROCESS_EXIT,
                            error_type="WorkerProcessExit",
                            detail=f"grey fitting process exited with code {self._process.exitcode}",
                        ),
                        worker_start_method="spawn",
                        worker_thread_environment=tuple((name, "1") for name in _THREAD_VARIABLES),
                    )
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("grey fit result was not ready before timeout") from error
        if not isinstance(message, GreyFitMessage) or message.request != self._pending.request:
            raise RuntimeError("grey fitting process returned an invalid message")
        self._pending = None
        return message

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None:
            if process.is_alive():
                self._requests.put(None)
                process.join(timeout=5.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5.0)
            process.close()
        for channel in (self._requests, self._results):
            if channel is not None:
                channel.close()
                channel.cancel_join_thread()
        self._process = None
        self._requests = None
        self._results = None
        self._pending = None

    def __enter__(self) -> GreyFitWorker:
        return self.start()

    def __exit__(self, exception_type: Any, exception: Any, traceback: Any) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class HistoryDecision:
    accepted: bool
    reasons: tuple[str, ...]


class TeardownRefitOutcome(StrEnum):
    DISABLED = "disabled"
    INSUFFICIENT = "insufficient"
    REJECTED = "rejected"
    FAILED = "failed"
    READY_FOR_REVIEW = "ready-for-review"
    ACCEPTED_NEXT_COOK = "accepted-next-cook"
    CHECKPOINT_FAILURE = "checkpoint-failure"


@dataclass(frozen=True, slots=True)
class TeardownRefitResult:
    outcome: TeardownRefitOutcome
    reason: str
    origin: Any
    policy: Any
    candidate_digest: str | None = None

    def __post_init__(self) -> None:
        from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin

        if not isinstance(self.outcome, TeardownRefitOutcome):
            object.__setattr__(self, "outcome", TeardownRefitOutcome(self.outcome))
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be non-blank")
        if not isinstance(self.origin, CandidateOrigin):
            object.__setattr__(self, "origin", CandidateOrigin(self.origin))
        if not isinstance(self.policy, ActivationPolicy):
            object.__setattr__(self, "policy", ActivationPolicy(self.policy))
        if self.candidate_digest is not None:
            value = self.candidate_digest
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError("candidate_digest must be a lowercase SHA-256 digest")

    @property
    def accepted(self) -> bool:
        return self.outcome in {
            TeardownRefitOutcome.READY_FOR_REVIEW,
            TeardownRefitOutcome.ACCEPTED_NEXT_COOK,
        }

    @classmethod
    def insufficient(cls, reason: str) -> TeardownRefitResult:
        from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin

        return cls(
            TeardownRefitOutcome.INSUFFICIENT,
            reason,
            CandidateOrigin.COOK_REFIT,
            ActivationPolicy.COOK_REFIT,
        )

    @classmethod
    def rejected(cls, reason: str, *, origin: Any) -> TeardownRefitResult:
        from controller.model_learning.contracts import (
            ActivationPolicy,
            CandidateOrigin,
        )

        resolved = origin if isinstance(origin, CandidateOrigin) else CandidateOrigin(origin)
        policy = (
            ActivationPolicy.OPERATOR_REVIEWED
            if resolved is CandidateOrigin.OPERATOR_CALIBRATION
            else ActivationPolicy.COOK_REFIT
        )
        return cls(TeardownRefitOutcome.REJECTED, reason, resolved, policy)

    @classmethod
    def failed(cls, reason: str, *, origin: Any) -> TeardownRefitResult:
        from controller.model_learning.contracts import (
            ActivationPolicy,
            CandidateOrigin,
        )

        resolved = origin if isinstance(origin, CandidateOrigin) else CandidateOrigin(origin)
        policy = (
            ActivationPolicy.OPERATOR_REVIEWED
            if resolved is CandidateOrigin.OPERATOR_CALIBRATION
            else ActivationPolicy.COOK_REFIT
        )
        return cls(TeardownRefitOutcome.FAILED, reason, resolved, policy)

    @classmethod
    def ready_for_review(cls, reason: str, *, candidate_digest: str) -> TeardownRefitResult:
        from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin

        return cls(
            TeardownRefitOutcome.READY_FOR_REVIEW,
            reason,
            CandidateOrigin.OPERATOR_CALIBRATION,
            ActivationPolicy.OPERATOR_REVIEWED,
            candidate_digest,
        )

    @classmethod
    def accepted_next_cook(cls, reason: str, *, candidate_digest: str) -> TeardownRefitResult:
        from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin

        return cls(
            TeardownRefitOutcome.ACCEPTED_NEXT_COOK,
            reason,
            CandidateOrigin.COOK_REFIT,
            ActivationPolicy.COOK_REFIT,
            candidate_digest,
        )


class TeardownGreyHistory:
    """Complete bounded contiguous fit evidence, including applied probes."""

    def __init__(self, *, role_generation: int, max_observations: int = MAX_FIT_OBSERVATIONS) -> None:
        self.role_generation = _nonnegative_int(role_generation, "role_generation")
        self.max_observations = _positive_int(max_observations, "max_observations")
        if self.max_observations > MAX_FIT_OBSERVATIONS:
            raise ValueError(f"max_observations must be bounded to {MAX_FIT_OBSERVATIONS}")
        self._observations: deque[Any] = deque(maxlen=self.max_observations)

    @property
    def observations(self) -> tuple[Any, ...]:
        return tuple(self._observations)

    @property
    def origin(self) -> Any:
        from controller.model_learning.contracts import CandidateOrigin

        return (
            CandidateOrigin.OPERATOR_CALIBRATION
            if any(
                frame.calibration_fit
                and frame.probe_valid
                and not math.isclose(frame.probe_q, 0.0, rel_tol=0.0, abs_tol=1e-12)
                for frame in self._observations
            )
            else CandidateOrigin.COOK_REFIT
        )

    def observe(self, frame: Any) -> HistoryDecision:
        from controller.model_learning.contracts import FrameObservation

        if not isinstance(frame, FrameObservation):
            raise ValueError("frame must be a FrameObservation")
        reason: str | None = None
        if frame.manual_override or frame.output_source == "manual-override":
            reason = "manual"
        elif frame.lid_open:
            reason = "lid-open"
        elif frame.safety_inhibited:
            reason = "safety"
        elif frame.stale:
            reason = "stale"
        elif frame.skipped or frame.reset:
            reason = "skipped-or-reset"
        elif not frame.continuous:
            reason = "discontinuity"
        elif frame.role_generation != self.role_generation:
            reason = "stale-generation"
        elif frame.allocation_join_reason is not None:
            reason = "unknown-actuation"
        elif frame.output_source != "controller":
            reason = "non-controller-output"
        elif frame.calibration_fit and not frame.probe_valid:
            reason = "invalid-probe"
        if reason is not None:
            self._observations.clear()
            return HistoryDecision(False, (reason,))
        if self._observations:
            previous = self._observations[-1]
            adjacent = (
                frame.observation_sequence == previous.observation_sequence + 1
                and math.isclose(frame.frame_start_s, previous.frame_end_s, rel_tol=0.0, abs_tol=1e-9)
            )
            if not adjacent:
                self._observations.clear()
        self._observations.append(frame)
        return HistoryDecision(True, ())


class PassiveGreyHistory:
    """Bounded passive Hold evidence with explicit single-cause rejection."""

    def __init__(self, *, role_generation: int, max_observations: int = MAX_FIT_OBSERVATIONS) -> None:
        self.role_generation = _nonnegative_int(role_generation, "role_generation")
        self.max_observations = _positive_int(max_observations, "max_observations")
        if self.max_observations > MAX_FIT_OBSERVATIONS:
            raise ValueError(f"max_observations must be bounded to {MAX_FIT_OBSERVATIONS}")
        self._observations: deque[Any] = deque(maxlen=self.max_observations)

    @property
    def observations(self) -> tuple[Any, ...]:
        return tuple(self._observations)

    def observe(self, frame: Any) -> HistoryDecision:
        from controller.model_learning.contracts import FrameObservation

        if not isinstance(frame, FrameObservation):
            raise ValueError("frame must be a FrameObservation")
        reason: str | None = None
        if frame.manual_override or frame.output_source == "manual-override":
            reason = "manual"
        elif frame.lid_open:
            reason = "lid-open"
        elif frame.safety_inhibited:
            reason = "safety"
        elif frame.stale:
            reason = "stale"
        elif frame.skipped or frame.reset:
            reason = "skipped-or-reset"
        elif not frame.continuous:
            reason = "discontinuity"
        elif frame.role_generation != self.role_generation:
            reason = "stale-generation"
        elif frame.allocation_join_reason is not None:
            reason = "unknown-actuation"
        elif frame.calibration_fit or frame.calibration_stage is not None or frame.probe_q != 0.0:
            reason = "calibration-frame"
        elif frame.output_source != "controller":
            reason = "non-controller-output"
        if reason is not None:
            self._observations.clear()
            return HistoryDecision(False, (reason,))
        if self._observations:
            previous = self._observations[-1]
            adjacent = (
                frame.observation_sequence == previous.observation_sequence + 1
                and math.isclose(frame.frame_start_s, previous.frame_end_s, rel_tol=0.0, abs_tol=1e-9)
            )
            if not adjacent:
                self._observations.clear()
        self._observations.append(frame)
        return HistoryDecision(True, ())


@dataclass(frozen=True, slots=True)
class TriggerConfig:
    min_samples: int = 120
    min_input_variance: float = 0.02
    min_input_levels: int = 3
    min_temperature_span_c: float = 8.0
    min_identifiability: float = 0.5

    def __post_init__(self) -> None:
        object.__setattr__(self, "min_samples", _positive_int(self.min_samples, "min_samples"))
        object.__setattr__(self, "min_input_levels", _positive_int(self.min_input_levels, "min_input_levels"))
        for name in ("min_input_variance", "min_temperature_span_c", "min_identifiability"):
            value = _finite(getattr(self, name), name)
            if value < 0.0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    ready: bool
    blockers: tuple[str, ...]


def fit_trigger(
    observations: Sequence[Any], *, identifiability: float, config: TriggerConfig | None = None
) -> TriggerDecision:
    from controller.model_learning.contracts import FrameObservation

    resolved = TriggerConfig() if config is None else config
    if not isinstance(resolved, TriggerConfig):
        raise ValueError("config must be a TriggerConfig")
    frames = tuple(observations)
    if not all(isinstance(frame, FrameObservation) for frame in frames):
        raise ValueError("observations must contain FrameObservation values")
    if len(frames) < resolved.min_samples:
        return TriggerDecision(False, ("minimum-samples",))
    blockers: list[str] = []
    loads = tuple(frame.realized_q for frame in frames)
    mean = sum(loads) / len(loads)
    variance = sum((value - mean) ** 2 for value in loads) / len(loads)
    levels = len({round(value, 9) for value in loads})
    if variance < resolved.min_input_variance or levels < resolved.min_input_levels:
        blockers.append("insufficient-excitation")
    temperatures = tuple(frame.temp_c for frame in frames)
    if max(temperatures) - min(temperatures) < resolved.min_temperature_span_c:
        blockers.append("insufficient-coverage")
    if any(not frame.continuous for frame in frames) or any(
        later.observation_sequence != earlier.observation_sequence + 1
        or not math.isclose(later.frame_start_s, earlier.frame_end_s, rel_tol=0.0, abs_tol=1e-9)
        for earlier, later in zip(frames, frames[1:])
    ):
        blockers.append("discontinuity")
    score = _finite(identifiability, "identifiability")
    if score < resolved.min_identifiability:
        blockers.append("identifiability")
    return TriggerDecision(not blockers, tuple(blockers))


def stale_result_reasons(
    result: Any,
    *,
    request: Any,
    current_window: Any,
    current_candidate_generation: int,
    current_origin: Any,
) -> tuple[str, ...]:
    from controller.model_learning.contracts import FitRequest, FitResult, FitWindowIdentity

    if not isinstance(result, FitResult):
        raise ValueError("result must be a FitResult")
    if not isinstance(request, FitRequest):
        raise ValueError("request must be a FitRequest")
    if not isinstance(current_window, FitWindowIdentity):
        raise ValueError("current_window must be a FitWindowIdentity")
    current_generation = _nonnegative_int(current_candidate_generation, "current_candidate_generation")
    submitted = request.window
    returned = result.window
    reasons: list[str] = []
    if result.origin != request.origin or current_origin != request.origin:
        reasons.append("origin-changed")
    if result.request_id != request.request_id:
        reasons.append("request-changed")
    if returned.session_id != submitted.session_id or current_window.session_id != submitted.session_id:
        reasons.append("session-changed")
    if returned.cook_id != submitted.cook_id or current_window.cook_id != submitted.cook_id:
        reasons.append("cook-changed")
    if (
        returned.first_observation_sequence != submitted.first_observation_sequence
        or returned.last_observation_sequence != submitted.last_observation_sequence
        or current_window.first_observation_sequence != submitted.first_observation_sequence
        or current_window.last_observation_sequence != submitted.last_observation_sequence
    ):
        reasons.append("window-changed")
    if (
        returned.configuration_digest != submitted.configuration_digest
        or current_window.configuration_digest != submitted.configuration_digest
    ):
        reasons.append("configuration-changed")
    if returned.incumbent_digest != submitted.incumbent_digest or current_window.incumbent_digest != submitted.incumbent_digest:
        reasons.append("incumbent-changed")
    if returned.role_generation != submitted.role_generation or current_window.role_generation != submitted.role_generation:
        reasons.append("role-generation-changed")
    if (
        result.candidate_generation != request.candidate_generation
        or current_generation != request.candidate_generation
    ):
        reasons.append("candidate-generation-changed")
    return tuple(reasons)


@dataclass(frozen=True, slots=True)
class TargetTimingEvidence:
    target: str
    samples: int
    p99_ms: float
    limit_ms: float

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("target must be non-blank")
        object.__setattr__(self, "samples", _positive_int(self.samples, "samples"))
        for name in ("p99_ms", "limit_ms"):
            value = _finite(getattr(self, name), name)
            if value < 0.0 or (name == "limit_ms" and value == 0.0):
                raise ValueError(f"{name} must be {'positive' if name == 'limit_ms' else 'non-negative'}")
            object.__setattr__(self, name, value)

    @property
    def accepted(self) -> bool:
        return self.p99_ms <= self.limit_ms


@dataclass(frozen=True, slots=True)
class CandidatePair:
    estimator: Any
    controller: Any

    def __post_init__(self) -> None:
        if self.estimator is None or self.controller is None:
            raise ValueError("candidate pair requires estimator and controller")


def grey_config_digest(config: Any) -> str:
    from controller.acados.contracts import GreyBoxMPCConfig

    if not isinstance(config, GreyBoxMPCConfig):
        raise ValueError("config must be a GreyBoxMPCConfig")
    document = {name: getattr(config, name) for name in config.__dataclass_fields__}
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidatePreparation:
    candidate: GreyFitSuccess
    incumbent_pair: Any
    accepted: bool
    blockers: tuple[str, ...]
    candidate_pair: CandidatePair | Any | None = None
    dry_solve_finite: bool = False
    timing: TargetTimingEvidence | None = None
    detail: str | None = None
    candidate_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, GreyFitSuccess):
            raise ValueError("candidate must be a GreyFitSuccess")
        blockers = tuple(self.blockers)
        if not all(isinstance(value, str) and value for value in blockers):
            raise ValueError("blockers must be non-blank strings")
        object.__setattr__(self, "blockers", blockers)
        object.__setattr__(self, "candidate_digest", grey_config_digest(self.candidate.config))
        if self.accepted:
            if blockers or self.candidate_pair is None or not self.dry_solve_finite:
                raise ValueError("accepted preparation must own a finite candidate pair without blockers")
            if not isinstance(self.timing, TargetTimingEvidence) or not self.timing.accepted:
                raise ValueError("accepted preparation requires passing target timing")
        elif self.candidate_pair is not None:
            raise ValueError("rejected preparation cannot retain a candidate pair")

    @classmethod
    def accepted_for_test(
        cls, *, candidate: GreyFitSuccess, candidate_pair: Any, incumbent_pair: Any, timing: TargetTimingEvidence
    ) -> CandidatePreparation:
        return cls(
            candidate=candidate,
            incumbent_pair=incumbent_pair,
            accepted=True,
            blockers=(),
            candidate_pair=candidate_pair,
            dry_solve_finite=True,
            timing=timing,
        )


def _close_if_owned(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def _rejected_candidate(
    candidate: GreyFitSuccess,
    incumbent_pair: Any,
    blocker: str,
    error: BaseException | str,
    *,
    timing: TargetTimingEvidence | None = None,
) -> CandidatePreparation:
    return CandidatePreparation(
        candidate=candidate,
        incumbent_pair=incumbent_pair,
        accepted=False,
        blockers=(blocker,),
        timing=timing,
        detail=str(error),
    )


def _finite_dry_solve(solve: Any, expected_horizon: int) -> bool:
    try:
        sequence = tuple(solve.sequence_q)
        objective = float(solve.objective)
    except (AttributeError, TypeError, ValueError):
        return False
    return len(sequence) == expected_horizon and math.isfinite(objective) and all(
        math.isfinite(float(value)) for value in sequence
    )


def prepare_candidate_off_path(
    candidate: GreyFitSuccess,
    *,
    incumbent_pair: Any,
    estimator_factory: Callable[[Any], Any],
    controller_factory: Callable[[Any], Any],
    timing_probe: Callable[[Any], TargetTimingEvidence],
) -> CandidatePreparation:
    """Build and exercise a complete challenger without touching the incumbent."""
    if not isinstance(candidate, GreyFitSuccess):
        raise ValueError("candidate must be a GreyFitSuccess")
    try:
        estimator = estimator_factory(candidate.config)
    except Exception as error:
        return _rejected_candidate(candidate, incumbent_pair, "estimator-build", error)
    try:
        controller = controller_factory(candidate.config)
    except Exception as error:
        _close_if_owned(estimator)
        return _rejected_candidate(candidate, incumbent_pair, "native-build", error)
    pair = CandidatePair(estimator=estimator, controller=controller)
    try:
        state = getattr(estimator, "state", getattr(estimator, "x", (0.0,) * candidate.config.state_size))
        state_values = tuple(float(value) for value in state)
        if len(state_values) != candidate.config.state_size or not all(math.isfinite(value) for value in state_values):
            raise ValueError("candidate estimator state must be a finite ten-state vector")
        solve = controller.solve(
            state,
            setpoint_c=candidate.config.T_amb + 50.0,
            q_previous=0.0,
            equilibrium_q=0.4,
        )
        if not _finite_dry_solve(solve, candidate.config.horizon_steps):
            raise ValueError("candidate native dry solve was non-finite")
    except Exception as error:
        _close_if_owned(controller)
        _close_if_owned(estimator)
        return _rejected_candidate(candidate, incumbent_pair, "native-dry-solve", error)
    timing: TargetTimingEvidence | None = None
    try:
        timing_result = timing_probe(controller)
        if not isinstance(timing_result, TargetTimingEvidence):
            raise TypeError("timing probe must return TargetTimingEvidence")
        timing = timing_result
        if not timing.accepted:
            raise RuntimeError(f"target p99 {timing.p99_ms} ms exceeds {timing.limit_ms} ms")
    except Exception as error:
        _close_if_owned(controller)
        _close_if_owned(estimator)
        evidence = timing if isinstance(timing, TargetTimingEvidence) else None
        return _rejected_candidate(candidate, incumbent_pair, "target-timing", error, timing=evidence)
    return CandidatePreparation(
        candidate=candidate,
        incumbent_pair=incumbent_pair,
        accepted=True,
        blockers=(),
        candidate_pair=pair,
        dry_solve_finite=True,
        timing=timing,
    )


@dataclass(frozen=True, slots=True)
class CausalForecastInput:
    frame: Any
    horizon_steps: int
    candidate_generation: int
    incumbent_digest: str
    challenger_digest: str



def paired_forecast_origin(
    frame: Any,
    *,
    horizon_steps: int,
    candidate_generation: int,
    incumbent_digest: str,
    challenger_digest: str,
    incumbent_predict: Callable[[CausalForecastInput], float],
    challenger_predict: Callable[[CausalForecastInput], float],
) -> Any | None:
    """Call both predictors with one shared immutable, pre-observation origin."""
    from controller.model_learning.contracts import FrameObservation
    from controller.model_learning.evaluation import ForecastOrigin

    if not isinstance(frame, FrameObservation):
        raise ValueError("frame must be a FrameObservation")
    if frame.calibration_fit or frame.calibration_stage is not None or frame.probe_q != 0.0:
        return None
    shared = CausalForecastInput(
        frame=frame,
        horizon_steps=horizon_steps,
        candidate_generation=candidate_generation,
        incumbent_digest=incumbent_digest,
        challenger_digest=challenger_digest,
    )
    incumbent_prediction = incumbent_predict(shared)
    challenger_prediction = challenger_predict(shared)
    return ForecastOrigin(
        origin_sequence=frame.observation_sequence,
        origin_time_s=frame.frame_end_s,
        horizon_steps=horizon_steps,
        role_generation=frame.role_generation,
        candidate_generation=candidate_generation,
        incumbent_digest=incumbent_digest,
        challenger_digest=challenger_digest,
        incumbent_prediction_c=incumbent_prediction,
        challenger_prediction_c=challenger_prediction,
        temperature_band=frame.temperature_band or "unknown",
        phase="hold",
        ambient_source=frame.ambient_source,
        calibration_fit=False,
    )


@dataclass(frozen=True, slots=True)
class CandidateHandoff:
    status: Any
    policy: Any | None
    prepared_id: Any | None
    active_pair: Any
    blockers: tuple[str, ...] = ()

class CandidateOwnershipTransferredError(RuntimeError):
    """Preparation failed after the activation runtime accepted pair ownership."""



def handoff_candidate(
    prepared: CandidatePreparation,
    *,
    evaluation: Any,
    confidence_accepted: bool,
    online_enabled: bool,
    prepare: Callable[[CandidatePreparation, Any], Any],
    install: Callable[[Any], Any],
) -> CandidateHandoff:
    """Prepare persistence handoff only; Task 7 never installs or swaps a pair."""
    from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin, LearningStatus

    if not isinstance(prepared, CandidatePreparation):
        raise ValueError("prepared must be a CandidatePreparation")
    if not isinstance(confidence_accepted, bool) or not isinstance(online_enabled, bool):
        raise ValueError("confidence_accepted and online_enabled must be bools")
    if not callable(prepare) or not callable(install):
        raise ValueError("prepare and install must be callable")
    del install  # Ownership transfer is intentionally outside Task 7.
    blockers: list[str] = []
    if not prepared.accepted:
        blockers.extend(prepared.blockers or ("candidate-rejected",))
    if not bool(getattr(evaluation, "accepted", False)):
        blockers.append("evaluation")
    if int(getattr(evaluation, "consecutive_wins", 0)) < 2:
        blockers.append("consecutive-confidence")
    request = prepared.candidate.request
    if (
        getattr(evaluation, "role_generation", None) != request.window.role_generation
        or getattr(evaluation, "candidate_generation", None) != request.candidate_generation
    ):
        blockers.append("stale-generation")
    if getattr(evaluation, "incumbent_digest", None) != request.window.incumbent_digest:
        blockers.append("incumbent-changed")
    if getattr(evaluation, "challenger_digest", None) != prepared.candidate_digest:
        blockers.append("challenger-changed")
    if not confidence_accepted:
        blockers.append("confidence")
    origin = request.origin
    if origin is CandidateOrigin.COOK_REFIT:
        raise ValueError("cook-refit handoff belongs to Task 12")
    policy = (
        ActivationPolicy.OPERATOR_REVIEWED
        if origin is CandidateOrigin.OPERATOR_CALIBRATION
        else ActivationPolicy.PASSIVE_AUTO
    )
    if origin is CandidateOrigin.PASSIVE_ONLINE and not online_enabled:
        blockers.append("online-disabled")
    if blockers:
        return CandidateHandoff(
            status=LearningStatus.EVALUATING,
            policy=None,
            prepared_id=None,
            active_pair=prepared.incumbent_pair,
            blockers=tuple(blockers),
        )
    prepared_id = prepare(prepared, policy)
    status = (
        LearningStatus.READY_FOR_REVIEW
        if policy is ActivationPolicy.OPERATOR_REVIEWED
        else LearningStatus.ACTIVATING
    )
    return CandidateHandoff(
        status=status,
        policy=policy,
        prepared_id=prepared_id,
        active_pair=prepared.incumbent_pair,
    )


@dataclass(frozen=True, slots=True)
class LiveLearningIdentity:
    """Task 8's live identity input to the otherwise off-path Task 7 pipeline."""

    session_id: str
    cook_id: str | None
    configuration_digest: str
    incumbent_digest: str
    role_generation: int
    candidate_generation: int

    def __post_init__(self) -> None:
        from controller.model_learning.contracts import FitWindowIdentity

        # Reuse the neutral validator with an empty-but-valid sequence window.
        FitWindowIdentity(
            session_id=self.session_id,
            cook_id=self.cook_id,
            first_observation_sequence=0,
            last_observation_sequence=0,
            configuration_digest=self.configuration_digest,
            incumbent_digest=self.incumbent_digest,
            role_generation=self.role_generation,
        )
        object.__setattr__(
            self,
            "role_generation",
            _nonnegative_int(self.role_generation, "role_generation"),
        )
        object.__setattr__(
            self,
            "candidate_generation",
            _nonnegative_int(self.candidate_generation, "candidate_generation"),
        )

    def window(self, first_sequence: int, last_sequence: int) -> Any:
        from controller.model_learning.contracts import FitWindowIdentity

        return FitWindowIdentity(
            session_id=self.session_id,
            cook_id=self.cook_id,
            first_observation_sequence=first_sequence,
            last_observation_sequence=last_sequence,
            configuration_digest=self.configuration_digest,
            incumbent_digest=self.incumbent_digest,
            role_generation=self.role_generation,
        )


@dataclass(frozen=True, slots=True)
class GreyLearningObservation:
    history: HistoryDecision
    submission: FitSubmission | None
    request: Any | None
    completed_forecasts: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class GreyLearningDelivery:
    message: GreyFitMessage | None
    stale_reasons: tuple[str, ...]
    preparation: CandidatePreparation | None
    blockers: tuple[str, ...] = ()


class GreyLearningOrchestrator:
    """Cohesive Task 7 pipeline for Task 8 to schedule off the control worker.

    This owner deliberately has no Controller/Hold references and never installs
    a pair.  Task 8 supplies live identities and calls ``poll_fit_off_path`` on
    its lifecycle worker; Task 10 supplies the preparation callback.
    """

    def __init__(
        self,
        *,
        identity: LiveLearningIdentity,
        config: Any,
        incumbent_pair: Any,
        estimator_factory: Callable[[Any], Any],
        controller_factory: Callable[[Any], Any],
        timing_probe: Callable[[Any], TargetTimingEvidence],
        trigger_config: TriggerConfig | None = None,
        evaluation_config: Any | None = None,
        worker: Any | None = None,
        max_observations: int = MAX_FIT_OBSERVATIONS,
    ) -> None:
        from controller.acados.contracts import GreyBoxMPCConfig
        from controller.model_learning.evaluation import EvaluationConfig

        if not isinstance(identity, LiveLearningIdentity):
            raise ValueError("identity must be a LiveLearningIdentity")
        if not isinstance(config, GreyBoxMPCConfig):
            raise ValueError("config must be a GreyBoxMPCConfig")
        self.identity = identity
        self.config = config
        self.incumbent_pair = incumbent_pair
        self.estimator_factory = estimator_factory
        self.controller_factory = controller_factory
        self.timing_probe = timing_probe
        self.trigger_config = TriggerConfig() if trigger_config is None else trigger_config
        self.evaluation_config = EvaluationConfig() if evaluation_config is None else evaluation_config
        if not isinstance(self.trigger_config, TriggerConfig):
            raise ValueError("trigger_config must be a TriggerConfig")
        if not isinstance(self.evaluation_config, EvaluationConfig):
            raise ValueError("evaluation_config must be an EvaluationConfig")
        self.worker = GreyFitWorker() if worker is None else worker
        self.passive_history = PassiveGreyHistory(
            role_generation=identity.role_generation,
            max_observations=max_observations,
        )
        self._operator_history: deque[Any] = deque(maxlen=max_observations)
        self._pending_request: FitRequest | None = None
        self._prepared: CandidatePreparation | None = None
        self._evaluator: Any | None = None
        self._evaluation_cursor = 0
        self._consecutive_wins = 0
        self._last_evaluation: Any | None = None
        self._handoff: CandidateHandoff | None = None
        self._started = False
        self._ownership_transferred = False

    @property
    def pending_request(self) -> FitRequest | None:
        return self._pending_request

    @property
    def prepared(self) -> CandidatePreparation | None:
        return self._prepared

    @property
    def last_evaluation(self) -> Any | None:
        return self._last_evaluation

    @property
    def handoff(self) -> CandidateHandoff | None:
        return self._handoff

    def start(self) -> GreyLearningOrchestrator:
        if not self._started:
            start = getattr(self.worker, "start", None)
            if callable(start):
                start()
            self._started = True
        return self

    def _release_prepared(self) -> None:
        if (
            self._prepared is not None
            and self._prepared.accepted
            and not self._ownership_transferred
            and self._prepared.candidate_pair is not None
        ):
            pair = self._prepared.candidate_pair
            owned = (
                (getattr(pair, "controller"), getattr(pair, "estimator"))
                if hasattr(pair, "controller") and hasattr(pair, "estimator")
                else (pair,)
            )
            closed: set[int] = set()
            for value in owned:
                if id(value) not in closed:
                    _close_if_owned(value)
                    closed.add(id(value))
        self._prepared = None
        self._ownership_transferred = False

    def update_identity(
        self,
        identity: LiveLearningIdentity,
        *,
        config: Any | None = None,
        incumbent_pair: Any = _UNSET,
    ) -> None:
        from controller.acados.contracts import GreyBoxMPCConfig

        if not isinstance(identity, LiveLearningIdentity):
            raise ValueError("identity must be a LiveLearningIdentity")
        configuration_changed = identity.configuration_digest != self.identity.configuration_digest
        incumbent_changed = identity.incumbent_digest != self.identity.incumbent_digest
        if configuration_changed and config is None:
            raise ValueError("configuration digest change requires the corresponding config")
        if incumbent_changed and incumbent_pair is _UNSET:
            raise ValueError("incumbent digest change requires the corresponding incumbent pair")
        replacement_config = self.config if config is None else config
        if not isinstance(replacement_config, GreyBoxMPCConfig):
            raise ValueError("config must be a GreyBoxMPCConfig")
        if (
            identity == self.identity
            and config is None
            and incumbent_pair is _UNSET
        ):
            return
        replacement_pair = self.incumbent_pair if incumbent_pair is _UNSET else incumbent_pair
        self._release_prepared()
        self.identity = identity
        self.config = replacement_config
        self.incumbent_pair = replacement_pair
        self.passive_history = PassiveGreyHistory(
            role_generation=identity.role_generation,
            max_observations=self.passive_history.max_observations,
        )
        self._operator_history.clear()
        self._evaluator = None
        self._evaluation_cursor = 0
        self._consecutive_wins = 0
        self._last_evaluation = None
        self._handoff = None

    @staticmethod
    def _operator_rejection(frame: Any, role_generation: int) -> str | None:
        if frame.manual_override or frame.output_source == "manual-override":
            return "manual"
        if frame.lid_open:
            return "lid-open"
        if frame.safety_inhibited:
            return "safety"
        if frame.stale:
            return "stale"
        if frame.skipped or frame.reset:
            return "skipped-or-reset"
        if not frame.continuous:
            return "discontinuity"
        if frame.role_generation != role_generation:
            return "stale-generation"
        if frame.allocation_join_reason is not None:
            return "unknown-actuation"
        if frame.output_source != "controller":
            return "non-controller-output"
        if not frame.calibration_fit or frame.calibration_stage is None or not frame.probe_valid:
            return "not-completed-operator-stage"
        return None

    @staticmethod
    def _append_contiguous(history: deque[Any], frame: Any) -> None:
        if history:
            previous = history[-1]
            if (
                frame.observation_sequence != previous.observation_sequence + 1
                or not math.isclose(frame.frame_start_s, previous.frame_end_s, rel_tol=0.0, abs_tol=1e-9)
            ):
                history.clear()
        history.append(frame)

    def observe_completed_frame(
        self,
        frame: Any,
        *,
        identifiability: float,
    ) -> GreyLearningObservation:
        """Collect one frame, complete causal origins, and submit at most one fit."""
        from controller.model_learning.contracts import CandidateOrigin, FitRequest, FrameObservation

        if not isinstance(frame, FrameObservation):
            raise ValueError("frame must be a FrameObservation")
        self.start()
        completed = () if self._evaluator is None else self._evaluator.observe(frame)
        operator = frame.calibration_fit or frame.calibration_stage is not None or frame.probe_q != 0.0
        if operator:
            reason = self._operator_rejection(frame, self.identity.role_generation)
            if reason is not None:
                self._operator_history.clear()
                decision = HistoryDecision(False, (reason,))
                observations = ()
            else:
                self._append_contiguous(self._operator_history, frame)
                decision = HistoryDecision(True, ())
                observations = tuple(self._operator_history)
            origin = CandidateOrigin.OPERATOR_CALIBRATION
        else:
            decision = self.passive_history.observe(frame)
            observations = self.passive_history.observations
            origin = CandidateOrigin.PASSIVE_ONLINE
        if (
            not decision.accepted
            or self._pending_request is not None
            or (self._prepared is not None and self._prepared.accepted)
        ):
            return GreyLearningObservation(decision, None, None, tuple(completed))
        trigger = fit_trigger(observations, identifiability=identifiability, config=self.trigger_config)
        if not trigger.ready:
            return GreyLearningObservation(decision, None, None, tuple(completed))
        first = observations[0].observation_sequence
        last = observations[-1].observation_sequence
        request_identity = {
            "origin": origin.value,
            "first": first,
            "last": last,
            "candidate_generation": self.identity.candidate_generation,
            "incumbent_digest": self.identity.incumbent_digest,
        }
        request_id = hashlib.sha256(
            json.dumps(request_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        request = FitRequest(
            request_id=request_id,
            origin=origin,
            window=self.identity.window(first, last),
            candidate_generation=self.identity.candidate_generation,
        )
        job = GreyFitJob(request=request, observations=observations, config=self.config)
        submission = self.worker.submit(job)
        if submission is FitSubmission.ACCEPTED:
            self._pending_request = request
        return GreyLearningObservation(decision, submission, request, tuple(completed))

    def poll_fit_off_path(
        self,
        *,
        live_identity: LiveLearningIdentity,
        live_origin: Any,
    ) -> GreyLearningDelivery | None:
        """Drain, stale-check, and build a candidate; Task 8 calls this off-worker."""
        from controller.model_learning.contracts import FitResult, FitStatus

        if self._pending_request is None:
            return None
        try:
            message = self.worker.receive(timeout_s=0.0)
        except TimeoutError:
            return None
        request = self._pending_request
        self._pending_request = None
        if isinstance(message.outcome, GreyFitError):
            return GreyLearningDelivery(message, (), None, ("fit-error",))
        success = message.outcome
        result = FitResult(
            request_id=request.request_id,
            origin=request.origin,
            window=request.window,
            candidate_generation=request.candidate_generation,
            status=FitStatus.SUCCEEDED,
            candidate_digest=grey_config_digest(success.config),
        )
        current_window = live_identity.window(
            request.window.first_observation_sequence,
            request.window.last_observation_sequence,
        )
        stale = stale_result_reasons(
            result,
            request=request,
            current_window=current_window,
            current_candidate_generation=live_identity.candidate_generation,
            current_origin=live_origin,
        )
        if stale:
            return GreyLearningDelivery(message, stale, None)
        if success.identifiability < self.trigger_config.min_identifiability:
            return GreyLearningDelivery(message, (), None, ("identifiability",))
        prepared = prepare_candidate_off_path(
            success,
            incumbent_pair=self.incumbent_pair,
            estimator_factory=self.estimator_factory,
            controller_factory=self.controller_factory,
            timing_probe=self.timing_probe,
        )
        self._release_prepared()
        self._prepared = prepared
        if prepared.accepted:
            from controller.model_learning.evaluation import CausalForecastEvaluator

            self._evaluator = CausalForecastEvaluator(
                role_generation=request.window.role_generation,
                candidate_generation=request.candidate_generation,
            )
            self._evaluation_cursor = 0
        return GreyLearningDelivery(message, (), prepared)

    def register_causal_forecasts(
        self,
        frame: Any,
        *,
        incumbent_predict: Callable[[CausalForecastInput], float],
        challenger_predict: Callable[[CausalForecastInput], float],
    ) -> tuple[Any, ...]:
        if self._prepared is None or not self._prepared.accepted or self._evaluator is None:
            return ()
        request = self._prepared.candidate.request
        origins = []
        for horizon in self.evaluation_config.required_horizons:
            origin = paired_forecast_origin(
                frame,
                horizon_steps=horizon,
                candidate_generation=request.candidate_generation,
                incumbent_digest=request.window.incumbent_digest,
                challenger_digest=self._prepared.candidate_digest,
                incumbent_predict=incumbent_predict,
                challenger_predict=challenger_predict,
            )
            if origin is not None:
                self._evaluator.register(origin)
                origins.append(origin)
        return tuple(origins)

    def evaluate_ready_off_path(self) -> Any | None:
        from controller.model_learning.evaluation import evaluate_forecasts

        if self._evaluator is None or self._prepared is None:
            return None
        rows = self._evaluator.completed_origins[self._evaluation_cursor :]
        horizons = {row.horizon_steps for row in rows}
        if not set(self.evaluation_config.required_horizons) <= horizons:
            return None
        request = self._prepared.candidate.request
        decision = evaluate_forecasts(
            tuple(rows),
            role_generation=request.window.role_generation,
            candidate_generation=request.candidate_generation,
            prior_consecutive_wins=self._consecutive_wins,
            config=self.evaluation_config,
        )
        self._evaluation_cursor = len(self._evaluator.completed_origins)
        self._consecutive_wins = decision.consecutive_wins
        self._last_evaluation = decision
        return decision

    def handoff_if_ready(
        self,
        *,
        confidence_accepted: bool,
        online_enabled: bool,
        prepare: Callable[[CandidatePreparation, Any], Any],
    ) -> CandidateHandoff | None:
        if self._ownership_transferred:
            return self._handoff
        if self._prepared is None or self._last_evaluation is None:
            return None
        try:
            self._handoff = handoff_candidate(
                self._prepared,
                evaluation=self._last_evaluation,
                confidence_accepted=confidence_accepted,
                online_enabled=online_enabled,
                prepare=prepare,
                install=lambda _pair: (_ for _ in ()).throw(
                    AssertionError("Task 7 cannot install a runtime pair")
                ),
            )
        except CandidateOwnershipTransferredError:
            self._ownership_transferred = True
            self._release_prepared()
            raise
        if not self._handoff.blockers:
            self._ownership_transferred = True
        return self._handoff

    def retire_evaluated_candidate(self, decision: Any) -> bool:
        """Release one terminally rejected candidate so a later fit may proceed."""
        if (
            decision is not self._last_evaluation
            or bool(getattr(decision, "accepted", False))
            or not tuple(getattr(decision, "blockers", ()))
        ):
            return False
        self._release_prepared()
        self._evaluator = None
        self._evaluation_cursor = 0
        self._consecutive_wins = 0
        self._last_evaluation = None
        self._handoff = None
        return True

    def close(self) -> None:
        self._release_prepared()
        close = getattr(self.worker, "close", None)
        if callable(close):
            close()
        self._started = False

    def __enter__(self) -> GreyLearningOrchestrator:
        return self.start()

    def __exit__(self, exception_type: Any, exception: Any, traceback: Any) -> None:
        self.close()
