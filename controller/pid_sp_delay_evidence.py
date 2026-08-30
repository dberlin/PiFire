from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal

import numpy as np

from .pid_sp_observation import PidSpDutySegment, PidSpInterval

DELAY_GRID_S = 5
INITIAL_DELAY_BOUND_S = 300
DELAY_BOUND_STEP_S = 150
MAX_DELAY_BOUND_S = 900
BASIN_LOSS_RATIO = 1.05
MAX_AUTHORIZED_WIDTH_S = 60
MIN_AUTHORIZED_EPISODES = 2

MIN_TRANSITION = 0.05
MIN_TRANSITION_HOLD = 60.0
EPISODE_RESPONSE_S = 1800.0
_PRE_ROLL_S = MAX_DELAY_BOUND_S + MIN_TRANSITION_HOLD


class DelayBlocker(StrEnum):
    INSUFFICIENT_EXCITATION_EPISODES = "insufficient-excitation-episodes"
    DELAY_BASIN_TOO_WIDE = "delay-basin-too-wide"
    DELAY_BASIN_EDGE = "delay-basin-edge"
    DELAY_RANGE_EXHAUSTED = "delay-range-exhausted"
    INSUFFICIENT_CONFIDENCE_EVIDENCE = "insufficient-confidence-evidence"
    NO_PHYSICALLY_VALID_CANDIDATE = "no-physically-valid-delay-candidate"


@dataclass(frozen=True, slots=True)
class EpisodeInputHistory:
    """Continuous realized-duty support preceding an episode's temperature rows."""

    duty_segments: tuple[PidSpDutySegment, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.duty_segments, tuple) or not self.duty_segments:
            raise ValueError("input history requires a nonempty duty-segment tuple")
        if not all(isinstance(segment, PidSpDutySegment) for segment in self.duty_segments):
            raise TypeError("input history must contain PidSpDutySegment values")
        if any(
            left.end_s != right.start_s
            for left, right in zip(
                self.duty_segments,
                self.duty_segments[1:],
            )
        ):
            raise ValueError("input history duty segments must be contiguous")


@dataclass(frozen=True, slots=True)
class ExcitationEpisode:
    episode_id: str
    intervals: tuple[PidSpInterval, ...]
    transition_at_s: float
    duty_before: float
    duty_after: float
    terminal_reason: str
    input_history: EpisodeInputHistory | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.episode_id, str) or not self.episode_id:
            raise ValueError("episode_id must be a nonempty string")
        if not isinstance(self.intervals, tuple) or not self.intervals:
            raise ValueError("intervals must be a nonempty tuple")
        if not all(isinstance(interval, PidSpInterval) for interval in self.intervals):
            raise TypeError("intervals must contain PidSpInterval values")
        transition = _finite_float(self.transition_at_s, "transition_at_s")
        before = _validated_duty(self.duty_before, "duty_before")
        after = _validated_duty(self.duty_after, "duty_after")
        if abs(after - before) < MIN_TRANSITION:
            raise ValueError("excitation transition must be at least 0.05 realized duty")
        if not isinstance(self.terminal_reason, str) or not self.terminal_reason:
            raise ValueError("terminal_reason must be a nonempty string")
        if self.input_history is not None:
            if not isinstance(self.input_history, EpisodeInputHistory):
                raise TypeError("input_history must be EpisodeInputHistory or None")
            if self.input_history.duty_segments[-1].end_s != self.intervals[0].start_s:
                raise ValueError("input history must end at the first episode interval")
        object.__setattr__(self, "transition_at_s", transition)
        object.__setattr__(self, "duty_before", before)
        object.__setattr__(self, "duty_after", after)


