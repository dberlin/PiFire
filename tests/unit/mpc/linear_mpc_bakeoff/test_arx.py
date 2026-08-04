"""Analytical contracts for the scheduled adaptive ARX model."""

from __future__ import annotations

from collections.abc import Callable
import inspect

import numpy as np
import numpy.testing as npt
import pytest

from docs.superpowers.experiments.linear_mpc_bakeoff.arx import ARXConfig, ScheduledARX
from docs.superpowers.experiments.linear_mpc_bakeoff.contracts import (
    AdaptiveLinearModel,
    Observation,
    SignalRecord,
)


def synthetic_step(q: np.ndarray) -> np.ndarray:
    y = np.zeros_like(q)
    for k in range(2, len(q) - 1):
        y[k + 1] = 0.92 * y[k] + 0.06 * q[k - 2]
    return y


def synthetic_record(
    step: Callable[[np.ndarray], np.ndarray], *, samples: int, seed: int
) -> SignalRecord:
    rng = np.random.default_rng(seed)
    q = rng.uniform(0.0, 1.0, samples)
    return SignalRecord(
        time_s=np.arange(samples, dtype=np.float64) * 20.0,
        temp_c=step(q),
        q=q,
        ambient_c=np.zeros(samples, dtype=np.float64),
        provenance="synthetic",
    )


def training_prefix() -> SignalRecord:
    return synthetic_record(synthetic_step, samples=400, seed=7)


def fitted_model(prefix: SignalRecord) -> ScheduledARX:
    model = ScheduledARX(ARXConfig(na=1, nb=1, delays=(1, 2, 3)))
    model.fit(prefix)
    return model


def observation(*, temp_c: float, q: float) -> Observation:
    return Observation(time_s=8_000.0, temp_c=temp_c, q=q, ambient_c=20.0)


def test_model_protocol_has_only_shared_model_operations() -> None:
    methods = {
        name
        for name, member in inspect.getmembers(AdaptiveLinearModel)
        if callable(member) and not name.startswith("_")
    }

    assert methods == {
        "affine_prediction",
        "fit",
        "forecast",
        "observe",
        "snapshot",
    }


def test_arx_recovers_delay_and_stable_pole() -> None:
    record = synthetic_record(synthetic_step, samples=1200, seed=4)
    model = ScheduledARX(ARXConfig(na=1, nb=1, delays=(1, 2, 3)))

    model.fit(record)

    snapshot = model.snapshot()
    assert snapshot["delay_steps"] == 2
    assert abs(snapshot["regions"][0]["poles"][0]) < 1.0


def test_arx_update_is_prequential() -> None:
    prefix = training_prefix()
    model = fitted_model(prefix)
    before = model.forecast(prefix, np.array([0.4]), np.array([20.0]))

    outcome = model.observe(observation(temp_c=999.0, q=0.4))

    assert outcome.predicted_temp_c == pytest.approx(before[0])


def test_arx_affine_prediction_matches_direct_forecast() -> None:
    prefix = training_prefix()
    model = fitted_model(prefix)
    ambient = np.full(10, 20.0)
    affine = model.affine_prediction(
        10, q_previous=prefix.q[-1], ambient_future=ambient
    )
    q = np.linspace(0.2, 0.5, 10)
    expected = model.forecast(prefix, q, ambient)

    assert affine.free_output_c.shape == (10,)
    assert affine.input_response_c.shape == (10, 10)
    npt.assert_allclose(
        affine.free_output_c + affine.input_response_c @ q, expected, atol=1e-9
    )


def test_delay_challenger_wins_must_be_consecutive() -> None:
    model = ScheduledARX(
        ARXConfig(na=1, nb=1, delays=(1, 2, 3), validation_window=1)
    )
    active = model._candidates[1]
    winning_challenger = model._candidates[2]
    non_winning_challenger = model._candidates[3]
    active.validation_error = 3.0
    winning_challenger.validation_error = 1.0
    non_winning_challenger.validation_error = 2.0
    for candidate in model._candidates.values():
        candidate.validation_samples = 1
    winning_challenger.consecutive_wins = 0
    non_winning_challenger.consecutive_wins = 1

    model._refresh_delay(1)

    assert winning_challenger.consecutive_wins == 1
    assert non_winning_challenger.consecutive_wins == 0


def test_scheduled_interpolation_projects_high_order_ar_poles() -> None:
    model = ScheduledARX(ARXConfig(na=3, nb=1, delays=(1,)))
    candidate = model._candidates[1]
    candidate.regions[0].theta[:3] = (2.7, -2.43, 0.729)
    candidate.regions[1].theta[:3] = (-2.7, -2.43, -0.729)
    candidate.regions[0].theta[3] = 0.1
    candidate.regions[1].theta[3] = 0.1

    theta = model._scheduled_theta(candidate, 122.5)

    assert np.max(np.abs(np.roots(np.concatenate(([1.0], -theta[:3]))))) <= 0.999


def test_forecast_output_is_read_only() -> None:
    prefix = training_prefix()
    forecast = fitted_model(prefix).forecast(
        prefix, np.array([0.4]), np.array([20.0])
    )

    assert forecast.flags.writeable is False
    with pytest.raises(ValueError):
        forecast[0] = 0.0


def test_snapshot_is_recursively_immutable() -> None:
    snapshot = fitted_model(training_prefix()).snapshot()

    with pytest.raises(TypeError):
        snapshot["order"]["na"] = 2
    with pytest.raises(TypeError):
        snapshot["regions"][0]["coefficients"]["ar"][0] = 0.0


def test_fit_refresh_records_its_actual_sample_index() -> None:
    record = synthetic_record(synthetic_step, samples=25, seed=9)
    model = ScheduledARX(
        ARXConfig(na=1, nb=1, delays=(1,), validation_window=10)
    )

    model.fit(record)

    assert model.snapshot()["update_timing"]["last_refresh_sample"] == 21


def test_snapshot_serializes_complex_poles_as_real_and_imaginary_parts() -> None:
    model = ScheduledARX(ARXConfig(na=2, nb=1, delays=(1,)))
    model._candidates[1].regions[0].theta[:2] = (0.0, -0.25)

    poles = model.snapshot()["regions"][0]["poles"]

    assert poles == (
        {"real": 0.0, "imag": 0.5},
        {"real": 0.0, "imag": -0.5},
    )
