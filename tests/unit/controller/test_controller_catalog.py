import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[3] / "controller" / "controllers.json"
FIXTURE = Path(__file__).resolve().parents[3] / "web-react" / "tests" / "e2e" / "fixtures" / "controller-metadata.json"
RETAINED = {"pid", "pid_sp", "mpc"}


def test_controller_catalog_contains_exactly_the_supported_controllers():
    metadata = json.loads(CATALOG.read_text())["metadata"]
    assert set(metadata) == RETAINED
    assert {entry["module_name"] for entry in metadata.values()} == RETAINED


def test_controller_metadata_fixture_matches_production_catalog():
    metadata = json.loads(CATALOG.read_text())["metadata"]
    fixture = json.loads(FIXTURE.read_text())["metadata"]
    assert set(fixture) == RETAINED
    assert fixture == metadata


def test_mpc_catalog_is_exactly_the_acados_grey_box_settings_surface():
    entry = json.loads(CATALOG.read_text())["metadata"]["mpc"]
    config = {item["option_name"]: item for item in entry["config"]}

    assert set(entry) == {
        "friendly_name",
        "module_name",
        "image",
        "description",
        "author",
        "link",
        "contributors",
        "attributions",
        "recommendations",
        "config",
    }
    assert "acados" in entry["description"].lower()
    assert "grey-box" in entry["description"].lower()
    assert "do-mpc" not in entry["description"].lower()
    assert set(config) == {
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
    assert config["n_horizon"] == {
        "option_name": "n_horizon",
        "option_friendly_name": "Prediction Horizon (steps)",
        "option_description": "Number of 25-second prediction steps. [Default=24]",
        "option_type": "int",
        "option_default": 24,
        "option_min": 5,
        "option_max": 24,
        "option_step": 1,
        "hidden": False,
    }
    assert config["estimator"]["list_values"] == ["ekf", "kf"]
    assert config["estimator"]["option_default"] == "ekf"


def test_mpc_learning_descriptions_state_the_grey_activation_timing():
    config = {
        item["option_name"]: item for item in json.loads(CATALOG.read_text())["metadata"]["mpc"]["config"]
    }

    passive = config["enable_online_adaptation"]["option_description"].lower()
    refit = config["enable_identification"]["option_description"].lower()
    assert "grey-box" in passive
    assert "automatic" in passive
    assert "validation" in passive
    assert "grey-box" in refit
    assert "next cook" in refit
