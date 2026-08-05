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
        "enable_fan_input",
        "est_r_meas",
    } <= names
    policy = next(o for o in e["config"] if o["option_name"] == "policy")
    assert set(policy["list_values"]) == {"nlp", "net"}
    assert policy["option_default"] == "nlp"


def test_pid_sp_declares_numpy_without_an_extra():
    """numpy missing means a broken install, not a missing opt-in, so there is
    nothing for PiFire to offer to install."""
    meta = _meta()["pid_sp"]
    assert meta["dependencies"] == {"modules": ["numpy"]}
    assert "extra" not in meta["dependencies"]


def test_pid_sp_no_longer_offers_tau_or_theta():
    """Identification is online; a user-supplied tau=115 is not merely unused,
    it is outside the design's own trusted band of 300-20000 s."""
    options = {o["option_name"] for o in _meta()["pid_sp"]["config"]}
    assert "tau" not in options
    assert "theta" not in options
    assert options == {"PB", "Td", "Ti", "stable_window", "center_factor"}


def test_numpy_is_an_explicit_project_dependency():
    import tomllib

    with open(os.path.join(BASE, "pyproject.toml"), "rb") as f:
        project = tomllib.load(f)["project"]
    assert any(d.split(">")[0].split("=")[0].strip() == "numpy" for d in project["dependencies"])


def test_default_controller_config_includes_mpc():
    cwd = os.getcwd()
    os.chdir(BASE)
    try:
        from common.defaults import _default_controller_config

        cfg = _default_controller_config()
    finally:
        os.chdir(cwd)
    assert "mpc" in cfg
    assert cfg["mpc"]["control_period"] == 5.0
    assert cfg["mpc"]["theta"] == 50.0
    assert cfg["mpc"]["n_delay"] == 8
    assert cfg["mpc"]["K_Q"] == 350.0
    assert cfg["mpc"]["estimator"] == "ekf"
    assert cfg["mpc"]["policy"] == "nlp"
    assert cfg["mpc"]["sigma"] > 0.0
    assert cfg["mpc"]["enable_fan_input"] is False
