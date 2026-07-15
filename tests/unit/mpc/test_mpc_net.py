import os
import numpy as np
import pytest
from controller.mpc_net import NetPolicy, net_path_for
from controller.mpc import _DEFAULTS

ART = os.path.join(os.path.dirname(__file__), "..", "..", "..", "controller", "mpc_policy_net.npz")

pytestmark = pytest.mark.skipif(not os.path.exists(ART), reason="net artifact not exported")


def _policy():
    return NetPolicy.load(ART)


def test_artifact_loads_and_matches_defaults():
    p = _policy()
    # the shipped net was trained on the default calibration
    assert p.matches_config(_DEFAULTS)
    assert p.n_delay == int(_DEFAULTS["n_delay"])
    assert p.sp_lo < p.sp_hi


def test_numpy_forward_matches_torch_reference():
    # the export embedded torch-computed (state,u_prev,T_set)->Q pairs; the pure
    # numpy NetPolicy must reproduce them (export/import + matmul fidelity).
    z = np.load(ART)
    p = _policy()
    state, uprev, sset, qref = z["ref_state"], z["ref_uprev"], z["ref_set"], z["ref_Q"]
    for i in range(len(qref)):
        q = p.firing_rate(state[i], float(uprev[i]), float(sset[i]))
        assert abs(q - float(qref[i])) < 1e-3


def test_firing_rate_bounded_and_increases_with_setpoint():
    p = _policy()
    nd = p.n_delay
    # a settled-ish state at ~150C with zero disturbance
    x = np.array([20.0] * nd + [220.0, 150.0, 0.0])
    qs = [p.firing_rate(x, 20.0, sc) for sc in (110.0, 170.0, 230.0, 285.0)]
    for q in qs:
        assert _DEFAULTS["Q_min"] <= q <= _DEFAULTS["Q_max"]
    # hotter targets need more firing (Q_ss is monotone in T_set)
    assert qs[0] < qs[-1]


def test_matches_config_rejects_recalibration():
    p = _policy()
    bad = dict(_DEFAULTS)
    bad["K_Q"] = _DEFAULTS["K_Q"] * 1.5
    assert not p.matches_config(bad)
    bad2 = dict(_DEFAULTS)
    bad2["n_delay"] = int(_DEFAULTS["n_delay"]) + 1
    assert not p.matches_config(bad2)


def test_net_path_for_fan_off_returns_base():
    assert net_path_for("./controller/mpc_policy_net.npz", False) == "./controller/mpc_policy_net.npz"


def test_net_path_for_fan_on_inserts_suffix():
    assert net_path_for("./controller/mpc_policy_net.npz", True) == "./controller/mpc_policy_net_fan.npz"


def test_net_path_for_handles_dotted_dirs():
    # dots in the directory must not confuse the extension split
    assert net_path_for("/opt/pi.fire/models/net.npz", True) == "/opt/pi.fire/models/net_fan.npz"


def test_legacy_artifact_defaults_to_fan_off():
    # the shipped artifact predates the flag; it must load and read as fan-off (0)
    p = NetPolicy.load(ART)
    assert p.calib["enable_fan_input"] == 0
    assert p.matches_config({**_DEFAULTS, "enable_fan_input": False})
    assert not p.matches_config({**_DEFAULTS, "enable_fan_input": True})
