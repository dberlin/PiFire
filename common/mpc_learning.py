"""Shared current contract for MPC challenger forecast horizons."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

MPC_PREDICTION_STEP_S = 25
MPC_OBSERVATION_FRAME_S = 20


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class ForecastHorizonSpec:
    """One elapsed-time horizon expressed in both MPC-owned clocks."""

    seconds: int
    prediction_steps: int
    observation_frames: int
    maximum_rmse_c: float

    def __post_init__(self) -> None:
        seconds = _positive_int(self.seconds, "seconds")
        prediction_steps = _positive_int(self.prediction_steps, "prediction_steps")
        observation_frames = _positive_int(self.observation_frames, "observation_frames")
        if prediction_steps * MPC_PREDICTION_STEP_S != seconds:
            raise ValueError("prediction steps must exactly span the horizon")
        if observation_frames * MPC_OBSERVATION_FRAME_S != seconds:
            raise ValueError("observation frames must exactly span the horizon")
        if not isfinite(self.maximum_rmse_c) or self.maximum_rmse_c <= 0.0:
            raise ValueError("maximum_rmse_c must be finite and positive")


MPC_FORECAST_HORIZONS = tuple(
    ForecastHorizonSpec(seconds, prediction_steps, observation_frames, 2.8)
    for seconds, prediction_steps, observation_frames in (
        (100, 4, 5),
        (200, 8, 10),
        (300, 12, 15),
        (400, 16, 20),
        (600, 24, 30),
    )
)
MPC_FORECAST_HORIZON_SECONDS = tuple(horizon.seconds for horizon in MPC_FORECAST_HORIZONS)
_HORIZONS_BY_SECONDS = {horizon.seconds: horizon for horizon in MPC_FORECAST_HORIZONS}


def forecast_horizon_spec(seconds: object) -> ForecastHorizonSpec:
    """Return the current MPC horizon specification for exact elapsed seconds."""

    normalized = _positive_int(seconds, "horizon seconds")
    try:
        return _HORIZONS_BY_SECONDS[normalized]
    except KeyError as exc:
        raise ValueError(f"horizon seconds must be one of {MPC_FORECAST_HORIZON_SECONDS}") from exc
