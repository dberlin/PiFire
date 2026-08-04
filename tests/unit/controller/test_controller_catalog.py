import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[3] / "controller" / "controllers.json"
FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "web-react"
    / "tests"
    / "e2e"
    / "fixtures"
    / "controller-metadata.json"
)
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
