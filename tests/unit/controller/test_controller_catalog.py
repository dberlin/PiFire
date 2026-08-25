import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from common.web_contracts.registry import WEB_CONTRACT_BUNDLES
from common.web_contracts.settings import (
    CONTROLLER_CONFIG_MODELS,
    ControllerCatalog,
    ControllerConfigs,
)

CATALOG = Path(__file__).resolve().parents[3] / "controller" / "controllers.json"
FIXTURE = Path(__file__).resolve().parents[3] / "web-react" / "tests" / "e2e" / "fixtures" / "controller-metadata.json"
RETAINED = {"pid", "pid_sp", "mpc"}

OPTION_TYPES = {
    "float": float,
    "int": int,
    "bool": bool,
    "string": str,
}


def test_production_controller_catalog_validates_and_round_trips():
    payload = json.loads(CATALOG.read_text())

    catalog = ControllerCatalog.model_validate(payload, strict=True)

    assert catalog.model_dump(mode="json") == payload


def test_controller_catalog_preserves_numeric_option_value_types():
    payload = json.loads(CATALOG.read_text())
    dumped = ControllerCatalog.model_validate(payload, strict=True).model_dump(mode="json")

    for name, definition in payload["metadata"].items():
        for source, rendered in zip(
            definition["config"],
            dumped["metadata"][name]["config"],
            strict=True,
        ):
            if source["option_type"] not in {"float", "int"}:
                continue
            numeric_type = float if source["option_type"] == "float" else int
            for field in ("option_default", "option_min", "option_max", "option_step"):
                if field in source and source[field] is not None:
                    assert type(rendered[field]) is numeric_type


def test_dynamic_controller_configs_match_manifest_names_types_and_defaults():
    metadata = json.loads(CATALOG.read_text())["metadata"]

    assert set(CONTROLLER_CONFIG_MODELS) == set(metadata)
    assert set(ControllerConfigs.model_fields) == set(metadata)

    for controller_name, definition in metadata.items():
        model = CONTROLLER_CONFIG_MODELS[controller_name]
        options = definition["config"]
        assert set(model.model_fields) == {option["option_name"] for option in options}
        assert model().model_dump(mode="json") == {
            option["option_name"]: option["option_default"] for option in options
        }

        for option in options:
            field = model.model_fields[option["option_name"]]
            assert not field.is_required()
            assert field.default == option["option_default"]
            if option["option_type"] == "list":
                for declared in option["list_values"]:
                    model.model_validate({option["option_name"]: declared}, strict=True)
                with pytest.raises(ValidationError):
                    model.model_validate({option["option_name"]: "__not_declared__"}, strict=True)
            else:
                expected_type = OPTION_TYPES[option["option_type"]]
                assert field.annotation is expected_type
                with pytest.raises(ValidationError):
                    model.model_validate(
                        {option["option_name"]: object()},
                        strict=True,
                    )


def test_controller_contract_bundle_targets_the_established_frontend_artifact():
    bundle = next(item for item in WEB_CONTRACT_BUNDLES if item.name == "controller")

    assert ControllerCatalog in bundle.models
    assert ControllerConfigs in bundle.models
    assert bundle.typescript_output == "../settings/controllerTypes.gen.ts"


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
    config = {item["option_name"]: item for item in json.loads(CATALOG.read_text())["metadata"]["mpc"]["config"]}

    passive = config["enable_online_adaptation"]["option_description"].lower()
    refit = config["enable_identification"]["option_description"].lower()
    assert "grey-box" in passive
    assert "automatic" in passive
    assert "validation" in passive
    assert "grey-box" in refit
    assert "next cook" in refit
