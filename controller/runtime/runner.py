"""Temperature-controller execution seam (PID/MPC/etc).

`ControllerRunner` is the abstract interface `HoldMode.on_tick` drives:
set_target/submit/latest to run the control math, reconfigure() to rebuild the
core on a settings change, control_period() for the mode's poll interval, and
commands_fan() so the caller knows whether this controller issues its own fan
command (MPC) or leaves fan control to the temperature-profile logic.
`SyncControllerRunner` runs the underlying controller module's `update()`
synchronously on submit/latest -- control math and probe-read cadence are the
same cadence. `ThreadedControllerRunner` runs the core on a background thread
at its own control period and hands back non-blocking snapshots via
`.latest()`, decoupling control-math cadence from the probe-read cadence.
`build_runner` selects between the two by the core's `wants_async()` (MPC
requests the threaded runner; other controllers get the sync runner), and -- if
the selected controller will not build at all -- substitutes FALLBACK_CONTROLLER
so a live fire never ends up unregulated. See `_build_core` and `build_runner`
for why both of those matter.
"""

from __future__ import annotations

import collections
import importlib
import math
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeAlias

from common.control_trace import ActuationMode, ControllerType, ResultStaleState

from controller.base import ControllerTraceDiagnostics, MpcTraceDiagnostics, normalize_controller_output
from controller.mpc_allocator import AllocationResult

if TYPE_CHECKING:
    from controller.linear_mpc.contracts import FrameObservation


StatusScalar: TypeAlias = None | bool | int | float | str
StatusValue: TypeAlias = StatusScalar | Mapping[str, "StatusValue"] | tuple["StatusValue", ...]


@dataclass(frozen=True, slots=True)
class ObservationOutcomeEnvelope:
    """One runner-owned learner result for the exact observation the core saw."""

    submission_sequence: int
    configuration_generation: int
    observation: FrameObservation
    outcome: object


@dataclass(frozen=True, slots=True)
class ObservationSubmission:
    submission_sequence: int
    configuration_generation: int
    evicted_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class ObservationOutcomeDrain:
    envelopes: tuple[ObservationOutcomeEnvelope, ...]
    dropped_count: int
    dropped_sequences: tuple[int, ...]

    def __iter__(self):
        return iter(self.envelopes)

def _freeze_status_value(value: object) -> StatusValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, StatusValue] = {}
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise TypeError("controller status keys must be strings")
            frozen[key] = _freeze_status_value(nested_value)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(_freeze_status_value(item) for item in value)
    raise TypeError(f"unsupported controller status value: {type(value).__name__}")


def _freeze_status(status: Mapping[str, object]) -> Mapping[str, StatusValue]:
    return MappingProxyType({key: _freeze_status_value(value) for key, value in status.items()})


MutableStatusValue: TypeAlias = StatusScalar | dict[str, "MutableStatusValue"] | list["MutableStatusValue"]


def _thaw_status_value(value: object) -> MutableStatusValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        thawed: dict[str, MutableStatusValue] = {}
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise TypeError("controller status keys must be strings")
            thawed[key] = _thaw_status_value(nested_value)
        return thawed
    if isinstance(value, tuple | list):
        return [_thaw_status_value(item) for item in value]
    raise TypeError(f"unsupported controller status value: {type(value).__name__}")


def _thaw_status(status: Mapping[str, object]) -> dict[str, MutableStatusValue]:
    thawed = _thaw_status_value(status)
    if not isinstance(thawed, dict):
        raise TypeError("controller status must be a mapping")
    return thawed


def _control_period_seconds(value: object) -> float | None:
    """Return the quality budget, or disable budget checks for legacy cores."""
    if value is None:
        return None
    period = float(value)
    if not math.isfinite(period) or period <= 0:
        return None
    return period


