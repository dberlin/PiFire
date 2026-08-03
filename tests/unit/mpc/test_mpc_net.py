import os
import tempfile

import numpy as np
import pytest
from controller.mpc_model import MODEL_SCHEMA
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
    # a settled-ish state at ~150C with zero disturbance:
    # [q0..q_{nd-1}, T_c, d]
    x = np.array([20.0] * nd + [150.0, 0.0])
    qs = [p.firing_rate(x, 20.0, sc) for sc in (110.0, 170.0, 230.0, 285.0)]
    for q in qs:
        assert _DEFAULTS["Q_min"] <= q <= _DEFAULTS["Q_max"]
    # hotter targets need more firing (Q_ss is monotone in T_set)
    assert qs[0] < qs[-1]


def test_the_artifact_was_trained_on_this_model_s_state_vector():
    """The one mismatch nothing else in the calibration can see.

    An artifact trained against the two-lump model carries one extra state
    slot, and every scalar `matches_config` compares -- n_delay included -- is
    identical between the two models, so nothing but the width distinguishes
    them. Adopting one raises inside `firing_rate` on every control step, and
    Controller.update() catches that and holds the previous firing rate, so the
    grill would run on a frozen output with nothing said.

    The forged artifact below is the negative control: it is the real one with
    a slot added, which is what a pre-upgrade file is.
    """
    p = _policy()
    assert p.input_dim == p.n_delay + 4  # [q0..q_{nd-1}, T_c, d] + u_prev + T_set
    assert p.matches_config(_DEFAULTS)

    stale = _policy()
    stale.x_mean = np.append(stale.x_mean, 0.0)
    stale.x_std = np.append(stale.x_std, 1.0)
    assert stale.input_dim == p.input_dim + 1
    assert not stale.matches_config(_DEFAULTS)


def test_a_structure_change_that_keeps_the_width_is_still_refused():
    """The gap the width test alone leaves.

    Width catches a structure change that happens to RESIZE the state, which is
    what collapsing to one lump did. A future change that reorders the slots or
    swaps their meaning without resizing -- the disturbance and the chamber
    trading places, say -- passes the width test while making every reading the
    net takes wrong. `model_schema` is what such a change has to bump, so it is
    checked independently of the width.

    Forged both ways round: same width and a stale schema must be refused, and
    the shipped artifact must genuinely declare the current one rather than
    passing because the field defaulted to it.
    """
    p = _policy()
    assert p.model_schema == MODEL_SCHEMA
    assert p.matches_config(_DEFAULTS)

    same_width_old_structure = _policy()
    same_width_old_structure.model_schema = MODEL_SCHEMA - 1
    assert same_width_old_structure.input_dim == p.input_dim
    assert not same_width_old_structure.matches_config(_DEFAULTS)


def test_an_artifact_predating_the_structure_field_reads_as_the_old_model():
    """A missing declaration is the old model, never the current one.

    Every artifact exported before `model_schema` existed was trained on the
    two-lump model, so absence has to mean schema 1. Defaulting it to
    MODEL_SCHEMA would make the check pass by default on precisely the files it
    exists to catch.
    """
    z = dict(np.load(ART))
    assert "model_schema" in z, "the shipped artifact must declare its structure"

    legacy = {k: v for k, v in z.items() if k != "model_schema"}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "legacy.npz")
        np.savez_compressed(path, **legacy)
        old = NetPolicy.load(path)
    assert old.model_schema == 1
    assert not old.matches_config(_DEFAULTS)


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
