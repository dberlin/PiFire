"""Horizon-safe free-run prediction evaluation for model bake-off arms."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

import numpy as np
import numpy.typing as npt

from .contracts import FloatArray, SignalRecord
from .data import validate_record


class FreeRunModel(Protocol):
    """The minimal forecasting interface evaluated by the bake-off."""

    def forecast(
        self,
        record_prefix: SignalRecord,
        q_future: FloatArray,
        ambient_future: FloatArray,
    ) -> npt.ArrayLike:
        """Predict future chamber temperatures from history and known exogenous input."""
        ...


@dataclass(frozen=True, slots=True)
class HorizonScore:
    """Immutable aggregate error measures for one free-run horizon."""

    available: bool
    origins: tuple[int, ...]
    rmse_c: float | None
    max_abs_c: float | None
    bias_c: float | None
    p90_abs_c: float | None


def _normalized_horizons(horizons_s: Iterable[int]) -> tuple[int, ...]:
    horizons = tuple(horizons_s)
    if len(set(horizons)) != len(horizons):
        raise ValueError("horizons_s must not contain duplicates")
    if any(not isinstance(horizon, int) or isinstance(horizon, bool) for horizon in horizons):
        raise TypeError("horizons_s must contain integer seconds")
    if any(horizon <= 0 for horizon in horizons):
        raise ValueError("horizons_s must contain positive seconds")
    return horizons


def _horizon_targets(record: SignalRecord, horizon_s: int) -> tuple[tuple[int, int], ...]:
    """Pair each eligible origin with the first sample at its target time."""
    targets: list[tuple[int, int]] = []
    for origin, origin_time_s in enumerate(record.time_s):
        target = int(np.searchsorted(record.time_s, origin_time_s + horizon_s))
        if target < record.time_s.size:
            targets.append((origin, target))
    return tuple(targets)


def prediction_origins(record: SignalRecord, horizons_s: Iterable[int]) -> Mapping[int, tuple[int, ...]]:
    """Return every origin whose record tail reaches each requested horizon."""
    validate_record(record)
    availability = {
        horizon_s: tuple(origin for origin, _ in _horizon_targets(record, horizon_s))
        for horizon_s in _normalized_horizons(horizons_s)
    }
    return MappingProxyType(availability)


def _record_prefix(record: SignalRecord, origin: int) -> SignalRecord:
    """Make the model-visible history ending at an allowed prediction origin."""
    end = origin + 1
    return SignalRecord(
        time_s=record.time_s[:end],
        temp_c=record.temp_c[:end],
        q=record.q[:end],
        ambient_c=record.ambient_c[:end],
        provenance=record.provenance,
        metadata=dict(record.metadata),
    )


def _unavailable_score() -> HorizonScore:
    return HorizonScore(
        available=False,
        origins=(),
        rmse_c=None,
        max_abs_c=None,
        bias_c=None,
        p90_abs_c=None,
    )


def score_free_run(
    model: FreeRunModel,
    record: SignalRecord,
    horizons_s: Sequence[int],
) -> Mapping[int, HorizonScore]:
    """Score open-loop forecasts without providing models future temperatures."""
    validate_record(record)
    scores: dict[int, HorizonScore] = {}
    for horizon_s in _normalized_horizons(horizons_s):
        targets = _horizon_targets(record, horizon_s)
        if not targets:
            scores[horizon_s] = _unavailable_score()
            continue

        errors: list[FloatArray] = []
        origins: list[int] = []
        for origin, target in targets:
            forecast = np.asarray(
                model.forecast(
                    _record_prefix(record, origin),
                    record.q[origin + 1 : target + 1],
                    record.ambient_c[origin + 1 : target + 1],
                ),
                dtype=np.float64,
            )
            truth = record.temp_c[origin + 1 : target + 1]
            if forecast.shape != truth.shape:
                raise ValueError("forecast must return one temperature for each future input frame")
            if not np.isfinite(forecast).all():
                raise ValueError("forecast must contain only finite temperatures")
            errors.append(forecast - truth)
            origins.append(origin)

        error = np.concatenate(errors)
        absolute_error = np.abs(error)
        scores[horizon_s] = HorizonScore(
            available=True,
            origins=tuple(origins),
            rmse_c=float(np.sqrt(np.mean(np.square(error)))),
            max_abs_c=float(np.max(absolute_error)),
            bias_c=float(np.mean(error)),
            p90_abs_c=float(np.quantile(absolute_error, 0.9)),
        )
    return MappingProxyType(scores)