@dataclass(frozen=True, slots=True)
class DelayBasin:
    lower_s: int
    upper_s: int
    representative_s: int
    confidence_lower_s: int
    confidence_upper_s: int
    confidence_method: Literal["raw-basin", "provided", "moving-block-refit"]
    confidence_resamples: int
    episode_count: int
    interior: bool
    blockers: tuple[DelayBlocker, ...]

    def __post_init__(self) -> None:
        bounds = (
            ("lower_s", self.lower_s),
            ("upper_s", self.upper_s),
            ("representative_s", self.representative_s),
            ("confidence_lower_s", self.confidence_lower_s),
            ("confidence_upper_s", self.confidence_upper_s),
        )
        for name, value in bounds:
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be integer seconds")
            if not 0 <= value <= MAX_DELAY_BOUND_S or value % DELAY_GRID_S:
                raise ValueError(f"{name} must lie on the bounded delay grid")
        if not self.lower_s <= self.representative_s <= self.upper_s:
            raise ValueError("basin bounds and representative must be ordered")
        if self.confidence_lower_s > self.confidence_upper_s:
            raise ValueError("confidence bounds must be ordered")
        if self.confidence_method not in {"raw-basin", "provided", "moving-block-refit"}:
            raise ValueError("unsupported confidence method")
        if (
            isinstance(self.confidence_resamples, bool)
            or not isinstance(self.confidence_resamples, int)
            or self.confidence_resamples < 0
        ):
            raise ValueError("confidence_resamples must be a nonnegative integer")
        if self.confidence_method == "provided" and self.confidence_resamples != 0:
            raise ValueError("provided confidence must not claim resamples")
        if self.confidence_method == "moving-block-refit" and self.confidence_resamples == 0:
            raise ValueError("moving-block confidence must report successful resamples")
        if isinstance(self.episode_count, bool) or not isinstance(self.episode_count, int) or self.episode_count < 0:
            raise ValueError("episode_count must be a nonnegative integer")
        if not isinstance(self.interior, bool):
            raise TypeError("interior must be a bool")
        blockers = _validated_blockers(self.blockers)
        object.__setattr__(self, "blockers", blockers)
        envelope_lower_s = min(self.lower_s, self.confidence_lower_s)
        envelope_upper_s = max(self.upper_s, self.confidence_upper_s)
        touches_edge = DelayBlocker.DELAY_BASIN_EDGE in blockers or DelayBlocker.DELAY_RANGE_EXHAUSTED in blockers
        if self.interior == touches_edge:
            raise ValueError("interior must agree with edge blockers")
        if (envelope_upper_s - envelope_lower_s > MAX_AUTHORIZED_WIDTH_S) != (
            DelayBlocker.DELAY_BASIN_TOO_WIDE in blockers
        ):
            raise ValueError("confidence width must agree with basin blockers")
        confidence_insufficient = DelayBlocker.INSUFFICIENT_CONFIDENCE_EVIDENCE in blockers
        if (self.confidence_method == "raw-basin") != confidence_insufficient:
            raise ValueError("confidence method must agree with confidence blocker")
        excitation_insufficient = DelayBlocker.INSUFFICIENT_EXCITATION_EPISODES in blockers
        if (self.episode_count < MIN_AUTHORIZED_EPISODES) != excitation_insufficient:
            raise ValueError("episode count must agree with excitation blocker")

    @property
    def width_s(self) -> int:
        return self.upper_s - self.lower_s


@dataclass(frozen=True, slots=True)
class DelayProfile:
    model_form: str
    evaluated_bound_s: int
    candidate_losses: tuple[tuple[int, float], ...]
    episode_ids: tuple[str, ...]
    basin: DelayBasin | None
    next_evaluated_bound_s: int | None
    blockers: tuple[DelayBlocker, ...]
    authorized: bool

    def __post_init__(self) -> None:
        if not isinstance(self.model_form, str) or not self.model_form:
            raise ValueError("model_form must be a nonempty string")
        bound = _validated_bound(self.evaluated_bound_s)
        if not isinstance(self.candidate_losses, tuple) or not self.candidate_losses:
            raise ValueError("candidate_losses must be a nonempty tuple")
        previous_delay = -DELAY_GRID_S
        for candidate in self.candidate_losses:
            if not isinstance(candidate, tuple) or len(candidate) != 2:
                raise TypeError("candidate losses must contain (delay_s, loss) tuples")
            delay_s, loss = candidate
            if (
                isinstance(delay_s, bool)
                or not isinstance(delay_s, int)
                or delay_s % DELAY_GRID_S
                or not previous_delay < delay_s <= bound
            ):
                raise ValueError("candidate delays must be ordered within the evaluated bound")
            if _finite_float(loss, "candidate loss") < 0.0:
                raise ValueError("candidate losses must be finite and nonnegative")
            previous_delay = delay_s
        episode_ids = _distinct_episode_ids(self.episode_ids)
        if episode_ids != self.episode_ids:
            raise ValueError("episode_ids must be distinct")
        blockers = _validated_blockers(self.blockers)
        object.__setattr__(self, "blockers", blockers)
        if not isinstance(self.authorized, bool):
            raise TypeError("authorized must be a bool")
        if self.authorized != (not blockers):
            raise ValueError("authorized must be true exactly when blockers are empty")
        if self.next_evaluated_bound_s is not None and (self.next_evaluated_bound_s > MAX_DELAY_BOUND_S):
            raise ValueError("next evaluated bound exceeds the hard delay bound")
        unavailable = DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE
        if self.basin is None:
            if blockers != (unavailable,):
                raise ValueError("a basin-less profile requires exactly the no-physically-valid-candidate blocker")
            if self.next_evaluated_bound_s is not None:
                raise ValueError("a basin-less profile cannot expand its delay bound")
            expected_delays = tuple(range(0, bound + 1, DELAY_GRID_S))
            if tuple(delay_s for delay_s, _ in self.candidate_losses) != expected_delays:
                raise ValueError("a basin-less profile must retain the full evaluated delay grid")
            if any(loss != 1e300 for _, loss in self.candidate_losses):
                raise ValueError("a basin-less profile must retain exact physically invalid losses")
            return
        if not isinstance(self.basin, DelayBasin):
            raise TypeError("basin must be a DelayBasin or null")
        if unavailable in blockers:
            raise ValueError("the no-physically-valid-candidate blocker requires a null basin")
        if blockers != self.basin.blockers:
            raise ValueError("profile and basin blockers must agree")
        if self.basin.episode_count != len(episode_ids):
            raise ValueError("profile and basin episode counts must agree")
        if (
            self.basin.lower_s > bound
            or self.basin.upper_s > bound
            or self.basin.confidence_lower_s > bound
            or self.basin.confidence_upper_s > bound
        ):
            raise ValueError("basin bounds must lie within the evaluated bound")
        envelope_lower_s = min(
            self.basin.lower_s,
            self.basin.confidence_lower_s,
        )
        envelope_upper_s = max(
            self.basin.upper_s,
            self.basin.confidence_upper_s,
        )
        edge_blocked = DelayBlocker.DELAY_BASIN_EDGE in blockers
        exhausted = DelayBlocker.DELAY_RANGE_EXHAUSTED in blockers
        if edge_blocked and exhausted:
            raise ValueError("delay edge and range exhaustion are mutually exclusive")
        if exhausted != (bound == MAX_DELAY_BOUND_S and envelope_upper_s == MAX_DELAY_BOUND_S):
            raise ValueError("range exhaustion must agree with the evaluated bound")
        if edge_blocked != (not exhausted and (envelope_lower_s == 0 or envelope_upper_s == bound)):
            raise ValueError("edge blocker must agree with basin bounds")
        expected_next_bound = bound + DELAY_BOUND_STEP_S if edge_blocked and envelope_upper_s == bound else None
        if self.next_evaluated_bound_s != expected_next_bound:
            raise ValueError("next evaluated bound must be the upper-edge expansion")


