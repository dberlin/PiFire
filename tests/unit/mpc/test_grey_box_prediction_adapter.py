"""Behavioral contracts for immutable 25-second, eight-delay grey forecasts."""

from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace

import numpy as np
import numpy.testing as npt
import pytest

from controller.grey_box import GreyBoxPredictionAdapter
from controller.mpc_model import GreyBoxEKF, GreyBoxKF


_CONFIG = {
    "C_c": 320.0,
    "h_amb": 0.5,
    "T_amb": 20.0,
    "theta": 40.0,
    "n_delay": 8,
    "K_Q": 350.0,
    "sigma": 0.0,
}
_CONTROLLER_STEP_S = 5.0
_PREDICTION_STEP_S = 25.0


def _controller_origin(*, sigma: float = 0.0) -> SimpleNamespace:
    config = dict(_CONFIG, sigma=sigma, control_period=_CONTROLLER_STEP_S)
    estimator = GreyBoxKF(
        C_c=320.0,
        h_amb=0.5,
        T_amb=20.0,
        theta=40.0,
        n_delay=8,
        K_Q=350.0,
        t_step=_CONTROLLER_STEP_S,
        q_temp=1e-2,
        q_dist=0.05,
        r_meas=0.04,
    )
    for load, measured_c in ((0.1, 80.0), (0.4, 95.0), (0.3, 110.0)):
        estimator.update(load, measured_c)
    return SimpleNamespace(cfg=config, estimator=estimator)


def _direct_kf_forecast(origin: SimpleNamespace, q_future: np.ndarray) -> np.ndarray:
    estimator = GreyBoxKF(
        C_c=_CONFIG["C_c"],
        h_amb=_CONFIG["h_amb"],
        T_amb=_CONFIG["T_amb"],
        theta=_CONFIG["theta"],
        n_delay=_CONFIG["n_delay"],
        K_Q=_CONFIG["K_Q"],
        t_step=_PREDICTION_STEP_S,
        q_temp=1e-2,
        q_dist=0.05,
        r_meas=0.04,
        x0=origin.estimator.x,
    )
    state = estimator.x.copy()
    output = np.empty(q_future.size)
    for index, q in enumerate(q_future):
        state = estimator.Ad @ state + estimator.Bd.ravel() * q + estimator.bd.ravel()
        output[index] = state[_CONFIG["n_delay"]]
    return output


def _ekf_controller_origin() -> SimpleNamespace:
    config = dict(_CONFIG, sigma=1.4e-9, control_period=_CONTROLLER_STEP_S)
    estimator = GreyBoxEKF(
        C_c=320.0,
        h_amb=0.5,
        T_amb=20.0,
        theta=40.0,
        n_delay=8,
        K_Q=350.0,
        sigma=1.4e-9,
        t_step=_CONTROLLER_STEP_S,
        q_temp=1e-2,
        q_dist=0.05,
        r_meas=0.04,
    )
    for load, measured_c in ((0.1, 80.0), (0.4, 95.0), (0.3, 110.0)):
        estimator.update(load, measured_c)
    return SimpleNamespace(cfg=config, estimator=estimator)


def _direct_frozen_ekf_forecast(
    origin: SimpleNamespace,
    q_future: np.ndarray,
    ambient_future: np.ndarray,
) -> np.ndarray:
    estimator = origin.estimator
    temperature_index = _CONFIG["n_delay"]
    chamber_origin = float(estimator.x[temperature_index])
    state = estimator.x.copy()
    output = np.empty(q_future.size)
    estimator.t_step = _PREDICTION_STEP_S
    for index, (q, ambient_c) in enumerate(zip(q_future, ambient_future, strict=True)):
        estimator.T_amb = float(ambient_c)
        estimator.Baug[temperature_index, 1] = _CONFIG["h_amb"] * ambient_c / _CONFIG["C_c"]
        estimator.x[temperature_index] = chamber_origin
        transition, q_gain, offset = estimator._discretize()
        state = transition @ state + q_gain.ravel() * q + offset.ravel()
        output[index] = state[temperature_index]
    return output


