import builtins
import importlib
import sys
from types import SimpleNamespace

import pytest

import controller.mpc as mpc_module
from controller.acados import GreyBoxMPCConfig


CYCLE = {"u_min": 0.1, "u_max": 0.9}


def _config(**overrides):
    config = {
        "n_horizon": 12,
        "control_period": 3.5,
        "Q_w": 2.25,
        "R_dQ": 0.35,
        "C_c": 410.0,
        "h_amb": 0.65,
        "T_amb": 18.0,
        "theta": 62.0,
        "K_Q": 390.0,
        "sigma": 1.1e-9,
        "estimator": "ekf",
        "est_q_temp": 0.02,
        "est_q_dist": 0.07,
        "est_r_meas": 0.05,
        "enable_fan_input": True,
        "fan_min_pct": 42.0,
        "fan_max_pct": 93.0,
        "enable_online_adaptation": False,
    }
    config.update(overrides)
    return config


class _Estimator:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def update(self, _load, temperature):
        return [0.0] * 8 + [float(temperature), 0.0]


class _Solver:
    created = []

    def __init__(self, config):
        self.config = config
        self.closed = False
        self.created.append(self)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("horizon", [5, 12, 24])
def test_live_build_maps_every_native_configuration_value(monkeypatch, horizon):
    _Solver.created.clear()
    monkeypatch.setattr(mpc_module, "GreyBoxEKF", _Estimator)
    monkeypatch.setattr(mpc_module, "AcadosGreyBoxMPC", _Solver, raising=False)

    controller = mpc_module.Controller(_config(n_horizon=horizon), "C", dict(CYCLE))

    assert len(_Solver.created) == 1
    native = _Solver.created[0].config
    assert native == GreyBoxMPCConfig(
        C_c=410.0,
        h_amb=0.65,
        T_amb=18.0,
        theta=62.0,
        K_Q=390.0,
        sigma=1.1e-9,
        horizon_steps=horizon,
        delay_states=8,
        state_size=10,
        timestep_s=25.0,
        temperature_weight=2.25,
        terminal_weight=2.25,
        move_weight=0.35,
        residual_weight=1000.0,
        max_iterations=10,
    )
    assert controller.estimator.kwargs == {
        "C_c": 410.0,
        "h_amb": 0.65,
        "T_amb": 18.0,
        "t_step": 3.5,
        "q_temp": 0.02,
        "q_dist": 0.07,
        "r_meas": 0.05,
        "theta": 62.0,
        "n_delay": 8,
        "K_Q": 390.0,
        "sigma": 1.1e-9,
    }
    assert controller.mpc is _Solver.created[0]
    assert controller.model is None
    assert controller._net is None


def test_kf_is_the_only_alternate_estimator_and_keeps_control_cadence(monkeypatch):
    seen = []

    def build_kf(**kwargs):
        seen.append(kwargs)
        return _Estimator(**kwargs)

    monkeypatch.setattr(mpc_module, "GreyBoxKF", build_kf)
    monkeypatch.setattr(mpc_module, "AcadosGreyBoxMPC", _Solver, raising=False)
    controller = mpc_module.Controller(_config(estimator="kf"), "C", dict(CYCLE))

    assert len(seen) == 1
    assert seen[0]["t_step"] == 3.5
    assert seen[0]["n_delay"] == 8
    assert "sigma" not in seen[0]
    controller.close()


def test_fresh_default_model_has_no_residual_regularization(monkeypatch):
    _Solver.created.clear()
    monkeypatch.setattr(mpc_module, "AcadosGreyBoxMPC", _Solver, raising=False)
    controller = mpc_module.Controller(dict(mpc_module._DEFAULTS), "C", dict(CYCLE))
    assert controller.mpc.config.residual_weight == 0.0
    controller.close()


def test_loading_the_live_mpc_module_does_not_import_retired_policy_stacks(monkeypatch):
    forbidden = (
        "do_mpc",
        "casadi",
        "controller.mpc_net",
        "controller.linear_mpc.arx",
        "controller.linear_mpc.state_space",
        "controller.linear_mpc.policy",
    )
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if any(name == item or name.startswith(item + ".") for item in forbidden):
            raise AssertionError(f"retired live import: {name}")
        return original_import(name, *args, **kwargs)

    for name in tuple(sys.modules):
        if any(name == item or name.startswith(item + ".") for item in forbidden):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    reloaded = importlib.reload(mpc_module)
    assert reloaded.Controller