@dataclass(frozen=True, slots=True)
class _ConfidenceEvidence:
    lower_s: int
    upper_s: int
    method: Literal["raw-basin", "moving-block-refit"]
    resamples: int
    sufficient: bool


class EpisodeAccumulator:
    """Own sustained duty transitions and freeze bounded completed evidence."""

    def __init__(self, max_completed_episodes: int = 32) -> None:
        if (
            isinstance(max_completed_episodes, bool)
            or not isinstance(max_completed_episodes, int)
            or max_completed_episodes <= 0
        ):
            raise ValueError("max_completed_episodes must be a positive integer")
        self._max_completed_episodes = max_completed_episodes
        self._completed: list[ExcitationEpisode] = []
        self._history: list[PidSpInterval] = []
        self._active: list[PidSpInterval] | None = None
        self._active_id: str | None = None
        self._transition_at_s: float | None = None
        self._duty_before: float | None = None
        self._duty_after: float | None = None
        self._stable_duty: float | None = None
        self._pending_duty: float | None = None
        self._pending_since_s: float | None = None
        self._pending_intervals: list[PidSpInterval] = []

    def observe(self, interval: PidSpInterval) -> ExcitationEpisode | None:
        if not isinstance(interval, PidSpInterval):
            raise TypeError("interval must be a PidSpInterval")
        if not interval.continuous or (self._history and interval.start_s != self._history[-1].end_s):
            self._flush_pending_into_active()
            completed = self._close("discontinuity")
            self._stable_duty = interval.realized_duty
            self._clear_pending()
            self._history = [interval]
            return completed

        self._history.append(interval)
        if self._active is not None:
            completed = self._observe_active_transition(interval)
            if completed is not None:
                return completed
            assert self._transition_at_s is not None
            if interval.end_s - self._transition_at_s >= EPISODE_RESPONSE_S:
                self._flush_pending_into_active()
                return self._close("response-window-complete")
            return None

        if self._stable_duty is None:
            self._stable_duty = interval.realized_duty
            self._prune_history(interval.end_s)
            return None

        if abs(interval.realized_duty - self._stable_duty) < MIN_TRANSITION:
            self._clear_pending()
            self._stable_duty = interval.realized_duty
            self._prune_history(interval.end_s)
            return None

        if self._pending_duty is None or abs(interval.realized_duty - self._pending_duty) >= MIN_TRANSITION:
            self._pending_duty = interval.realized_duty
            self._pending_since_s = interval.start_s
            self._prune_history(interval.end_s)
            return None

        assert self._pending_since_s is not None
        if interval.end_s - self._pending_since_s >= MIN_TRANSITION_HOLD:
            self._open(interval)
        return None

    def completed(self) -> tuple[ExcitationEpisode, ...]:
        return tuple(self._completed)

    def interrupt(self, terminal_reason: str) -> ExcitationEpisode | None:
        if not isinstance(terminal_reason, str) or not terminal_reason:
            raise ValueError("terminal_reason must be a nonempty string")
        self._flush_pending_into_active()
        completed = self._close(terminal_reason)
        self._history = []
        self._stable_duty = None
        self._clear_pending()
        return completed

    def _open(self, interval: PidSpInterval) -> None:
        assert self._stable_duty is not None
        assert self._pending_duty is not None
        assert self._pending_since_s is not None
        self._active = list(self._history)
        self._active_id = f"episode-{interval.role_generation}-{interval.observation_sequence}"
        self._transition_at_s = self._pending_since_s
        self._duty_before = self._stable_duty
        self._duty_after = self._pending_duty
        self._stable_duty = self._pending_duty
        self._clear_pending()

    def _observe_active_transition(self, interval: PidSpInterval) -> ExcitationEpisode | None:
        assert self._active is not None
        assert self._duty_after is not None
        if abs(interval.realized_duty - self._duty_after) < MIN_TRANSITION:
            self._flush_pending_into_active()
            self._active.append(interval)
            self._clear_pending()
            return None
        if self._pending_duty is None or abs(interval.realized_duty - self._pending_duty) >= MIN_TRANSITION:
            self._flush_pending_into_active()
            self._pending_duty = interval.realized_duty
            self._pending_since_s = interval.start_s
            self._pending_intervals = [interval]
            return None
        self._pending_intervals.append(interval)
        assert self._pending_since_s is not None
        if interval.end_s - self._pending_since_s < MIN_TRANSITION_HOLD:
            return None

        transition_at_s = self._pending_since_s
        duty_before = self._duty_after
        duty_after = self._pending_duty
        pending = tuple(self._pending_intervals)
        completed = self._close("new-sustained-transition")
        pre_roll_cutoff = transition_at_s - _PRE_ROLL_S
        pre_roll = [retained for retained in self._history if pre_roll_cutoff <= retained.end_s <= transition_at_s]
        self._active = [*pre_roll, *pending]
        self._active_id = f"episode-{interval.role_generation}-{interval.observation_sequence}"
        self._transition_at_s = transition_at_s
        self._duty_before = duty_before
        self._duty_after = duty_after
        self._stable_duty = duty_after
        self._clear_pending()
        return completed

    def _flush_pending_into_active(self) -> None:
        if self._active is not None and self._pending_intervals:
            self._active.extend(self._pending_intervals)

    def _clear_pending(self) -> None:
        self._pending_duty = None
        self._pending_since_s = None
        self._pending_intervals = []

    def _close(self, terminal_reason: str) -> ExcitationEpisode | None:
        if self._active is None:
            return None
        assert self._active_id is not None
        assert self._transition_at_s is not None
        assert self._duty_before is not None
        assert self._duty_after is not None
        episode = ExcitationEpisode(
            episode_id=self._active_id,
            intervals=tuple(self._active),
            transition_at_s=self._transition_at_s,
            duty_before=self._duty_before,
            duty_after=self._duty_after,
            terminal_reason=terminal_reason,
        )
        self._completed.append(episode)
        if len(self._completed) > self._max_completed_episodes:
            del self._completed[: len(self._completed) - self._max_completed_episodes]
        self._stable_duty = self._active[-1].realized_duty
        self._active = None
        self._active_id = None
        self._transition_at_s = None
        self._duty_before = None
        self._duty_after = None
        self._clear_pending()
        return episode

    def _prune_history(self, now_s: float) -> None:
        cutoff = now_s - _PRE_ROLL_S
        first = 0
        while first < len(self._history) and self._history[first].end_s < cutoff:
            first += 1
        if first:
            del self._history[:first]