def test_default_five_second_controller_origin_forecasts_locked_twenty_five_second_dynamics() -> None:
    controller = _controller_origin(sigma=1.4e-9)
    adapter = GreyBoxPredictionAdapter.from_controller(controller)
    q_future = np.array([0.2, 0.6, 0.4, 0.8, 0.1])
    ambient_future = np.full(q_future.size, _CONFIG["T_amb"])

    assert controller.cfg["control_period"] == _CONTROLLER_STEP_S
    assert adapter.state.shape == (10,)
    npt.assert_allclose(
        adapter.forecast(q_future, ambient_future),
        _direct_kf_forecast(controller, q_future),
        atol=1e-8,
        rtol=0.0,
    )


def test_radiative_ekf_adapter_applies_each_future_ambient_at_fixed_prediction_steps() -> None:
    controller = _ekf_controller_origin()
    assert controller.estimator.t_step == _CONTROLLER_STEP_S
    adapter = GreyBoxPredictionAdapter.from_controller(controller)
    q_future = np.array([0.2, 0.6, 0.4, 0.8, 0.1])
    ambient_future = np.array([20.0, 30.0, 15.0, 25.0, 18.0])
    direct = _direct_frozen_ekf_forecast(controller, q_future, ambient_future)

    affine = adapter.affine_prediction(
        horizon_steps=q_future.size,
        q_previous=0.3,
        ambient_future=ambient_future,
    )

    npt.assert_allclose(adapter.forecast(q_future, ambient_future), direct, atol=1e-8)
    npt.assert_allclose(affine.free_output_c + affine.input_response_c @ q_future, direct, atol=1e-8)


def test_affine_map_reconstructs_a_twenty_four_step_grey_forecast() -> None:
    controller = _controller_origin()
    adapter = GreyBoxPredictionAdapter.from_controller(controller)
    q_future = np.linspace(0.1, 0.9, 24)
    ambient_future = np.full(24, _CONFIG["T_amb"])

    affine = adapter.affine_prediction(
        horizon_steps=24,
        q_previous=0.3,
        ambient_future=ambient_future,
    )

    npt.assert_allclose(
        affine.free_output_c + affine.input_response_c @ q_future,
        _direct_kf_forecast(controller, q_future),
        atol=1e-8,
        rtol=0.0,
    )


def test_adapter_owns_origin_values_and_never_retains_the_live_controller() -> None:
    controller = _controller_origin()
    adapter = GreyBoxPredictionAdapter.from_controller(controller)
    q_future = np.full(5, 0.4)
    ambient_future = np.full(5, _CONFIG["T_amb"])
    before = adapter.forecast(q_future, ambient_future)

    controller.estimator.x[:] = -999.0
    controller.cfg["T_amb"] = 999.0

    npt.assert_allclose(adapter.forecast(q_future, ambient_future), before, atol=0.0)
    assert all(getattr(adapter, field.name) is not controller for field in fields(adapter))
    with pytest.raises(ValueError):
        adapter.state[0] = 0.0


def test_adapter_rejects_noncanonical_delay_state_count() -> None:
    controller = _controller_origin()
    controller.cfg["n_delay"] = 7

    with pytest.raises(ValueError, match="eight delay"):
        GreyBoxPredictionAdapter.from_controller(controller)


def test_adapter_rejects_nonfinite_or_mismatched_future_inputs() -> None:
    adapter = GreyBoxPredictionAdapter.from_controller(_controller_origin())

    with pytest.raises(ValueError, match="equal lengths"):
        adapter.forecast(np.ones(3), np.ones(2))
    with pytest.raises(ValueError, match="finite"):
        adapter.forecast(np.array([np.nan]), np.array([20.0]))