@dataclass(slots=True)
class _ResultQualityTracker:
    """State that belongs to completed runner results, never the solver."""

    control_period: float | None
    deadline_miss_count: int = 0
    consecutive_deadline_miss_count: int = 0
    stale_state: ResultStaleState = ResultStaleState.FRESH

    def completed(self, result: "ControllerUpdateResult") -> tuple["ControllerUpdateResult", ResultStaleState | None]:
        deadline_missed = self.control_period is not None and result.solve_duration_seconds > self.control_period
        if deadline_missed:
            self.deadline_miss_count += 1
            self.consecutive_deadline_miss_count += 1
        else:
            self.consecutive_deadline_miss_count = 0
        recovered = self.stale_state is ResultStaleState.STALE
        self.stale_state = ResultStaleState.FRESH
        quality_result = replace(
            result,
            result_age_seconds=0.0,
            deadline_miss_count=self.deadline_miss_count,
            consecutive_deadline_miss_count=self.consecutive_deadline_miss_count,
            stale_state=ResultStaleState.FRESH,
            recovered=recovered,
        )
        return (
            _with_result_quality(quality_result),
            ResultStaleState.FRESH if recovered else None,
        )

    def polled(
        self, result: "ControllerUpdateResult", monotonic_now: float
    ) -> tuple["ControllerUpdateResult", ResultStaleState | None]:
        if result.revision == 0 or self.control_period is None:
            return result, None
        age = max(0.0, monotonic_now - result.solve_end_monotonic)
        stale = age >= 2.0 * self.control_period
        next_state = ResultStaleState.STALE if stale else ResultStaleState.FRESH
        transition = next_state is not self.stale_state
        if transition:
            self.stale_state = next_state
        if not transition and age == result.result_age_seconds and next_state is result.stale_state:
            return result, None
        quality_result = replace(
            result,
            result_age_seconds=age,
            stale_state=next_state,
            recovered=result.recovered if next_state is ResultStaleState.FRESH and not transition else False,
        )
        return _with_result_quality(quality_result), next_state if transition else None


def _quality_status(result: "ControllerUpdateResult") -> dict[str, StatusScalar]:
    return {
        "solve_duration_seconds": result.solve_duration_seconds,
        "result_age_seconds": result.result_age_seconds,
        "deadline_miss_count": result.deadline_miss_count,
        "consecutive_deadline_miss_count": result.consecutive_deadline_miss_count,
        "result_stale_state": result.stale_state.value,
        "result_recovered": result.recovered,
    }


def _actuation_mode_for(core) -> ActuationMode:
    value = core.actuation_mode()
    if not isinstance(value, ActuationMode):
        raise TypeError("controller actuation_mode() must return ActuationMode")
    return value


def _controller_type_for(value: object) -> ControllerType | None:
    try:
        return ControllerType(value)
    except TypeError, ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ControllerUpdateResult:
    """One atomically captured controller completion."""

    cycle_ratio: float
    fan: Mapping[str, float] | None
    input_temperature: float
    diagnostics: ControllerTraceDiagnostics | None = None
    allocation: AllocationResult | None = None
    status: Mapping[str, StatusValue] | None = None
    revision: int = 0
    solve_start_monotonic: float | None = None
    solve_end_monotonic: float | None = None
    solve_duration_seconds: float | None = None
    completed_wall_time: float | None = None
    result_age_seconds: float = 0.0
    deadline_miss_count: int = 0
    consecutive_deadline_miss_count: int = 0
    stale_state: ResultStaleState = ResultStaleState.FRESH
    recovered: bool = False

    def __post_init__(self):
        if self.fan is not None:
            object.__setattr__(self, "fan", MappingProxyType(dict(self.fan)))
        if self.status is not None:
            object.__setattr__(self, "status", _freeze_status(self.status))
        if not math.isfinite(self.input_temperature):
            raise ValueError("input_temperature must be finite")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        if not math.isfinite(self.result_age_seconds) or self.result_age_seconds < 0:
            raise ValueError("result age must be finite and non-negative")
        if self.deadline_miss_count < 0 or self.consecutive_deadline_miss_count < 0:
            raise ValueError("deadline miss counts must be non-negative")
        if not isinstance(self.stale_state, ResultStaleState):
            raise TypeError("stale_state must be ResultStaleState")
        solve_start = self.solve_start_monotonic
        solve_end = self.solve_end_monotonic
        solve_duration = self.solve_duration_seconds
        completion_wall_time = self.completed_wall_time
        if self.revision == 0:
            if (
                solve_start is not None
                or solve_end is not None
                or solve_duration is not None
                or completion_wall_time is not None
            ):
                raise ValueError("an uncompleted result has no completion timestamps")
            return
        if solve_start is None or solve_end is None or solve_duration is None or completion_wall_time is None:
            raise ValueError("a completed result requires all completion timestamps")
        if not all(math.isfinite(value) for value in (solve_start, solve_end, solve_duration, completion_wall_time)):
            raise ValueError("completion timestamps must be finite")
        if solve_end < solve_start:
            raise ValueError("solve end must not precede solve start")
        if solve_duration != solve_end - solve_start:
            raise ValueError("solve duration must equal its monotonic interval")