def select_delay_basin(
    candidate_losses: Sequence[tuple[int, float]],
    *,
    episode_ids: tuple[str, ...],
    evaluated_bound_s: int,
    confidence_interval_s: tuple[int, int] | None = None,
) -> DelayProfile:
    bound = _validated_bound(evaluated_bound_s)
    losses = _validated_losses(candidate_losses, bound)
    distinct_episode_ids = _distinct_episode_ids(episode_ids)

    best_index = min(range(len(losses)), key=lambda index: (losses[index][1], losses[index][0]))
    threshold = losses[best_index][1] * BASIN_LOSS_RATIO
    accepted = tuple(loss <= threshold for _, loss in losses)
    lower_index = best_index
    while lower_index and accepted[lower_index - 1]:
        lower_index -= 1
    upper_index = best_index
    while upper_index + 1 < len(losses) and accepted[upper_index + 1]:
        upper_index += 1

    raw_lower_s = losses[lower_index][0]
    raw_upper_s = losses[upper_index][0]
    confidence_lower_s, confidence_upper_s = _validated_confidence_interval(
        confidence_interval_s,
        bound,
        default=(raw_lower_s, raw_upper_s),
    )
    envelope_lower_s = min(raw_lower_s, confidence_lower_s)
    envelope_upper_s = max(raw_upper_s, confidence_upper_s)
    representative_s = losses[best_index][0]
    interior = envelope_lower_s > 0 and envelope_upper_s < bound
    blockers: list[DelayBlocker] = []
    next_bound: int | None = None
    if envelope_upper_s == bound:
        if bound == MAX_DELAY_BOUND_S:
            blockers.append(DelayBlocker.DELAY_RANGE_EXHAUSTED)
        else:
            blockers.append(DelayBlocker.DELAY_BASIN_EDGE)
            next_bound = bound + DELAY_BOUND_STEP_S
    elif envelope_lower_s == 0:
        blockers.append(DelayBlocker.DELAY_BASIN_EDGE)
    if envelope_upper_s - envelope_lower_s > MAX_AUTHORIZED_WIDTH_S:
        blockers.append(DelayBlocker.DELAY_BASIN_TOO_WIDE)
    if confidence_interval_s is None:
        blockers.append(DelayBlocker.INSUFFICIENT_CONFIDENCE_EVIDENCE)
    if len(distinct_episode_ids) < MIN_AUTHORIZED_EPISODES:
        blockers.append(DelayBlocker.INSUFFICIENT_EXCITATION_EPISODES)

    blocker_tuple = tuple(blockers)
    basin = DelayBasin(
        lower_s=raw_lower_s,
        upper_s=raw_upper_s,
        representative_s=representative_s,
        confidence_lower_s=confidence_lower_s,
        confidence_upper_s=confidence_upper_s,
        confidence_method="raw-basin" if confidence_interval_s is None else "provided",
        confidence_resamples=0,
        episode_count=len(distinct_episode_ids),
        interior=interior,
        blockers=blocker_tuple,
    )
    return DelayProfile(
        model_form="synthetic",
        evaluated_bound_s=bound,
        candidate_losses=losses,
        episode_ids=distinct_episode_ids,
        basin=basin,
        next_evaluated_bound_s=next_bound,
        blockers=blocker_tuple,
        authorized=not blocker_tuple,
    )


