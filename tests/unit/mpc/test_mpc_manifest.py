import json
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _meta():
    with open(os.path.join(BASE, "controller", "controllers.json")) as f:
        return json.load(f)["metadata"]


def test_mpc_entry_exposes_only_retained_acados_grey_box_options():
    entry = _meta()["mpc"]
    options = {option["option_name"]: option for option in entry["config"]}

    assert entry["module_name"] == "mpc"
    assert "dependencies" not in entry
    assert set(options) == {
        "n_horizon",
        "control_period",
        "Q_w",
        "R_dQ",
        "C_c",
        "h_amb",
        "T_amb",
        "theta",
        "K_Q",
        "sigma",
        "estimator",
        "fan_min_pct",
        "fan_max_pct",
        "enable_fan_input",
        "est_q_temp",
        "est_q_dist",
        "est_r_meas",
        "enable_identification",
        "enable_online_adaptation",
    }
    assert options["n_horizon"]["option_min"] == 5
    assert options["n_horizon"]["option_max"] == 24
    assert options["estimator"]["list_values"] == ["ekf", "kf"]
    assert options["estimator"]["option_default"] == "ekf"


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
    assert options == {"PB", "Td", "Ti", "stable_window", "center_factor", "enable_identification"}


def test_numpy_is_an_explicit_project_dependency():
    import tomllib

    with open(os.path.join(BASE, "pyproject.toml"), "rb") as f:
        project = tomllib.load(f)["project"]
    assert any(d.split(">")[0].split("=")[0].strip() == "numpy" for d in project["dependencies"])


def test_default_controller_config_exposes_only_retained_acados_mpc_values():
    cwd = os.getcwd()
    os.chdir(BASE)
    try:
        from common.defaults import _default_controller_config

        cfg = _default_controller_config()["mpc"]
    finally:
        os.chdir(cwd)

    assert set(cfg) == {
        "n_horizon",
        "control_period",
        "Q_w",
        "R_dQ",
        "C_c",
        "h_amb",
        "T_amb",
        "theta",
        "K_Q",
        "sigma",
        "estimator",
        "fan_min_pct",
        "fan_max_pct",
        "enable_fan_input",
        "est_q_temp",
        "est_q_dist",
        "est_r_meas",
        "enable_identification",
        "enable_online_adaptation",
    }
    assert cfg["n_horizon"] == 24
    assert cfg["control_period"] == 5.0
    assert cfg["theta"] == 50.0
    assert cfg["K_Q"] == 350.0
    assert cfg["estimator"] == "ekf"
    assert cfg["sigma"] > 0.0
    assert cfg["enable_fan_input"] is False
    assert cfg["enable_identification"] is True
    assert cfg["enable_online_adaptation"] is False