def _with_result_quality(result: ControllerUpdateResult) -> ControllerUpdateResult:
    """Copy runner-owned timing into the immutable MPC trace diagnostics."""
    diagnostics = result.diagnostics
    if not isinstance(diagnostics, MpcTraceDiagnostics):
        return result
    return replace(
        result,
        diagnostics=replace(
            diagnostics,
            solve_start_monotonic=result.solve_start_monotonic,
            solve_end_monotonic=result.solve_end_monotonic,
            solve_duration_seconds=result.solve_duration_seconds,
            result_age_seconds=result.result_age_seconds,
            deadline_miss_count=result.deadline_miss_count,
            consecutive_deadline_miss_count=result.consecutive_deadline_miss_count,
            stale_state=result.stale_state,
            recovered=result.recovered,
        ),
    )


def _capture_completed_result(core, temp, revision, *, monotonic_clock, wall_clock):
    solve_start = monotonic_clock()
    raw = core.update(temp)
    solve_end = monotonic_clock()
    cycle_ratio, fan = normalize_controller_output(raw)
    status = getattr(core, "get_status", lambda: None)()
    diagnostics = getattr(core, "trace_diagnostics", lambda: None)()
    allocation = getattr(core, "trace_allocation", lambda: None)()
    return ControllerUpdateResult(
        cycle_ratio=cycle_ratio,
        fan=fan,
        input_temperature=float(temp),
        diagnostics=diagnostics,
        allocation=allocation,
        status=status,
        revision=revision,
        solve_start_monotonic=solve_start,
        solve_end_monotonic=solve_end,
        solve_duration_seconds=solve_end - solve_start,
        completed_wall_time=wall_clock(),
    )


class ControllerRunner(ABC):
    @abstractmethod
    def set_target(self, setpoint): ...
    @abstractmethod
    def submit(self, temp): ...
    @abstractmethod
    def latest(self) -> ControllerUpdateResult: ...
    @abstractmethod
    def reconfigure(self, settings, control, logger=None): ...
    @abstractmethod
    def control_period(self): ...
    @abstractmethod
    def commands_fan(self): ...
    @abstractmethod
    def wants_async(self): ...
    @abstractmethod
    def actuation_mode(self) -> ActuationMode: ...
    @abstractmethod
    def controller_type(self) -> ControllerType | None: ...
    @abstractmethod
    def configuration_revision(self) -> int: ...
    @abstractmethod
    def runs_async(self) -> bool: ...
    @abstractmethod
    def set_output(self, applied): ...
    @abstractmethod
    def observe_frame(self, observation: FrameObservation): ...
    @abstractmethod
    def drain_observation_outcomes(self) -> tuple[ObservationOutcomeEnvelope, ...]: ...
    @abstractmethod
    def get_model_snapshot(self): ...
    @abstractmethod
    def restore_model(self, snapshot): ...
    @abstractmethod
    def refit_from_cook(self): ...
    @abstractmethod
    def controller_state(self) -> dict[str, object]: ...
    @abstractmethod
    def stop(self): ...