def profile_delays(
    episodes: tuple[ExcitationEpisode, ...],
    model_form: str,
    max_delay_s: int,
) -> DelayProfile:
    if not isinstance(episodes, tuple) or not episodes:
        raise ValueError("episodes must be a nonempty tuple")
    if not all(isinstance(episode, ExcitationEpisode) for episode in episodes):
        raise TypeError("episodes must contain ExcitationEpisode values")
    if model_form not in {"ipdt", "fopdt"}:
        raise ValueError("model_form must be 'ipdt' or 'fopdt'")
    bound = _validated_bound(max_delay_s)
    episode_ids = _distinct_episode_ids(tuple(episode.episode_id for episode in episodes))
    prepared = tuple(_prepare_episode(episode, bound) for episode in episodes)
    if not all(rows for rows in prepared):
        raise ValueError("each episode must contain consecutive temperature observations")

    losses: list[tuple[int, float]] = []
    candidate_designs: list[tuple[int, tuple[tuple[np.ndarray, np.ndarray], ...]]] = []
    invalid_loss = 1e300
    for delay_s in range(0, bound + 1, DELAY_GRID_S):
        designs = tuple(_design(rows, delay_s, model_form) for rows in prepared)
        loss = invalid_loss
        if all(design is not None for design in designs):
            complete_designs = tuple(design for design in designs if design is not None)
            candidate_loss = _candidate_validation_loss(
                complete_designs,
                model_form,
            )
            if math.isfinite(candidate_loss) and candidate_loss >= 0.0:
                loss = candidate_loss
                candidate_designs.append((delay_s, complete_designs))
        losses.append((delay_s, loss))
    if all(loss == invalid_loss for _, loss in losses):
        return DelayProfile(
            model_form=model_form,
            evaluated_bound_s=bound,
            candidate_losses=tuple(losses),
            episode_ids=episode_ids,
            basin=None,
            next_evaluated_bound_s=None,
            blockers=(DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE,),
            authorized=False,
        )

    raw_selected = select_delay_basin(
        tuple(losses),
        episode_ids=episode_ids,
        evaluated_bound_s=bound,
    )
    raw_basin = raw_selected.basin
    assert raw_basin is not None
    confidence_evidence = _confidence_bounds(
        raw_basin,
        tuple(candidate_designs),
        prepared,
        model_form,
    )
    selected = select_delay_basin(
        tuple(losses),
        episode_ids=episode_ids,
        evaluated_bound_s=bound,
        confidence_interval_s=(
            (confidence_evidence.lower_s, confidence_evidence.upper_s) if confidence_evidence.sufficient else None
        ),
    )
    selected_basin = selected.basin
    assert selected_basin is not None
    audited_basin = replace(
        selected_basin,
        confidence_method=confidence_evidence.method,
        confidence_resamples=confidence_evidence.resamples,
    )
    return replace(selected, model_form=model_form, basin=audited_basin)


