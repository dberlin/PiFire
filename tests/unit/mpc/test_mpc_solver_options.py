import subprocess
import sys
from types import SimpleNamespace

import pytest

import controller.mpc as mpc_module
from controller.acados import GreyBoxMPCConfig
from tests.unit.mpc._solver_fixtures import CYCLE, _config, _Estimator, _Solver


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


def test_loading_the_live_mpc_module_does_not_import_retired_policy_stacks():
    code = """
import builtins

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

builtins.__import__ = guarded_import
import controller.mpc
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_snapshot_codec_is_not_reexported_from_the_live_controller_module():
    retired_exports = (
        "GreySnapshotInvalid",
        "migrate_grey_learning_snapshot",
        "normalize_grey_parameters",
        "GREY_BOX_KIND",
        "MODEL_PARAM_KEYS",
    )

    assert all(not hasattr(mpc_module, name) for name in retired_exports)