class SyncControllerRunner(ControllerRunner):
    def __init__(
        self,
        core,
        *,
        controller_type: ControllerType | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        warning_callback: Callable[[ResultStaleState], None] | None = None,
    ):
        self._core = core
        self._temp = None
        self._revision = 0
        self._latest_result = None
        self._outcome_drops_since_drain = 0
        self._outcome_dropped_sequences = collections.deque(maxlen=60)
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._warning_callback = warning_callback
        self._controller_type = controller_type
        self._observation_sequence = 0
        self._observation_outcomes = collections.deque(maxlen=_MAX_PENDING_OBSERVATIONS)
        self._dropped_observation_outcomes = 0
        self._configuration_revision = 0
        period = getattr(core, "get_control_period", lambda: None)()
        self._quality = _ResultQualityTracker(_control_period_seconds(period))

    def set_target(self, setpoint):
        self._core.set_target(setpoint)

    def submit(self, temp):
        self._temp = temp

    def latest(self) -> ControllerUpdateResult:
        self._revision += 1
        result = _capture_completed_result(
            self._core,
            self._temp,
            self._revision,
            monotonic_clock=self._monotonic_clock,
            wall_clock=self._wall_clock,
        )
        self._latest_result, transition = self._quality.completed(result)
        if transition is not None and self._warning_callback is not None:
            self._warning_callback(transition)
        return self._latest_result

    def latest_from(self, temp):
        self.submit(temp)
        return self.latest()

    def reconfigure(self, settings, control, logger=None):
        core, status = _build_core(settings, control, logger=logger)
        if status == "Active":
            self._core = core
            self._controller_type = _controller_type_for(_selected_controller(settings))
            self._configuration_revision += 1
            self._quality.control_period = _control_period_seconds(core.get_control_period())
        else:
            report_reconfigure_failure(settings, logger=logger)
        return status

    def control_period(self):
        return self._core.get_control_period()

    def commands_fan(self):
        return self._core.commands_fan()

    def wants_async(self):
        return self._core.wants_async()

    def actuation_mode(self) -> ActuationMode:
        return _actuation_mode_for(self._core)

    def controller_type(self) -> ControllerType | None:
        return self._controller_type

    def configuration_revision(self) -> int:
        return self._configuration_revision

    def runs_async(self) -> bool:
        return False

    def stop(self):
        pass

    def set_output(self, applied):
        self._core.set_output(applied)

    def observe_frame(self, observation: FrameObservation):
        self._observation_sequence += 1
        observe = getattr(self._core, "observe_frame", None)
        outcome = observe(observation) if observe is not None else None
        if outcome is not None:
            if len(self._observation_outcomes) == self._observation_outcomes.maxlen:
                dropped = self._observation_outcomes[0]
                self._dropped_observation_outcomes += 1
                self._outcome_drops_since_drain += 1
                self._outcome_dropped_sequences.append(dropped.submission_sequence)
            self._observation_outcomes.append(
                ObservationOutcomeEnvelope(
                    self._observation_sequence, self._configuration_revision, observation, outcome
                )
            )
        return ObservationSubmission(self._observation_sequence, self._configuration_revision)

    def drain_observation_outcomes(self) -> ObservationOutcomeDrain:
        drain = ObservationOutcomeDrain(
            tuple(self._observation_outcomes),
            self._outcome_drops_since_drain,
            tuple(self._outcome_dropped_sequences),
        )
        self._observation_outcomes.clear()
        self._outcome_drops_since_drain = 0
        self._outcome_dropped_sequences.clear()
        return drain

    def get_model_snapshot(self):
        return self._core.get_model_snapshot()

    def restore_model(self, snapshot):
        return self._core.restore_model(snapshot)

    def refit_from_cook(self):
        """Ask the core to learn from the cook that just ended, if it can.

        A controller with no identification of its own simply has nothing to
        refit, which is None rather than an error.
        """
        fn = getattr(self._core, "refit_from_cook", None)
        return fn() if fn is not None else None

    def controller_state(self):
        """A mutable, JSON-safe copy of the current status snapshot."""
        if self._latest_result is not None:
            self._latest_result, transition = self._quality.polled(self._latest_result, self._monotonic_clock())
            if transition is not None and self._warning_callback is not None:
                self._warning_callback(transition)
            state = {} if self._latest_result.status is None else _thaw_status(self._latest_result.status)
            if self.actuation_mode() is ActuationMode.FRAMED_PULSE:
                state.update(_quality_status(self._latest_result))
            return state
        status = self._core.get_status()
        return {} if status is None else _thaw_status(status)


_UNSET = object()

# Hold reports once per work-loop tick, and that loop runs at roughly 20 Hz
# (`ControlMode.run` sleeps 0.05 s), while the worker drains only once per
# controller solve. This ceiling spans a stalled solve without letting the
# backlog grow without bound; the oldest reports are the ones to lose, since
# a consumer identifying a process model cares about recent duty.
_MAX_PENDING_OUTPUTS = 2048

# A completed learning frame covers 20 seconds. Thirty retained frames span the
# required ten-minute recovery window while bounding the worker's handoff.
_MAX_PENDING_OBSERVATIONS = 30


def _owned_model_snapshot(snapshot):
    """A copy the caller can hold and mutate without reaching into the core's
    live model, made where the snapshot is produced rather than where it is
    read. `get_model_snapshot()` may legitimately answer None."""
    return None if snapshot is None else dict(snapshot)


