import os

import numpy as np

from controller.mpc import _DEFAULTS
from controller.mpc_model import MODEL_SCHEMA
from controller.mpc_net import NetPolicy, _CALIB_FLOATS, _CALIB_INTS, net_path_for

ART = os.path.join(os.path.dirname(__file__), "..", "..", "..", "controller", "mpc_policy_net.npz")


def _normalized_policy():
    calib = {key: _DEFAULTS[key] for key in _CALIB_FLOATS}
    calib.update({key: _DEFAULTS[key] for key in _CALIB_INTS})
    calib["model_schema"] = MODEL_SCHEMA
    input_dim = int(_DEFAULTS["n_delay"]) + 4
    return NetPolicy(
        [(np.zeros((input_dim, 1)), np.zeros(1))],
        np.zeros(input_dim),
        np.ones(input_dim),
        0.0,
        1.0,
        calib,
        110.0,
        285.0,
    )


def test_regenerated_normalized_artifact_matches_the_active_config():
    policy = _normalized_policy()

    assert policy.model_schema == MODEL_SCHEMA
    assert policy.matches_config(_DEFAULTS)


def test_old_scale_shipped_artifact_is_rejected_by_model_schema():
    old_scale = NetPolicy.load(ART)

    assert old_scale.model_schema != MODEL_SCHEMA
    assert not old_scale.matches_config(_DEFAULTS)


def test_firing_rate_is_bounded_to_the_normalized_command_domain():
    policy = _normalized_policy()
    state = np.array([0.0] * policy.n_delay + [150.0, 0.0])

    for setpoint in (110.0, 170.0, 230.0, 285.0):
        assert 0.0 <= policy.firing_rate(state, 0.5, setpoint) <= 1.0


def test_normalized_artifact_rejects_a_changed_model_calibration():
    policy = _normalized_policy()
    altered = dict(_DEFAULTS, K_Q=_DEFAULTS["K_Q"] * 1.5)

    assert not policy.matches_config(altered)


def test_net_path_for_fan_off_returns_base():
    assert net_path_for("./controller/mpc_policy_net.npz", False) == "./controller/mpc_policy_net.npz"


def test_net_path_for_fan_on_inserts_suffix():
    assert net_path_for("./controller/mpc_policy_net.npz", True) == "./controller/mpc_policy_net_fan.npz"
