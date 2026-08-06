"""Behavioral contracts for immutable grey-box affine prediction origins."""

from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace

import numpy as np
import numpy.testing as npt
import pytest

from controller.linear_mpc.grey_box import GreyBoxPredictionAdapter
from controller.mpc_model import GreyBoxEKF, GreyBoxKF


_CONFIG = {
    "C_c": 320.0,
    "h_amb": 0.5,
    "T_amb": 20.0,
    "theta": 40.0,
    "n_delay": 2,
    "K_Q": 350.0,
    "sigma": 0.0,
}


def _controller_origin(*, sigma: float = 0.0) -> SimpleNamespace:
    config = dict(_CONFIG, sigma=sigma, control_period=20.0)
    estimator = GreyBoxKF(
        **{name: config[name] for name in ("C_c", "h_amb", "T_amb", "theta", "n_delay", "K_Q")},
        t_step=20.0,
        q_temp=1e-2,
        q_dist=0.05,
        r_meas=0.04,
    )
    for load, measured_c in ((0.1, 80.0), (0.4, 95.0), (0.3, 110.0)):
        estimator.update(load, measured_c)
    return SimpleNamespace(cfg=config, estimator=estimator)


def _direct_estimator_forecast(origin: SimpleNamespace, q_future: np.ndarray) -> np.ndarray:
    """Forecast independently from the frozen production KF's own matrices."""
    estimator = origin.estimator
    state = estimator.x.copy()
    output = np.empty(q_future.size)
    for index, q in enumerate(q_future):
        state = estimator.Ad @ state + estimator.Bd.ravel() * q + estimator.bd.ravel()
        output[index] = state[_CONFIG["n_delay"]]
    return output


def test_kf_origin_uses_its_actual_discrete_dynamics_when_configured_sigma_is_nonzero() -> None:
    controller = _controller_origin(sigma=1.4e-9)
    q_future = np.array([0.2, 0.6, 0.4])
    adapter = GreyBoxPredictionAdapter.from_controller(controller)

    npt.assert_allclose(
        adapter.forecast(q_future, np.full(q_future.size, _CONFIG["T_amb"])),
        _direct_estimator_forecast(controller, q_future),
        atol=1e-8,
        rtol=0.0,
    )


def _ekf_controller_origin() -> SimpleNamespace:
    config = dict(_CONFIG, sigma=1.4e-9, control_period=20.0)
    estimator = GreyBoxEKF(
        **{name: config[name] for name in _CONFIG},
        t_step=20.0,
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
    """Use the production EKF discretizer at its frozen temperature origin."""
    estimator = origin.estimator
    temperature_index = int(origin.cfg["n_delay"])
    chamber_origin = float(estimator.x[temperature_index])
    state = estimator.x.copy()
    output = np.empty(q_future.size)
    for index, (q, ambient_c) in enumerate(zip(q_future, ambient_future, strict=True)):
        estimator.T_amb = float(ambient_c)
        estimator.Baug[temperature_index, 1] = float(origin.cfg["h_amb"]) * ambient_c / float(origin.cfg["C_c"])
        estimator.x[temperature_index] = chamber_origin
        transition, q_gain, offset = estimator._discretize()
        state = transition @ state + q_gain.ravel() * q + offset.ravel()
        output[index] = state[temperature_index]
    return output


def test_radiative_ekf_origin_applies_each_future_ambient_value() -> None:
    controller = _ekf_controller_origin()
    adapter = GreyBoxPredictionAdapter.from_controller(controller)
    q_future = np.array([0.2, 0.6, 0.4, 0.8])
    ambient_future = np.array([20.0, 30.0, 15.0, 25.0])

    direct = _direct_frozen_ekf_forecast(controller, q_future, ambient_future)
    affine = adapter.affine_prediction(
        horizon_steps=q_future.size,
        q_previous=0.3,
        ambient_future=ambient_future,
    )

    npt.assert_allclose(adapter.forecast(q_future, ambient_future), direct, atol=1e-8)
    npt.assert_allclose(
        affine.free_output_c + affine.input_response_c @ q_future,
        direct,
        atol=1e-8,
    )


def test_affine_map_reconstructs_a_frozen_grey_box_forecast_to_machine_precision() -> None:
    controller = _controller_origin()
    adapter = GreyBoxPredictionAdapter.from_controller(controller)
    q_future = np.array([0.1, 0.9, 0.2, 0.7, 0.4, 0.6, 0.3, 0.5, 0.8, 0.2, 0.1, 0.4, 0.9, 0.3, 0.6])
    ambient_future = np.full(q_future.size, _CONFIG["T_amb"])

    affine = adapter.affine_prediction(
        horizon_steps=q_future.size,
        q_previous=0.3,
        ambient_future=ambient_future,
    )
    direct = _direct_estimator_forecast(controller, q_future)
    npt.assert_allclose(
        affine.free_output_c + affine.input_response_c @ q_future,
        direct,
        atol=1e-8,
        rtol=0.0,
    )
    npt.assert_allclose(
        adapter.forecast(q_future[:3], ambient_future[:3]),
        direct[:3],
        atol=1e-8,
        rtol=0.0,
    )


def test_adapter_owns_origin_values_and_never_retains_the_live_controller() -> None:
    controller = _controller_origin()
    adapter = GreyBoxPredictionAdapter.from_controller(controller)
    q_future = np.full(3, 0.4)
    ambient_future = np.full(3, _CONFIG["T_amb"])
    before = adapter.forecast(q_future, ambient_future)

    controller.estimator.x[:] = -999.0
    controller.cfg["T_amb"] = 999.0

    npt.assert_allclose(adapter.forecast(q_future, ambient_future), before, atol=0.0)
    assert all(getattr(adapter, field.name) is not controller for field in fields(adapter))
    with pytest.raises(ValueError):
        adapter.state[0] = 0.0


def test_adapter_rejects_nonfinite_or_mismatched_future_inputs() -> None:
    adapter = GreyBoxPredictionAdapter.from_controller(_controller_origin())

    with pytest.raises(ValueError, match="equal lengths"):
        adapter.forecast(np.ones(3), np.ones(2))
    with pytest.raises(ValueError, match="finite"):
        adapter.forecast(np.array([np.nan]), np.array([20.0]))