def _safe_initial_status(core):
    """core.get_status() called here runs before the core has proven itself
    with a successful update() -- outside the try/except _build_core uses
    specifically so a constructor bug can never kill the control process with
    the auger already on (see its docstring). An exception here must not
    either."""
    try:
        return core.get_status()
    except Exception:
        return None


class ThreadedControllerRunner(ControllerRunner):
    """Runs core.update() on a background thread at the core's control period, so
    an expensive solve never blocks the caller. submit()/latest() are
    non-blocking snapshots; the running core is mutated only by the thread."""

    def __init__(
        self,
        core,
        *,
        controller_type: ControllerType | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        warning_callback: Callable[[ResultStaleState], None] | None = None,
        wait_for_period: Callable[[float], None] | None = None,
    ):
        self._core = core
        self._lock = threading.Lock()
        self._temp = None
        self._output = ControllerUpdateResult(
            cycle_ratio=0.0,
            fan=None,
            input_temperature=0.0,
            diagnostics=None,
            status=None,
            revision=0,
            solve_start_monotonic=None,
            solve_end_monotonic=None,
            solve_duration_seconds=None,
            completed_wall_time=None,
        )
        self._revision = 0
        self._pending_target = _UNSET
        self._pending_core = None
        self._pending_controller_type = None
        self._configuration_revision = 0
        self._outcome_drops_since_drain = 0
        self._outcome_dropped_sequences = collections.deque(maxlen=60)
        self._pending_outputs = collections.deque(maxlen=_MAX_PENDING_OUTPUTS)
        self._pending_dropped = 0
        self._pending_restore = None
        self._pending_observations: list[tuple[int, int, FrameObservation]] = []
        self._accept_observations = True
        self._dropped_observations = 0
        self._observations_discontinuous: set[int] = set()
        self._observation_sequence = 0
        self._observation_outcomes = collections.deque(maxlen=_MAX_PENDING_OBSERVATIONS)
        self._dropped_observation_outcomes = 0
        self._model_snapshot = _owned_model_snapshot(core.get_model_snapshot())
        self._initial_status = _safe_initial_status(core)
        self._control_period = core.get_control_period()
        self._commands_fan = core.commands_fan()
        self._actuation_mode = _actuation_mode_for(core)
        self._controller_type = controller_type
        self._quality = _ResultQualityTracker(_control_period_seconds(self._control_period))
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._warning_callback = warning_callback
        self._stop_event = threading.Event()
        self._wait_for_period = self._stop_event.wait if wait_for_period is None else wait_for_period
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            stopping = self._stop_event.is_set()
            with self._lock:
                target = self._pending_target
                self._pending_target = _UNSET
                new_core = None
                new_controller_type = None
                pending_outputs = list(self._pending_outputs)
                self._pending_outputs.clear()
                restore = self._pending_restore
                self._pending_restore = None
            # A pending core is installed only after the old core has drained
            # every accepted observation.  The lock-held emptiness check keeps
            # the returned generation bound to the consuming core.
            if restore is not None:
                self._core.restore_model(restore)
            if target is not _UNSET:
                self._core.set_target(target)
            # A command must reach the core before the temperature that command
            # caused, and in the order the auger saw it.
            for applied in sorted(pending_outputs, key=lambda a: a.timestamp):
                self._core.set_output(applied)
            # Learner calls must never hold _lock. Drain until a lock-protected
            # empty observation queue commits this iteration's temperature
            # update; an observation that wins the lock before that commit is
            # necessarily delivered first.
            while True:
                with self._lock:
                    if self._pending_observations:
                        if self._pending_core is not None:
                            pending_observations = [
                                item for item in self._pending_observations if item[1] == self._configuration_revision
                            ]
                            self._pending_observations = [
                                item for item in self._pending_observations if item[1] != self._configuration_revision
                            ]
                            handoff_batch = bool(pending_observations)
                        else:
                            pending_observations = self._pending_observations
                            self._pending_observations = []
                            handoff_batch = False
                        if pending_observations and pending_observations[0][1] in self._observations_discontinuous:
                            sequence, generation, observation = pending_observations[0]
                            pending_observations[0] = (sequence, generation, replace(observation, continuous=False))
                            self._observations_discontinuous.discard(generation)
                        update_temp = _UNSET if pending_observations else self._temp
                    else:
                        pending_observations = []
                        handoff_batch = False
                        update_temp = self._temp
                if pending_observations:
                    observe = getattr(self._core, "observe_frame", None)
                    if observe is not None:
                        for sequence, generation, observation in pending_observations:
                            outcome = observe(observation)
                            if outcome is not None:
                                with self._lock:
                                    if len(self._observation_outcomes) == self._observation_outcomes.maxlen:
                                        dropped = self._observation_outcomes[0]
                                        self._dropped_observation_outcomes += 1
                                        self._outcome_drops_since_drain += 1
                                        self._outcome_dropped_sequences.append(dropped.submission_sequence)
                                    self._observation_outcomes.append(
                                        ObservationOutcomeEnvelope(sequence, generation, observation, outcome)
                                    )
                    if handoff_batch:
                        break
                    continue
                break
            with self._lock:
                if self._pending_core is not None:
                    new_core = self._pending_core
                    new_controller_type = self._pending_controller_type
                    self._pending_core = None
                    self._pending_controller_type = None
                    self._core = new_core
                    self._control_period = new_core.get_control_period()
                    self._commands_fan = new_core.commands_fan()
                    self._actuation_mode = _actuation_mode_for(new_core)
                    self._controller_type = new_controller_type
                    self._quality.control_period = _control_period_seconds(self._control_period)
                    self._configuration_revision += 1
            if update_temp is not _UNSET and update_temp is not None:
                result = _capture_completed_result(
                    self._core,
                    update_temp,
                    self._revision + 1,
                    monotonic_clock=self._monotonic_clock,
                    wall_clock=self._wall_clock,
                )
                model = _owned_model_snapshot(self._core.get_model_snapshot())
                with self._lock:
                    result, transition = self._quality.completed(result)
                    self._revision = result.revision
                    self._output = result
                    self._model_snapshot = model
                if transition is not None and self._warning_callback is not None:
                    self._warning_callback(transition)
            if stopping:
                with self._lock:
                    if self._pending_observations:
                        continue
                return
            self._wait_for_period(self._control_period or 1.0)

    def set_target(self, setpoint):
        with self._lock:
            self._pending_target = setpoint

    def submit(self, temp):
        with self._lock:
            self._temp = temp

    def latest(self) -> ControllerUpdateResult:
        with self._lock:
            self._output, transition = self._quality.polled(self._output, self._monotonic_clock())
            result = self._output
        if transition is not None and self._warning_callback is not None:
            self._warning_callback(transition)
        return result

    def reconfigure(self, settings, control, logger=None):
        core, status = _build_core(settings, control, logger=logger)
        if status == "Active":
            with self._lock:
                self._pending_core = core
                self._pending_controller_type = _controller_type_for(_selected_controller(settings))
        else:
            report_reconfigure_failure(settings, logger=logger)
        return status

    def control_period(self):
        return self._control_period

    def commands_fan(self):
        return self._commands_fan

    def wants_async(self):
        return True

    def actuation_mode(self) -> ActuationMode:
        with self._lock:
            return self._actuation_mode

    def controller_type(self) -> ControllerType | None:
        with self._lock:
            return self._controller_type

    def configuration_revision(self) -> int:
        with self._lock:
            return self._configuration_revision

    def runs_async(self) -> bool:
        return True

    def controller_state(self):
        with self._lock:
            self._output, transition = self._quality.polled(self._output, self._monotonic_clock())
            status = self._output.status
            source = self._initial_status if status is None else status
            state = {} if source is None else _thaw_status(source)
            if self._actuation_mode is ActuationMode.FRAMED_PULSE and self._output.revision > 0:
                state.update(_quality_status(self._output))
            state["pending_dropped"] = self._pending_dropped
            state["pending_observations"] = len(self._pending_observations)
            state["dropped_observations"] = self._dropped_observations
        if transition is not None and self._warning_callback is not None:
            self._warning_callback(transition)
        return state

    def set_output(self, applied):
        with self._lock:
            if len(self._pending_outputs) == self._pending_outputs.maxlen:
                self._pending_dropped += 1
            self._pending_outputs.append(applied)

    def get_model_snapshot(self):
        with self._lock:
            return self._model_snapshot


    def observe_frame(self, observation: FrameObservation):
        with self._lock:
            if not self._accept_observations:
                return None
            self._observation_sequence += 1
            sequence = self._observation_sequence
            generation = self._configuration_revision + (1 if self._pending_core is not None else 0)
            index = 0
            while (
                index < len(self._pending_observations)
                and self._pending_observations[index][2].frame_end_s <= observation.frame_end_s
            ):
                index += 1
            self._pending_observations.insert(index, (sequence, generation, observation))
            evicted_sequence = None
            if len(self._pending_observations) > _MAX_PENDING_OBSERVATIONS:
                evicted_sequence, evicted_generation, _ = self._pending_observations.pop(0)
                self._dropped_observations += 1
                self._observations_discontinuous.add(evicted_generation)
            return ObservationSubmission(sequence, generation, evicted_sequence)

    def drain_observation_outcomes(self) -> ObservationOutcomeDrain:
        with self._lock:
            drain = ObservationOutcomeDrain(
                tuple(self._observation_outcomes),
                self._outcome_drops_since_drain,
                tuple(self._outcome_dropped_sequences),
            )
            self._observation_outcomes.clear()
            self._outcome_drops_since_drain = 0
            self._outcome_dropped_sequences.clear()
        return drain

    def restore_model(self, snapshot):
        """Queue a snapshot for the worker to attempt to adopt.

        True means accepted for restore, not adopted: the core is mutated only
        on the worker thread, so whether the snapshot was actually adopted is
        not knowable from here.
        """
        if snapshot is None:
            return False
        with self._lock:
            # A snapshot queued before the worker gets to the previous one
            # supersedes it -- only the most recent restore request matters,
            # since an older one describes a model the caller has moved past.
            self._pending_restore = dict(snapshot)
        return True

    def refit_from_cook(self):
        """Refit the core's model from the cook that just ended.

        Runs synchronously on the CALLER's thread, which is why teardown asks
        for it only after `stop()`: a refit takes seconds and mutates the
        core's config, so it must never overlap a solve.

        `stop()` joins with a timeout and so cannot promise the worker is
        gone. A worker still running would overwrite the republish below on
        its next pass and the cook's learning would vanish without a trace, so
        that case raises instead -- losing a refit is acceptable, losing it
        silently is not.

        The worker is what normally republishes the model snapshot, and it has
        stopped by now, so this republishes it directly: otherwise an adopted
        model would exist in the core and be invisible to the
        `get_model_snapshot()` the caller persists it through.
        """
        if self._thread.is_alive():
            raise RuntimeError("the controller worker did not stop; refusing to refit behind it")
        fn = getattr(self._core, "refit_from_cook", None)
        if fn is None:
            return None
        refit_error = None
        try:
            return fn()
        except BaseException as error:
            refit_error = error
            raise
        finally:
            try:
                model = _owned_model_snapshot(self._core.get_model_snapshot())
                with self._lock:
                    self._model_snapshot = model
            except Exception:
                if refit_error is None:
                    raise

    def stop(self):
        with self._lock:
            self._accept_observations = False
        self._stop_event.set()
        self._thread.join(timeout=2.0)
