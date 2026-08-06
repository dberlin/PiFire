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

def test_mpc_online_adaptation_option_is_an_explicit_opt_in():
    config = json.loads(CATALOG.read_text())["metadata"]["mpc"]["config"]
    option = next(item for item in config if item["option_name"] == "enable_online_adaptation")

    assert sum(item["option_name"] == "enable_online_adaptation" for item in config) == 1
    assert option == {
        "option_name": "enable_online_adaptation",
        "option_friendly_name": "Online Model Adaptation",
        "option_description": (
            "Learn a scheduled linear model during cooks and let it take over after repeated "
            "validation wins. Experimental. [Default=false]"
        ),
        "option_type": "bool",
        "option_default": False,
        "hidden": False,
    }
