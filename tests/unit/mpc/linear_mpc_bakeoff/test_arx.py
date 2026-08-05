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
    temperature = np.zeros_like(q)
    delta_temperature = np.zeros_like(q)
    delta_q = np.diff(q, prepend=q[0])
    for index in range(3, len(q)):
        delta_temperature[index] = (
            0.92 * delta_temperature[index - 1] + 0.06 * delta_q[index - 3]
        )
        temperature[index] = temperature[index - 1] + delta_temperature[index]
    return temperature


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


def incremental_step(q: np.ndarray) -> np.ndarray:
    """Return a stable delayed plant expressed in approved increment coordinates."""
    temperature = np.full(q.size, 20.0)
    delta_temperature = np.zeros(q.size)
    delta_q = np.diff(q, prepend=q[0])
    for index in range(3, q.size):
        delta_temperature[index] = (
            0.85 * delta_temperature[index - 1]
            + 0.3 * delta_q[index - 3]
            + 0.05 * (20.0 - temperature[index - 1])
        ) / 1.05
        temperature[index] = temperature[index - 1] + delta_temperature[index]
    return temperature


def incremental_record(*, samples: int, seed: int) -> SignalRecord:
    rng = np.random.default_rng(seed)
    q = rng.uniform(0.0, 1.0, samples)
    return SignalRecord(
        time_s=np.arange(samples, dtype=np.float64) * 20.0,
        temp_c=incremental_step(q),
        q=q,
        ambient_c=np.full(samples, 20.0),
        provenance="synthetic",
    )


def training_prefix() -> SignalRecord:
    return synthetic_record(synthetic_step, samples=400, seed=7)


def fitted_model(prefix: SignalRecord) -> ScheduledARX:
    model = ScheduledARX(ARXConfig(na=1, nb=1, delays=(1, 2, 3)))
    model.fit(prefix)
    return model


def observation(*, temp_c: float, q: float) -> Observation:
    return Observation(time_s=8_000.0, temp_c=temp_c, q=q, ambient_c=0.0)


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
        "track",
        "snapshot",
    }


def test_arx_recovers_delay_and_stable_pole() -> None:
    record = synthetic_record(synthetic_step, samples=1200, seed=4)
    model = ScheduledARX(ARXConfig(na=1, nb=1, delays=(1, 2, 3)))

    model.fit(record)

    snapshot = model.snapshot()
    assert snapshot["delay_steps"] == 2
    assert abs(snapshot["regions"][0]["poles"][0]) < 1.0


def test_arx_fits_incremental_temperature_and_input_regressors() -> None:
    record = incremental_record(samples=1_200, seed=15)
    model = ScheduledARX(ARXConfig(na=1, nb=1, delays=(1, 2, 3)))

    model.fit(record)

    snapshot = model.snapshot()
    coefficients = snapshot["regions"][0]["coefficients"]
    assert snapshot["delay_steps"] == 2
    assert coefficients["ar"][0] == pytest.approx(0.85, abs=0.02)
    assert coefficients["input"][0] == pytest.approx(0.3, abs=0.02)

def test_snapshot_exposes_arm_neutral_gain_and_delay_diagnostics() -> None:
    snapshot = fitted_model(training_prefix()).snapshot()

    assert snapshot["steady_gain"] > 0.0
    assert snapshot["delay_seconds"] == snapshot["delay_steps"] * 20.0
    bounds = snapshot["plausibility_bounds"]
    assert bounds["max_dc_gain_c_per_q"] > 0.0
    assert snapshot["update_timing"]["refreshes"] >= 0


def test_arx_reconstructs_incremental_forecast_from_prefix_state() -> None:
    record = incremental_record(samples=1_000, seed=16)
    prefix_size = 700
    prefix = SignalRecord(
        record.time_s[:prefix_size],
        record.temp_c[:prefix_size],
        record.q[:prefix_size],
        record.ambient_c[:prefix_size],
        record.provenance,
    )
    model = ScheduledARX(ARXConfig(na=1, nb=1, delays=(1, 2, 3)))
    model.fit(prefix)

    forecast = model.forecast(
        prefix,
        record.q[prefix_size:],
        record.ambient_c[prefix_size:],
    )

    npt.assert_allclose(forecast, record.temp_c[prefix_size:], atol=0.02)



