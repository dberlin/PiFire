import json
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _meta():
    with open(os.path.join(BASE, "controller", "controllers.json")) as f:
        return json.load(f)["metadata"]


def test_mpc_entry_present():
    e = _meta()["mpc"]
    assert e["module_name"] == "mpc"
    names = {o["option_name"] for o in e["config"]}
    # a representative subset of the required options
    assert {
        "n_horizon",
        "control_period",
        "theta",
        "n_delay",
        "K_Q",
        "sigma",
        "estimator",
        "policy",
        "policy_net_path",
        "C_c",
        "h_amb",
        "Q_max",
        "enable_fan_input",
        "est_r_meas",
    } <= names
    policy = next(o for o in e["config"] if o["option_name"] == "policy")
    assert set(policy["list_values"]) == {"nlp", "net"}
    assert policy["option_default"] == "nlp"


def test_default_controller_config_includes_mpc():
    cwd = os.getcwd()
    os.chdir(BASE)
    try:
        from common.common import _default_controller_config

        cfg = _default_controller_config()
    finally:
        os.chdir(cwd)
    assert "mpc" in cfg
    assert cfg["mpc"]["control_period"] == 5.0
    assert cfg["mpc"]["theta"] == 50.0
    assert cfg["mpc"]["n_delay"] == 4
    assert cfg["mpc"]["K_Q"] == 3.5
    assert cfg["mpc"]["estimator"] == "ekf"
    assert cfg["mpc"]["policy"] == "nlp"
    assert cfg["mpc"]["sigma"] > 0.0
    assert cfg["mpc"]["enable_fan_input"] is False