@dataclass(frozen=True, slots=True)
class _TemperatureRow:
    start_s: float
    end_s: float
    previous_temperature_f: float
    terminal_temperature_f: float
    ledger: _DutyLedger


def _prepare_episode(
    episode: ExcitationEpisode,
    evaluated_bound_s: int,
) -> tuple[_TemperatureRow, ...]:
    ledger = _DutyLedger(episode)
    rows: list[_TemperatureRow] = []
    previous: PidSpInterval | None = None
    for interval in episode.intervals:
        if previous is not None and (
            interval.continuous
            and previous.continuous
            and interval.start_s == previous.end_s
            and ledger.covers(
                previous.end_s - evaluated_bound_s,
                interval.end_s - evaluated_bound_s,
            )
        ):
            rows.append(
                _TemperatureRow(
                    start_s=previous.end_s,
                    end_s=interval.end_s,
                    previous_temperature_f=previous.temperature_f,
                    terminal_temperature_f=interval.temperature_f,
                    ledger=ledger,
                )
            )
        previous = interval
    return tuple(rows)


def _candidate_validation_loss(
    designs: tuple[tuple[np.ndarray, np.ndarray], ...],
    model_form: str,
) -> float:
    if len(designs) == 1:
        x, y = designs[0]
        coefficients = _fit_physical(x, y, model_form)
        if coefficients is None:
            return math.inf
        residuals = y - x @ coefficients
        return float(np.mean(residuals * residuals))

    fold_losses: list[float] = []
    for held_out in range(1, len(designs)):
        train_x = np.concatenate([designs[index][0] for index in range(held_out)], axis=0)
        train_y = np.concatenate([designs[index][1] for index in range(held_out)], axis=0)
        coefficients = _fit_physical(train_x, train_y, model_form)
        if coefficients is None:
            return math.inf
        validation_x, validation_y = designs[held_out]
        residuals = validation_y - validation_x @ coefficients
        fold_losses.append(float(np.mean(residuals * residuals)))
    return float(np.mean(fold_losses))


def _design(
    rows: tuple[_TemperatureRow, ...],
    delay_s: int,
    model_form: str,
) -> tuple[np.ndarray, np.ndarray] | None:
    features: list[tuple[float, ...]] = []
    targets: list[float] = []
    for row in rows:
        average_duty = row.ledger.average(row.start_s - delay_s, row.end_s - delay_s)
        if average_duty is None:
            return None
        rate = (row.terminal_temperature_f - row.previous_temperature_f) / (row.end_s - row.start_s)
        if model_form == "ipdt":
            features.append((1.0, average_duty))
        else:
            features.append((1.0, row.previous_temperature_f, average_duty))
        targets.append(rate)
    parameter_count = 2 if model_form == "ipdt" else 3
    if len(features) <= parameter_count:
        return None
    return np.asarray(features, dtype=float), np.asarray(targets, dtype=float)


def _fit_physical(x: np.ndarray, y: np.ndarray, model_form: str) -> np.ndarray | None:
    coefficients, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
    if rank != x.shape[1] or not np.isfinite(coefficients).all():
        return None
    if model_form == "ipdt":
        c0, gain_rate = coefficients
        if not (0.05 <= gain_rate <= 5.0 and c0 <= 0.0):
            return None
        return coefficients
    c0, temperature_coefficient, duty_coefficient = coefficients
    if temperature_coefficient >= 0.0 or duty_coefficient <= 0.0:
        return None
    tau = -1.0 / temperature_coefficient
    gain = -duty_coefficient / temperature_coefficient
    if not (300.0 <= tau <= 20000.0 and 50.0 <= gain <= 2000.0 and math.isfinite(c0)):
        return None
    return coefficients


def _sample_episode_rows(
    *,
    row_count: int,
    block_length: int,
    generator: np.random.Generator,
) -> np.ndarray:
    if row_count <= 0 or block_length <= 0:
        raise ValueError("row_count and block_length must be positive")
    retained_block_length = min(row_count, block_length)
    blocks_needed = math.ceil(row_count / retained_block_length)
    starts = np.arange(row_count - retained_block_length + 1)
    sampled_starts = generator.choice(starts, size=blocks_needed, replace=True)
    return np.concatenate([np.arange(int(start), int(start) + retained_block_length) for start in sampled_starts])[
        :row_count
    ]


def _refit_score_held_out(
    training_x: np.ndarray,
    training_y: np.ndarray,
    held_out_x: np.ndarray,
    held_out_y: np.ndarray,
    sampled_rows: np.ndarray,
    model_form: str,
) -> float:
    coefficients = _fit_physical(
        training_x[sampled_rows],
        training_y[sampled_rows],
        model_form,
    )
    if coefficients is None:
        return math.inf
    residuals = held_out_y - held_out_x @ coefficients
    return float(np.mean(residuals * residuals))