def test_arx_regressors_use_temperature_and_delayed_input_increments() -> None:
    model = ScheduledARX(ARXConfig(na=1, nb=1, delays=(2,)))

    feature = model._feature(
        temperatures=np.array([20.0, 21.0, 23.0, 26.0]),
        inputs=np.array([0.1, 0.2, 0.5, 0.4]),
        ambient_c=20.0,
        target_temp_c=27.0,
        delay_steps=2,
    )

    assert feature[0] == pytest.approx(3.0)
    assert feature[1] == pytest.approx(0.1)
    assert feature[-2] == pytest.approx(-7.0)

def test_arx_update_is_prequential() -> None:
    prefix = training_prefix()
    model = fitted_model(prefix)
    before = model.forecast(prefix, np.array([0.4]), np.array([0.0]))

    outcome = model.observe(observation(temp_c=999.0, q=0.4))

    assert outcome.predicted_temp_c == pytest.approx(before[0])


def test_arx_affine_prediction_matches_direct_forecast() -> None:
    prefix = training_prefix()
    model = fitted_model(prefix)
    ambient = np.zeros(10)
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



def test_scheduled_interpolation_retains_slow_poles_and_limits_dc_gain() -> None:
    """Data-scaled gain regularization must not flatten legitimate slow dynamics."""
    model = ScheduledARX(ARXConfig(na=2, nb=2, delays=(1,)))
    model.fit(training_prefix())
    candidate = model._candidates[1]
    candidate.regions[0].theta[:2] = (0.995, 0.0)
    candidate.regions[0].theta[2:4] = (100.0, 0.0)

    theta = model._scheduled_theta(candidate, 80.0)
    dc_gain = float(np.sum(theta[2:4]) / (1.0 - np.sum(theta[:2])))

    assert np.max(np.abs(np.roots(np.concatenate(([1.0], -theta[:2]))))) >= 0.99
    assert model._max_dc_gain is not None
    assert 0.0 < dc_gain <= model._max_dc_gain


def test_arx_regularizes_explosive_sixty_minute_input_response() -> None:
    """An online candidate must stay inside its training-derived horizon envelope."""
    prefix = training_prefix()
    model = ScheduledARX(ARXConfig(na=2, nb=2, delays=(1,)))
    model.fit(prefix)
    for region in model._candidates[1].regions:
        region.theta[:2] = (0.995, 0.0)
        region.theta[-2] = -0.999
        region.theta[2:4] = (100.0, 0.0)

    prediction = model.affine_prediction(180, prefix.q[-1], np.zeros(180))

    assert model._max_forecast_deviation is not None
    assert np.isfinite(prediction.input_response_c).all()
    assert np.max(np.abs(prediction.input_response_c)) <= model._max_forecast_deviation
    assert np.max(np.abs(prediction.free_output_c - prefix.temp_c[-1])) <= (
        model._max_forecast_deviation
    )

def test_affine_prediction_never_mutates_fitted_arx_regions() -> None:
    """Forecast-envelope limiting must not rewrite fitted theta or RLS state."""
    prefix = training_prefix()
    model = ScheduledARX(ARXConfig(na=2, nb=2, delays=(1,)))
    model.fit(prefix)
    for region in model._candidates[1].regions:
        region.theta[:2] = (0.995, 0.0)
        region.theta[-2] = -0.999
        region.theta[2:4] = (100.0, 0.0)
    model._max_forecast_deviation = 0.01

    before = model.snapshot()
    model.affine_prediction(180, prefix.q[-1], np.zeros(180))
    model.affine_prediction(180, prefix.q[-1], np.zeros(180))

    assert model.snapshot() == before

def test_track_updates_arx_history_without_rewriting_fitted_regions() -> None:
    """A frozen ARX incumbent advances prediction history without an RLS update."""
    prefix = training_prefix()
    model = fitted_model(prefix)
    before = model.snapshot()

    outcome = model.track(observation(temp_c=100.0, q=0.4))

    assert outcome.updated is False
    assert model.snapshot()["regions"] == before["regions"]

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

    assert model.snapshot()["update_timing"]["last_refresh_sample"] == 22


def test_snapshot_serializes_complex_poles_as_real_and_imaginary_parts() -> None:
    model = ScheduledARX(ARXConfig(na=2, nb=1, delays=(1,)))
    model._candidates[1].regions[0].theta[:2] = (0.0, -0.25)

    poles = model.snapshot()["regions"][0]["poles"]

    assert poles == (
        {"real": 0.0, "imag": 0.5},
        {"real": 0.0, "imag": -0.5},
    )