FALLBACK_CONTROLLER = "pid"




def _selected_controller(settings):
    """settings['controller']['selected'], tolerating a malformed settings tree.

    The reporting paths below run precisely when something is already wrong, so
    they must not be the thing that raises.
    """
    try:
        return settings["controller"]["selected"]
    except KeyError, TypeError:
        return None


def _raise_banner(text, logger=None):
    """Put `text` in front of the user (dashboard banner + control log).

    Same idiom build_devices() uses for a display or grill platform that will not
    load. Deliberately best-effort: a datastore hiccup while reporting a
    controller problem must not itself take down the control loop.
    """
    try:
        from common.common import ErrorKind
        from common.datastore_accessors import read_errors, write_errors

        errors = read_errors(ErrorKind.CONTROL)
        errors.append(text)
        write_errors(ErrorKind.CONTROL, errors)
    except Exception:
        pass
    if logger is not None:
        try:
            logger.error(text)
        except Exception:
            pass


def _dependency_hint(controller_type, settings):
    """A sentence naming the uninstalled package, or "" if that is not the problem."""
    try:
        from common.controller_deps import check_controller_dependencies

        config = (settings.get("controller") or {}).get("config", {}).get(controller_type, {})
        missing = check_controller_dependencies(controller_type, config)
    except Exception:
        return ""
    if missing is None:
        return ""
    names = ", ".join(missing.modules)
    return (
        f"It needs the {names} package, which is not part of a standard PiFire install. "
        f"Open Settings > Controller, select {controller_type.upper()} and save: PiFire will install it "
        f"in the background (several minutes on a Pi), after which the controller will start normally. "
    )