def _confidence_fallback(
    basin: DelayBasin,
    *,
    successful_resamples: int = 0,
) -> _ConfidenceEvidence:
    return _ConfidenceEvidence(
        lower_s=basin.lower_s,
        upper_s=basin.upper_s,
        method="raw-basin",
        resamples=successful_resamples,
        sufficient=False,
    )


def _confidence_bounds(
    basin: DelayBasin,
    candidate_designs: tuple[
        tuple[int, tuple[tuple[np.ndarray, np.ndarray], ...]],
        ...,
    ],
    prepared: tuple[tuple[_TemperatureRow, ...], ...],
    model_form: str,
) -> _ConfidenceEvidence:
    episode_count = len(prepared)
    expected_row_counts = tuple(len(rows) for rows in prepared)
    comparable = tuple(
        (delay_s, designs)
        for delay_s, designs in candidate_designs
        if len(designs) == episode_count
        and tuple(len(y) for _, y in designs) == expected_row_counts
        and all(np.isfinite(x).all() and np.isfinite(y).all() for x, y in designs)
    )
    if len(comparable) < 2 or not expected_row_counts or min(expected_row_counts) < 4:
        return _confidence_fallback(basin)

    observation_durations = tuple(row.end_s - row.start_s for episode_rows in prepared for row in episode_rows)
    median_observation_s = float(np.median(observation_durations))
    block_length = max(1, math.ceil(MAX_DELAY_BOUND_S / median_observation_s))
    generator = np.random.default_rng(0)
    representatives: list[int] = []
    single_episode = episode_count == 1
    for _ in range(500):
        if single_episode:
            sampling_plan: object = _sample_episode_rows(
                row_count=expected_row_counts[0],
                block_length=block_length,
                generator=generator,
            )
        else:
            fold_plans: list[tuple[np.ndarray, ...]] = []
            for held_out in range(1, episode_count):
                fold_plans.append(
                    tuple(
                        _sample_episode_rows(
                            row_count=expected_row_counts[training_episode],
                            block_length=block_length,
                            generator=generator,
                        )
                        for training_episode in range(held_out)
                    )
                )
            sampling_plan = tuple(fold_plans)

        sampled_losses: list[float] = []
        for _, designs in comparable:
            if single_episode:
                sampled_rows = sampling_plan
                x, y = designs[0]
                sampled_x = x[sampled_rows]
                sampled_y = y[sampled_rows]
                coefficients = _fit_physical(sampled_x, sampled_y, model_form)
                if coefficients is None:
                    sampled_losses.append(math.inf)
                else:
                    residuals = sampled_y - sampled_x @ coefficients
                    sampled_losses.append(float(np.mean(residuals * residuals)))
                continue

            fold_losses: list[float] = []
            for held_out, fold_plan in enumerate(sampling_plan, start=1):
                training_x = np.concatenate(
                    [designs[index][0] for index in range(held_out)],
                    axis=0,
                )
                training_y = np.concatenate(
                    [designs[index][1] for index in range(held_out)],
                    axis=0,
                )

                offsets = np.cumsum([0, *(len(designs[index][1]) for index in range(held_out - 1))])
                sampled_rows = np.concatenate(
                    [local_rows + offsets[index] for index, local_rows in enumerate(fold_plan)]
                )
                held_out_x, held_out_y = designs[held_out]
                fold_losses.append(
                    _refit_score_held_out(
                        training_x,
                        training_y,
                        held_out_x,
                        held_out_y,
                        sampled_rows,
                        model_form,
                    )
                )
            sampled_losses.append(float(np.mean(fold_losses)))

        best_index = min(
            range(len(sampled_losses)),
            key=lambda index: sampled_losses[index],
        )
        if math.isfinite(sampled_losses[best_index]):
            representatives.append(comparable[best_index][0])

    if len(representatives) != 500:
        return _confidence_fallback(
            basin,
            successful_resamples=len(representatives),
        )
    return _ConfidenceEvidence(
        lower_s=int(np.percentile(representatives, 5, method="nearest")),
        upper_s=int(np.percentile(representatives, 95, method="nearest")),
        method="moving-block-refit",
        resamples=500,
        sufficient=True,
    )


def _validated_blockers(
    blockers: tuple[DelayBlocker, ...],
) -> tuple[DelayBlocker, ...]:
    if not isinstance(blockers, tuple):
        raise TypeError("blockers must be a tuple")
    if not all(isinstance(blocker, DelayBlocker) for blocker in blockers):
        raise TypeError("blockers must contain DelayBlocker values")
    if len(set(blockers)) != len(blockers):
        raise ValueError("blockers must be unique")
    return blockers


