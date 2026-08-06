"""Bake-off-only validation-horizon selection."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite

_CANDIDATE_HORIZONS_S = (600, 800, 1000)


def select_validation_horizon(validation_scores: Mapping[int, float]) -> int:
    """Select the shortest candidate within one percent of the validation best."""
    if set(validation_scores) != set(_CANDIDATE_HORIZONS_S):
        raise ValueError("validation scores must contain exactly 600, 800, and 1000 seconds")
    scores = {horizon_s: float(score) for horizon_s, score in validation_scores.items()}
    if not all(isfinite(score) and score >= 0.0 for score in scores.values()):
        raise ValueError("validation scores must be finite and non-negative")
    best = min(scores.values())
    threshold = best * 1.01
    return next(
        horizon_s for horizon_s in _CANDIDATE_HORIZONS_S if scores[horizon_s] < threshold or scores[horizon_s] == best
    )