def _build_core(settings, control, logger=None, controller_type=None):
    """Construct the selected controller core. NEVER raises.

    Both halves are guarded: `import_module` (a missing or renamed module) and --
    the half that used to be outside the guard -- the CONSTRUCTOR. That gap was
    not theoretical: controller/mpc.py imports do_mpc lazily inside __init__, so
    on an install without the optional `mpc` extra the module imports cleanly and
    then `Controller(...)` raises ModuleNotFoundError. Nothing between here and
    the process entry point catches it, so the exception propagated out of
    HoldMode.setup() -> Controller.run() and killed the control process with the
    auger already commanded on. supervisor restarted it, control was flushed to
    Stop, and the user's cook ended with the fire out and no explanation.
    """
    controller_type = controller_type or settings["controller"]["selected"]
    try:
        module = importlib.import_module(f"controller.{controller_type}")
    except Exception:
        if logger is not None:
            logger.exception("Error occurred loading controller module. Trace dump: ")
        return None, "Inactive"
    try:
        core = module.Controller(
            settings["controller"]["config"][controller_type], settings["globals"]["units"], settings["cycle_data"]
        )
        core.set_target(control["primary_setpoint"])
    except Exception:
        if logger is not None:
            logger.exception(f"Error occurred building the [{controller_type}] controller. Trace dump: ")
        return None, "Inactive"
    return core, "Active"


