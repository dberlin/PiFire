"""Pure guarded online-calibration state machine."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Callable, Mapping

_DWELL_COUNTS = (2, 3, 5, 4, 3, 2)
_STAGES = ("low", "middle", "high")
_COAST = "coast"


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    value = float(value)
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _unit(value: float, name: str) -> float:
    value = _finite(value, name)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return value


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    band_centers_c: tuple[float, ...] = ((225.0 - 32.0) * 5.0 / 9.0, (325.0 - 32.0) * 5.0 / 9.0, (425.0 - 32.0) * 5.0 / 9.0)
    max_probe_q: float = 0.05
    min_stage_observations: int = 30
    min_realized_levels: int = 3
    min_realized_variance: float = 0.001
    min_positive_observations: int = 6
    min_negative_observations: int = 6
    stage_timeout_s: float = 3600.0
    band_tolerance_c: float = 2.0

    def __post_init__(self) -> None:
        centers = tuple(_finite(value, "band_centers_c") for value in self.band_centers_c)
        if len(centers) != 3 or any(right <= left for left, right in zip(centers, centers[1:])):
            raise ValueError("band_centers_c must contain three increasing finite values")
        probe = _finite(self.max_probe_q, "max_probe_q")
        if not 0.0 < probe <= 1.0:
            raise ValueError("max_probe_q must be in (0, 1]")
        variance = _finite(self.min_realized_variance, "min_realized_variance")
        if variance < 0.0:
            raise ValueError("min_realized_variance must be non-negative")
        timeout = _finite(self.stage_timeout_s, "stage_timeout_s")
        tolerance = _finite(self.band_tolerance_c, "band_tolerance_c")
        if timeout <= 0.0 or tolerance < 0.0:
            raise ValueError("timeout must be positive and band tolerance non-negative")
        object.__setattr__(self, "band_centers_c", centers)
        object.__setattr__(self, "max_probe_q", probe)
        object.__setattr__(self, "min_stage_observations", _positive_int(self.min_stage_observations, "min_stage_observations"))
        object.__setattr__(self, "min_realized_levels", _positive_int(self.min_realized_levels, "min_realized_levels"))
        object.__setattr__(self, "min_realized_variance", variance)
        object.__setattr__(self, "min_positive_observations", _positive_int(self.min_positive_observations, "min_positive_observations"))
        object.__setattr__(self, "min_negative_observations", _positive_int(self.min_negative_observations, "min_negative_observations"))
        object.__setattr__(self, "stage_timeout_s", timeout)
        object.__setattr__(self, "band_tolerance_c", tolerance)


@dataclass(frozen=True, slots=True)
class CalibrationCommand:
    command_revision: int
    maximum_temperature_c: float
    seed: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.command_revision, bool) or not isinstance(self.command_revision, int) or self.command_revision < 0:
            raise ValueError("command_revision must be a non-negative integer")
        object.__setattr__(self, "maximum_temperature_c", _finite(self.maximum_temperature_c, "maximum_temperature_c"))
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")


@dataclass(frozen=True, slots=True)
class CalibrationRuntimeContext:
    now_s: float
    temp_c: float
    target_c: float
    baseline_q: float
    realized_q: float
    safety_ceiling_c: float
    allocator_headroom: float
    error_rate_headroom: float
    capability_headroom: float
    saturation_headroom: float
    rank_progress: float
    coverage_progress: float
    lid_open: bool = False
    manual_mode: bool = False
    manual_output: bool = False
    safety_inhibited: bool = False
    temperature_guard: bool = False
    probe_valid: bool = True
    stale_result: bool = False
    skipped_frame: bool = False
    reset_frame: bool = False
    continuous: bool = True
    actuation_known: bool = True
    fallback: bool = False

    def __post_init__(self) -> None:
        for name in ("now_s", "temp_c", "target_c", "safety_ceiling_c"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        for name in ("baseline_q", "realized_q", "allocator_headroom", "error_rate_headroom", "capability_headroom", "saturation_headroom", "rank_progress", "coverage_progress"):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        for name in ("lid_open", "manual_mode", "manual_output", "safety_inhibited", "temperature_guard", "probe_valid", "stale_result", "skipped_frame", "reset_frame", "continuous", "actuation_known", "fallback"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a bool")


@dataclass(frozen=True, slots=True)
class CalibrationEvent:
    kind: str
    stage: str | None
    intended_probe_q: float
    bounded_probe_q: float
    realized_probe_sum: float
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.kind or (self.stage is not None and self.stage not in _STAGES):
            raise ValueError("invalid calibration event")
        object.__setattr__(self, "intended_probe_q", _finite(self.intended_probe_q, "intended_probe_q"))
        object.__setattr__(self, "bounded_probe_q", _finite(self.bounded_probe_q, "bounded_probe_q"))
        object.__setattr__(self, "realized_probe_sum", _finite(self.realized_probe_sum, "realized_probe_sum"))
        reasons = tuple(self.reasons)
        if any(not isinstance(reason, str) or not reason for reason in reasons):
            raise ValueError("reasons must be non-empty strings")
        object.__setattr__(self, "reasons", reasons)


@dataclass(frozen=True, slots=True)
class CalibrationProgress:
    eligible_observations: int = 0
    realized_levels: int = 0
    realized_variance: float = 0.0
    positive_observations: int = 0
    negative_observations: int = 0
    rank_progress: float = 0.0
    coverage_progress: float = 0.0
    continuous: bool = True
    realized_probe_sum: float = 0.0
    clipping_error: float = 0.0


@dataclass(frozen=True, slots=True)
class CalibrationDecision:
    active: bool
    probe_q: float
    stage: str | None
    progress: CalibrationProgress
    events: tuple[CalibrationEvent, ...] = ()
    target_c: float | None = None
    activation_ready: bool = False


@dataclass(frozen=True, slots=True)
class _State:
    command: CalibrationCommand
    stage_index: int
    stage_started_s: float
    schedule_position: int
    signed_dwell_plan: tuple[int, ...]
    schedule: tuple[float, ...]
    eligible_observations: int = 0
    realized_values: tuple[float, ...] = ()
    positive_observations: int = 0
    negative_observations: int = 0
    realized_probe_sum: float = 0.0
    rank_progress: float = 0.0
    coverage_progress: float = 0.0
    continuous: bool = True
    current_probe_q: float = 0.0
    clipping_error: float = 0.0
    coasting: bool = False
    completion_history: tuple[CalibrationProgress, ...] = ()


Predictor = Callable[[float, float, CalibrationRuntimeContext], float]


class CalibrationCoordinator:
    def __init__(self, config: CalibrationConfig | None = None, predict_max_c: Predictor | None = None) -> None:
        self._config = config or CalibrationConfig()
        self._predict_max_c = predict_max_c
        self._state: _State | None = None
        self._last: CalibrationDecision | None = None
        self._paused = False

    @property
    def config(self) -> CalibrationConfig:
        return self._config

    def start(self, command: CalibrationCommand, runtime: CalibrationRuntimeContext) -> CalibrationDecision:
        reasons = self._guard_reasons(runtime)
        if command.maximum_temperature_c >= runtime.safety_ceiling_c:
            reasons += ("safety_ceiling",)
        if reasons:
            return self._terminal("start_rejected", None, reasons)
        if self._predict_max_c is None:
            return self._terminal("start_rejected", None, ("prediction_unavailable",))
        plan = self._signed_dwell_plan(command.seed)
        schedule = tuple(sign * self._config.max_probe_q for sign in self._expand(plan))
        state = _State(command, 0, runtime.now_s, 0, plan, schedule)
        self._state = state
        probe, reasons = self._bound_probe(schedule[0], runtime)
        if reasons:
            return self._terminal("start_rejected", "low", reasons)
        self._state = self._with_probe(state, probe)
        return self._remember(CalibrationDecision(True, probe, "low", self._progress(self._state), (
            self._event("start_accepted", "low", schedule[0], probe, self._state),
            self._event("stage_started", "low", schedule[0], probe, self._state),
        ), self._config.band_centers_c[0]))

    def stop(self, runtime: CalibrationRuntimeContext | None = None) -> CalibrationDecision:
        return self._terminal("stopped", self._actual_stage, ("operator_stop",)) if self._state else self._last or self._terminal("stopped", None, ("not_active",))

    def cancel_probe(self, reason: str, runtime: CalibrationRuntimeContext | None = None) -> CalibrationDecision:
        if not isinstance(reason, str) or not reason:
            raise ValueError("reason must be a non-empty string")
        return self._terminal("safety_aborted", self._actual_stage, (reason,))

    def pause(self) -> CalibrationDecision:
        if self._state is None:
            return self._last or self._terminal("paused", None, ("not_active",))
        self._paused = True
        return self._remember(self._decision(True, 0.0, self._stage, self._state, (self._event("paused", self._actual_stage, self._state.current_probe_q, 0.0, self._state),)))

    def resume(self, runtime: CalibrationRuntimeContext) -> CalibrationDecision:
        if self._state is None:
            return self._last or self._terminal("incomplete", None, ("not_started",))
        reasons = self._guard_reasons(runtime)
        if reasons:
            return self._terminal("safety_aborted", self._actual_stage, reasons)
        probe, reasons = self._bound_probe(self._state.current_probe_q, runtime)
        if reasons:
            return self._terminal("safety_aborted", self._actual_stage, reasons)
        self._paused = False
        self._state = self._with_probe(self._state, probe)
        return self._remember(self._decision(True, probe, self._stage, self._state, (self._event("resumed", self._actual_stage, probe, probe, self._state),)))

    def advance(self, runtime: CalibrationRuntimeContext) -> CalibrationDecision:
        if self._state is None:
            return self._last or self._terminal("incomplete", None, ("not_started",))
        if self._paused:
            return self._last or self._decision(True, 0.0, self._stage, self._state)
        state = self._state
        if runtime.now_s - state.stage_started_s >= self._config.stage_timeout_s:
            return self._terminal("stage_timeout", self._actual_stage, ("timeout",))
        reasons = self._guard_reasons(runtime)
        if reasons:
            return self._terminal("safety_aborted", self._actual_stage, reasons)
        if state.coasting:
            return self._resume_from_coast(runtime)
        realized_probe = runtime.realized_q - runtime.baseline_q
        state = _State(
            state.command, state.stage_index, state.stage_started_s, state.schedule_position + 1, state.signed_dwell_plan, state.schedule,
            state.eligible_observations + 1, state.realized_values + (runtime.realized_q,),
            state.positive_observations + int(realized_probe > 0.0), state.negative_observations + int(realized_probe < 0.0),
            state.realized_probe_sum + realized_probe, max(state.rank_progress, runtime.rank_progress), max(state.coverage_progress, runtime.coverage_progress),
            state.continuous and runtime.continuous, 0.0, state.clipping_error + realized_probe - state.current_probe_q,
            False, state.completion_history,
        )
        self._state = state
        if state.schedule_position >= len(state.schedule):
            if abs(state.clipping_error) > 1e-12:
                return self._issue_probe(-state.clipping_error, runtime, state)
            if self._ready(state):
                return self._complete_stage(runtime)
            return self._remember(self._decision(True, 0.0, self._stage, state, (self._event("incomplete", self._actual_stage, 0.0, 0.0, state, self._incomplete_reasons(state)),)))
        return self._issue_probe(state.schedule[state.schedule_position], runtime, state)

    def snapshot(self) -> Mapping[str, object]:
        return MappingProxyType({"config": self._config, "state": self._state, "last": self._last, "paused": self._paused, "dwell_counts": _DWELL_COUNTS, "signed_dwell_plan": () if self._state is None else self._state.signed_dwell_plan, "completed_stages": () if self._state is None else self._state.completion_history})

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, object], predict_max_c: Predictor | None = None) -> CalibrationCoordinator:
        config, state, last, paused = snapshot.get("config"), snapshot.get("state"), snapshot.get("last"), snapshot.get("paused", False)
        if not isinstance(config, CalibrationConfig) or (state is not None and not isinstance(state, _State)) or (last is not None and not isinstance(last, CalibrationDecision)) or not isinstance(paused, bool):
            raise ValueError("invalid calibration snapshot")
        coordinator = cls(config, predict_max_c)
        coordinator._state, coordinator._last, coordinator._paused = state, last, paused
        return coordinator

    @property
    def _actual_stage(self) -> str | None:
        return None if self._state is None else _STAGES[self._state.stage_index]

    @property
    def _stage(self) -> str | None:
        return _COAST if self._state is not None and self._state.coasting else self._actual_stage

    def _resume_from_coast(self, runtime: CalibrationRuntimeContext) -> CalibrationDecision:
        assert self._state is not None
        center = self._config.band_centers_c[self._state.stage_index]
        if abs(runtime.temp_c - center) > self._config.band_tolerance_c or abs(runtime.target_c - center) > self._config.band_tolerance_c:
            return self._remember(self._decision(True, 0.0, _COAST, self._state))
        probe, reasons = self._bound_probe(self._state.schedule[0], runtime)
        if reasons:
            return self._terminal("safety_aborted", self._actual_stage, reasons)
        state = _State(self._state.command, self._state.stage_index, self._state.stage_started_s, 0, self._state.signed_dwell_plan, self._state.schedule, current_probe_q=probe, completion_history=self._state.completion_history)
        self._state = state
        return self._remember(self._decision(True, probe, self._actual_stage, state, (self._event("stage_started", self._actual_stage, state.schedule[0], probe, state),)))

    def _issue_probe(self, intended: float, runtime: CalibrationRuntimeContext, state: _State) -> CalibrationDecision:
        probe, reasons = self._bound_probe(intended, runtime)
        if reasons:
            return self._terminal("safety_aborted", self._actual_stage, reasons)
        state = self._with_probe(state, probe)
        self._state = state
        return self._remember(self._decision(True, probe, self._stage, state, (self._event("probe_changed", self._actual_stage, intended, probe, state),)))

    def _complete_stage(self, runtime: CalibrationRuntimeContext) -> CalibrationDecision:
        assert self._state is not None
        completed, stage, progress = self._state, self._actual_stage, self._progress(self._state)
        event = self._event("stage_completed", stage, 0.0, 0.0, completed)
        if completed.stage_index == len(_STAGES) - 1:
            return self._terminal("completed", stage, (), (event,))
        state = _State(completed.command, completed.stage_index + 1, runtime.now_s, 0, completed.signed_dwell_plan, completed.schedule, completion_history=completed.completion_history + (progress,), coasting=True)
        self._state = state
        return self._remember(self._decision(True, 0.0, _COAST, state, (event,)))

    def _bound_probe(self, intended: float, runtime: CalibrationRuntimeContext) -> tuple[float, tuple[str, ...]]:
        if self._predict_max_c is None:
            return 0.0, ("prediction_unavailable",)
        magnitude = min(self._config.max_probe_q, runtime.allocator_headroom, runtime.error_rate_headroom, runtime.capability_headroom, runtime.saturation_headroom, runtime.baseline_q if intended < 0.0 else 1.0 - runtime.baseline_q)
        if magnitude <= 0.0:
            return 0.0, ("inadequate_headroom",)
        bounded = max(-magnitude, min(magnitude, intended))
        try:
            predicted = _finite(self._predict_max_c(runtime.baseline_q, bounded, runtime), "predicted_max_c")
        except (TypeError, ValueError, ArithmeticError):
            return 0.0, ("prediction_invalid",)
        assert self._state is not None
        if predicted >= self._state.command.maximum_temperature_c or predicted >= runtime.safety_ceiling_c:
            return 0.0, ("overshoot_prediction",)
        return bounded, ()

    def _guard_reasons(self, runtime: CalibrationRuntimeContext) -> tuple[str, ...]:
        reasons = []
        for field, reason in (("lid_open", "lid_open"), ("manual_mode", "manual_mode"), ("manual_output", "manual_output"), ("safety_inhibited", "safety_inhibited"), ("temperature_guard", "temperature_guard"), ("stale_result", "stale_result"), ("skipped_frame", "skipped_frame"), ("reset_frame", "reset_frame"), ("fallback", "fallback")):
            if getattr(runtime, field): reasons.append(reason)
        if not runtime.probe_valid: reasons.append("invalid_probe")
        if not runtime.continuous: reasons.append("discontinuity")
        if not runtime.actuation_known: reasons.append("unknown_actuation")
        if min(runtime.allocator_headroom, runtime.error_rate_headroom, runtime.capability_headroom, runtime.saturation_headroom) <= 0.0: reasons.append("inadequate_headroom")
        return tuple(reasons)

    def _ready(self, state: _State) -> bool:
        progress = self._progress(state)
        return progress.eligible_observations >= self._config.min_stage_observations and progress.realized_levels >= self._config.min_realized_levels and progress.realized_variance >= self._config.min_realized_variance and progress.positive_observations >= self._config.min_positive_observations and progress.negative_observations >= self._config.min_negative_observations and progress.rank_progress >= 1.0 and progress.coverage_progress >= 1.0 and progress.continuous and abs(progress.realized_probe_sum) <= self._config.max_probe_q

    def _progress(self, state: _State) -> CalibrationProgress:
        values = state.realized_values
        mean = sum(values) / len(values) if values else 0.0
        variance = sum((value - mean) ** 2 for value in values) / len(values) if values else 0.0
        return CalibrationProgress(state.eligible_observations, len({round(value, 12) for value in values}), variance, state.positive_observations, state.negative_observations, state.rank_progress, state.coverage_progress, state.continuous, state.realized_probe_sum, state.clipping_error)

    def _incomplete_reasons(self, state: _State) -> tuple[str, ...]:
        progress, reasons = self._progress(state), []
        if progress.eligible_observations < self._config.min_stage_observations: reasons.append("insufficient_observations")
        if progress.realized_levels < self._config.min_realized_levels: reasons.append("insufficient_levels")
        if progress.realized_variance < self._config.min_realized_variance: reasons.append("insufficient_variance")
        if progress.positive_observations < self._config.min_positive_observations: reasons.append("insufficient_positive")
        if progress.negative_observations < self._config.min_negative_observations: reasons.append("insufficient_negative")
        if progress.rank_progress < 1.0 or progress.coverage_progress < 1.0: reasons.append("rank_coverage")
        if abs(progress.realized_probe_sum) > self._config.max_probe_q: reasons.append("nonzero_mean")
        return tuple(reasons)

    def _with_probe(self, state: _State, probe: float) -> _State:
        return _State(state.command, state.stage_index, state.stage_started_s, state.schedule_position, state.signed_dwell_plan, state.schedule, state.eligible_observations, state.realized_values, state.positive_observations, state.negative_observations, state.realized_probe_sum, state.rank_progress, state.coverage_progress, state.continuous, probe, state.clipping_error, state.coasting, state.completion_history)

    def _decision(self, active: bool, probe: float, stage: str | None, state: _State, events: tuple[CalibrationEvent, ...] = ()) -> CalibrationDecision:
        return CalibrationDecision(active, probe, stage, self._progress(state), events, self._config.band_centers_c[state.stage_index])

    def _terminal(self, kind: str, stage: str | None, reasons: tuple[str, ...], prefix: tuple[CalibrationEvent, ...] = ()) -> CalibrationDecision:
        state = self._state
        progress = self._progress(state) if state is not None else CalibrationProgress()
        self._state, self._paused = None, False
        return self._remember(CalibrationDecision(False, 0.0, stage, progress, prefix + (CalibrationEvent(kind, stage, 0.0, 0.0, progress.realized_probe_sum, reasons),)))

    def _event(self, kind: str, stage: str | None, intended: float, bounded: float, state: _State, reasons: tuple[str, ...] = ()) -> CalibrationEvent:
        return CalibrationEvent(kind, stage, intended, bounded, state.realized_probe_sum, reasons)

    def _remember(self, decision: CalibrationDecision) -> CalibrationDecision:
        self._last = decision
        return decision

    @staticmethod
    def _signed_dwell_plan(seed: int) -> tuple[int, ...]:
        counts = _DWELL_COUNTS[seed % len(_DWELL_COUNTS):] + _DWELL_COUNTS[:seed % len(_DWELL_COUNTS)]
        sign = -1 if seed & 1 else 1
        return tuple(item for count in counts for item in (sign * count, -sign * count))

    @staticmethod
    def _expand(plan: tuple[int, ...]) -> tuple[int, ...]:
        frames: list[int] = []
        for index in range(0, len(plan), 2):
            for dwell in plan[index:index + 2]:
                frames.extend((1 if dwell > 0 else -1,) * abs(dwell))
            frames.append(0)
        return tuple(frames)