class _DutyLedger:
    __slots__ = ("_components", "_cumulative", "_duties", "_ends", "_starts")

    def __init__(self, episode: ExcitationEpisode) -> None:
        starts: list[float] = []
        ends: list[float] = []
        duties: list[float] = []
        cumulative: list[float] = []
        components: list[int] = []
        total = 0.0
        component = 0
        previous_end: float | None = None
        history = () if episode.input_history is None else episode.input_history.duty_segments
        segments = (
            *((segment, True) for segment in history),
            *(
                (segment, interval.continuous)
                for interval in episode.intervals
                for segment in interval.duty_segments or ()
            ),
        )
        for segment, continuous in segments:
            if previous_end is not None and (segment.start_s != previous_end or not continuous):
                component += 1
            starts.append(segment.start_s)
            ends.append(segment.end_s)
            duties.append(segment.realized_duty)
            cumulative.append(total)
            components.append(component)
            total += (segment.end_s - segment.start_s) * segment.realized_duty
            previous_end = segment.end_s
        self._starts = tuple(starts)
        self._ends = tuple(ends)
        self._duties = tuple(duties)
        self._cumulative = tuple(cumulative)
        self._components = tuple(components)

    def covers(self, start_s: float, end_s: float) -> bool:
        if end_s <= start_s or not self._starts:
            return False
        first = bisect.bisect_right(self._starts, start_s) - 1
        last = bisect.bisect_left(self._ends, end_s)
        return (
            first >= 0
            and last < len(self._ends)
            and self._starts[first] <= start_s < self._ends[first]
            and self._starts[last] < end_s <= self._ends[last]
            and self._components[first] == self._components[last]
        )

    def average(self, start_s: float, end_s: float) -> float | None:
        if not self.covers(start_s, end_s):
            return None
        return (self._integral(end_s) - self._integral(start_s)) / (end_s - start_s)

    def _integral(self, time_s: float) -> float:
        index = bisect.bisect_right(self._starts, time_s) - 1
        index = min(max(index, 0), len(self._starts) - 1)
        elapsed = min(
            max(time_s - self._starts[index], 0.0),
            self._ends[index] - self._starts[index],
        )
        return self._cumulative[index] + self._duties[index] * elapsed


def _validated_bound(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("evaluated bound must be an integer number of seconds")
    if not INITIAL_DELAY_BOUND_S <= value <= MAX_DELAY_BOUND_S:
        raise ValueError("evaluated bound must be between 300 and 900 seconds")
    if (value - INITIAL_DELAY_BOUND_S) % DELAY_BOUND_STEP_S:
        raise ValueError("evaluated bound must increase from 300 in 150-second steps")
    return value


def _validated_confidence_interval(
    value: tuple[int, int] | None,
    bound_s: int,
    *,
    default: tuple[int, int],
) -> tuple[int, int]:
    if value is None:
        return default
    if not isinstance(value, tuple) or len(value) != 2:
        raise TypeError("confidence_interval_s must be a (lower_s, upper_s) tuple")
    lower_s, upper_s = value
    if (
        isinstance(lower_s, bool)
        or isinstance(upper_s, bool)
        or not isinstance(lower_s, int)
        or not isinstance(upper_s, int)
    ):
        raise TypeError("confidence bounds must be integer seconds")
    if not 0 <= lower_s <= upper_s <= bound_s:
        raise ValueError("confidence bounds must be ordered within the evaluated range")
    if lower_s % DELAY_GRID_S or upper_s % DELAY_GRID_S:
        raise ValueError("confidence bounds must lie on the 5-second delay grid")
    return lower_s, upper_s


def _validated_losses(
    candidate_losses: Sequence[tuple[int, float]],
    bound_s: int,
) -> tuple[tuple[int, float], ...]:
    expected_delays = tuple(range(0, bound_s + 1, DELAY_GRID_S))
    if len(candidate_losses) != len(expected_delays):
        raise ValueError("candidate losses must contain the complete evaluated delay grid")
    losses: list[tuple[int, float]] = []
    for expected_delay, candidate in zip(expected_delays, candidate_losses):
        if not isinstance(candidate, tuple) or len(candidate) != 2:
            raise TypeError("each candidate loss must be a (delay_s, loss) tuple")
        delay_s, loss_value = candidate
        if isinstance(delay_s, bool) or not isinstance(delay_s, int) or delay_s != expected_delay:
            raise ValueError("candidate losses must be ordered on the complete 5-second grid")
        loss = _finite_float(loss_value, "candidate loss")
        if loss < 0.0:
            raise ValueError("candidate losses must be nonnegative")
        losses.append((delay_s, loss))
    return tuple(losses)


def _distinct_episode_ids(episode_ids: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(episode_ids, tuple):
        raise TypeError("episode_ids must be a tuple")
    distinct: list[str] = []
    seen: set[str] = set()
    for episode_id in episode_ids:
        if not isinstance(episode_id, str) or not episode_id:
            raise ValueError("episode IDs must be nonempty strings")
        if episode_id not in seen:
            distinct.append(episode_id)
            seen.add(episode_id)
    return tuple(distinct)


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _validated_duty(value: object, name: str) -> float:
    result = _finite_float(value, name)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return result