def _wrap(core, status, controller_type):
    if core is None:
        return None, status
    actual_type = _controller_type_for(controller_type)
    if core.wants_async():
        return ThreadedControllerRunner(core, controller_type=actual_type), status
    return SyncControllerRunner(core, controller_type=actual_type), status


def build_runner(settings, control, logger=None):
    """Build the runner for a work cycle, substituting the default controller if
    the selected one will not build.

    Substituting is the safe choice here, and it is not the obvious one, so:
    this is called from HoldMode.setup() AFTER power/fan/auger have already been
    commanded on. Refusing to build aborts the Hold cycle at setup_safety with
    the fire lit and no controller regulating it. Holding at setpoint on the
    default PID controller -- with a banner saying exactly what was substituted
    and why -- keeps the user's cook alive and the grill controllable, which is
    the whole point. It mirrors build_devices(), which loads display.none or the
    prototype platform rather than letting a bad module stop the loop.

    Nothing is written back to settings: the user's choice is preserved so that
    re-saving it (once the missing package is installed) just works.
    """
    core, status = _build_core(settings, control, logger=logger)
    if core is not None:
        return _wrap(core, status, _selected_controller(settings))

    selected = _selected_controller(settings)
    if selected == FALLBACK_CONTROLLER:
        _raise_banner(
            f"The [{selected}] controller could not be started and PiFire has no fallback for it. "
            f"The current cook cycle has been stopped. Check logs/control.log for details.",
            logger=logger,
        )
        return None, status

    hint = _dependency_hint(selected, settings)
    core, status = _build_core(settings, control, logger=logger, controller_type=FALLBACK_CONTROLLER)
    if core is None:
        _raise_banner(
            f"The [{selected}] controller could not be started, and neither could the fallback "
            f"[{FALLBACK_CONTROLLER}] controller. {hint}The current cook cycle has been stopped. "
            f"Check logs/control.log for details.",
            logger=logger,
        )
        return None, status

    _raise_banner(
        f"The [{selected}] controller could not be started. {hint}"
        f"PiFire is running the [{FALLBACK_CONTROLLER}] controller instead so the grill stays under control. "
        f"Your controller selection has not been changed.",
        logger=logger,
    )
    return _wrap(core, status, FALLBACK_CONTROLLER)


def report_reconfigure_failure(settings, logger=None):
    """Tell the user a settings-triggered controller swap did not take.

    The runner keeps the core it was already using (see the `status == "Active"`
    guard in both reconfigure implementations), so a mid-cook switch to a
    controller that will not build is a no-op rather than a loss of control --
    but a silent no-op would leave the user believing MPC is regulating the fire.
    """
    selected = _selected_controller(settings)
    _raise_banner(
        f"Could not switch to the [{selected}] controller. {_dependency_hint(selected, settings)}"
        f"The previous controller is still running your cook.",
        logger=logger,
    )
