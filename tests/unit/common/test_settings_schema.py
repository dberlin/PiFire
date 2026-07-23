"""Parity: the pydantic shadow models must round-trip default_settings() exactly.

extra="allow" means sections not yet modeled pass through untouched, so this
test is meaningful from the first section onward and total as of Task 2 (all
21 top-level sections are now modeled).
"""

import copy
import json
import os

import pytest

from common import datastore, datastore_accessors
from common.defaults import default_settings
from common.settings_migration import read_settings_file
from common.settings_schema import SettingsSchema


def assert_parity(settings: dict) -> None:
    dumped = SettingsSchema.model_validate(settings).model_dump(mode="json")
    assert dumped == settings


def test_default_settings_round_trips():
    assert_parity(default_settings())


def test_extra_keys_survive():
    s = default_settings()
    s["safety"]["future_knob"] = 42
    s["totally_new_section"] = {"a": 1}
    assert_parity(s)


def test_lax_coercion_is_pinned():
    # S1 documents pydantic lax-mode behavior rather than fighting it:
    # numeric strings coerce. This pin makes S2's strictness decision explicit.
    s = default_settings()
    s["safety"]["maxtemp"] = "550"
    dumped = SettingsSchema.model_validate(s).model_dump(mode="json")
    assert dumped["safety"]["maxtemp"] == 550


def test_all_sections_are_modeled():
    # No top-level section may be passing through extra="allow" anymore.
    modeled = set(SettingsSchema.model_fields.keys())
    assert modeled == set(default_settings().keys())


# ---------------------------------------------------------------------------
# Migration-fixture parity (spec deliverable 2): an OLD-shaped settings.json,
# once carried through the full read_settings_file() migration pipeline
# (upgrade_settings() plus the post-upgrade deep_update() overlay that
# backfills anything the upgrade blocks themselves didn't touch -- see
# common/settings_migration.py:108), must still validate through the schema
# and round-trip exactly. This is deliberately NOT a raw call to
# upgrade_settings() alone: that function's return value is documented (by
# the deep_update() overlay that always follows it in read_settings_file())
# as allowed to be a partial dict -- e.g. a v1.4 install's migrated
# cycle_data only touches SmokeOnCycleTime/SmokeOffCycleTime, leaving fields
# like PMode/u_min/u_max absent -- so asserting parity directly against it
# would be asserting a property the real code never relies on. The full
# pipeline is what an actual upgrade ends up persisting.
# ---------------------------------------------------------------------------


@pytest.fixture
def _migration_env(tmp_path, monkeypatch):
    """Test-isolated datastore + backups dir, so read_settings_file()'s
    upgrade path (which calls backup_settings()) never touches the shared
    datastore or the real ./backups/ dir. Mirrors the `fresh`/
    `real_backups_dir` fixtures in tests/unit/common/test_settings_migration.py.
    """
    monkeypatch.setenv("PIFIRE_DB_PATH", str(tmp_path / "t.db"))
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    backups_path = tmp_path / "backups"
    backups_path.mkdir()
    backups_path_str = str(backups_path) + os.sep
    monkeypatch.setattr("common.settings_migration.BACKUP_PATH", backups_path_str)
    monkeypatch.setattr("common.backups.BACKUP_PATH", backups_path_str)
    yield tmp_path
    datastore._reset_for_tests(None)


def test_migrated_ancient_settings_round_trip(_migration_env):
    """A v1.4.x-or-earlier settings.json, migrated by the real
    read_settings_file() pipeline, still validates through SettingsSchema
    and round-trips exactly -- the same v1.4 cascade shape exercised by
    test_settings_migration.py's test_upgrade_settings_v1_4_cascade_* tests,
    but with realistic per-service notify dicts (rather than that test
    suite's synthetic {"legacy_marker": ...} placeholders) so the migrated
    notify_services shape is complete, matching a real upgrade.
    """
    d = default_settings()
    datastore_accessors.write_settings_store(d)

    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.4.0", "build": 0}
    old["startup"] = {"start_to_mode": {}}
    old["start_to_mode"] = {"grill1_setpoint": 225}
    for key, value in d["notify_services"].items():
        old[key] = copy.deepcopy(value)
    del old["notify_services"]
    old["probe_settings"]["probe_options"] = {"x": 1}
    old["probe_settings"]["probe_sources"] = {"x": 1}
    old["probe_settings"]["probes_enabled"] = {"x": 1}
    old["modules"]["adc"] = "mcp3008"
    old["cycle_data"] = {"SmokeCycleTime": 30, "HoldCycleTime": 25}

    p = _migration_env / "settings.json"
    p.write_text(json.dumps(old))

    migrated = read_settings_file(filename=str(p), init=True)

    assert_parity(migrated)


# ---------------------------------------------------------------------------
# Drift test (Task 3): schema committed to web-react/schema/settings.schema.json
# must match export_schema() at all times. Flags unintended schema drift.
# ---------------------------------------------------------------------------


from pathlib import Path

from common.settings_schema import export_schema

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "web-react" / "schema" / "settings.schema.json"


def test_committed_schema_is_current():
    """Fails when models changed but web-react/schema/settings.schema.json
    wasn't regenerated (uv run python -m common.settings_schema > ...)."""
    committed = json.loads(SCHEMA_PATH.read_text())
    assert committed == export_schema()
