"""Parity: the pydantic shadow models must round-trip default_settings() exactly.

extra="allow" means sections not yet modeled pass through untouched, so this
test is meaningful from the first section onward and total as of Task 2 (all
21 top-level sections are now modeled).
"""

import copy
import json
import os

import pytest
from pydantic import BaseModel

from common import datastore, datastore_accessors
from common.defaults import default_settings
from common.settings_migration import read_settings_file
from common.settings_schema import (
    LastUpdated,
    NotifyServices,
    OneSignalService,
    ServerInfo,
    SettingsSchema,
    Versions,
)


def assert_parity(settings: dict) -> None:
    # by_alias=True: platform.system.one_wire must dump back out as "1WIRE"
    # (its on-disk/defaults.py key) -- mirrors validate_settings_tree()'s dump.
    dumped = SettingsSchema.model_validate(settings).model_dump(mode="json", by_alias=True)
    assert dumped == settings


def test_default_settings_round_trips():
    assert_parity(default_settings())


def test_extra_keys_survive():
    s = default_settings()
    s["safety"]["future_knob"] = 42
    s["totally_new_section"] = {"a": 1}
    assert_parity(s)


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


def _migrate_ancient_settings(migration_env):
    """Build a v1.4.x-or-earlier settings.json fixture and carry it through
    the real read_settings_file() migration pipeline, returning the
    resulting dict. Shared by the round-trip test below and the
    extras-drift walk (test_documented_extras_allowlist_migrated_ancient)
    so both exercise an identical migrated-tree shape.
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

    p = migration_env / "settings.json"
    p.write_text(json.dumps(old))

    return read_settings_file(filename=str(p), init=True)


def test_migrated_ancient_settings_round_trip(_migration_env):
    """A v1.4.x-or-earlier settings.json, migrated by the real
    read_settings_file() pipeline, still validates through SettingsSchema
    and round-trips exactly -- the same v1.4 cascade shape exercised by
    test_settings_migration.py's test_upgrade_settings_v1_4_cascade_* tests,
    but with realistic per-service notify dicts (rather than that test
    suite's synthetic {"legacy_marker": ...} placeholders) so the migrated
    notify_services shape is complete, matching a real upgrade.
    """
    migrated = _migrate_ancient_settings(_migration_env)

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


# ---------------------------------------------------------------------------
# Final-review Finding 1a: extras allowlist. extra="allow" means any unmodeled
# key in defaults.py silently rides through every nested model's
# __pydantic_extra__ -- this closes that mesh by walking the FULL validated
# tree and asserting the only extras captured are the 2 deliberate,
# comment-documented Primary-only setpoint colors (settings_schema.py: the
# ProbeChartConfig comment above):
#   - history_page.probe_config.<label> carries bg_color_setpoint/
#     line_color_setpoint (defaults.py:329-331) on Primary probes only.
# (Task 2 promoted platform.system's "1WIRE" to a real aliased field --
# one_wire = Field(alias="1WIRE") on _SystemConfig -- so it no longer rides
# through extra="allow" and has left this list.)
# A NEW unmodeled key anywhere in defaults.py must fail this test; that claim
# is proven (not just asserted) by test_extras_walker_flags_unmodeled_key
# below, which injects a synthetic extra into a COPY of the input and shows
# the walker catches it.
# ---------------------------------------------------------------------------

# Dynamic dict keys (probe labels, controller names, etc.) are normalized to
# "*" in the collected path so the assertion is stable regardless of which
# probes/labels happen to exist in defaults.py.
DOCUMENTED_EXTRAS = {
    ("history_page.probe_config.*", "bg_color_setpoint"),
    ("history_page.probe_config.*", "line_color_setpoint"),
}


def _collect_extras(value, path: str = "") -> set[tuple[str, str]]:
    """Recursively walk a validated pydantic model (or plain list/dict
    container reachable from one), collecting {(dotted_path, key)} for every
    key captured by a nested model's extra="allow" fallthrough.
    """
    found: set[tuple[str, str]] = set()
    if isinstance(value, BaseModel):
        extra = value.__pydantic_extra__ or {}
        for key in extra:
            found.add((path, key))
        for field_name in type(value).model_fields:
            child_path = f"{path}.{field_name}" if path else field_name
            found |= _collect_extras(getattr(value, field_name), child_path)
    elif isinstance(value, dict):
        for v in value.values():
            found |= _collect_extras(v, f"{path}.*" if path else "*")
    elif isinstance(value, (list, tuple)):
        for v in value:
            found |= _collect_extras(v, f"{path}[]")
    return found


def test_documented_extras_allowlist():
    """The only extras anywhere in the validated default tree are the 3
    deliberate, documented ones -- no more, no fewer."""
    model = SettingsSchema.model_validate(default_settings())
    assert _collect_extras(model) == DOCUMENTED_EXTRAS


def test_extras_walker_flags_unmodeled_key():
    """Negative proof for test_documented_extras_allowlist: the walker must
    catch a brand-new unmodeled key. Inject a synthetic extra into a COPY of
    the input (defaults.py itself is untouched) and show it shows up in the
    collected set and is NOT part of the documented allowlist."""
    s = copy.deepcopy(default_settings())
    s["safety"]["totally_unmodeled_future_knob"] = "surprise"
    model = SettingsSchema.model_validate(s)
    found = _collect_extras(model)
    assert ("safety", "totally_unmodeled_future_knob") in found
    assert found - DOCUMENTED_EXTRAS == {("safety", "totally_unmodeled_future_knob")}
    assert found != DOCUMENTED_EXTRAS


def test_documented_extras_allowlist_migrated_ancient(_migration_env):
    """Widen the extras-drift guard to a migration-fixture tree, not just
    default_settings(): a v1.4.x-or-earlier install migrated by the real
    read_settings_file() pipeline must leave behind ONLY the documented
    ProbeChartConfig extras -- no more, no fewer -- the same walker and
    allowlist as test_documented_extras_allowlist, just aimed at
    _migrate_ancient_settings()'s output instead of the static default tree.

    This is the regression guard for the stray top-level notify-service-key
    migration bug (common/settings_migration.py's v1.4 cascade copied each
    service's dict into notify_services.<service> but historically never
    popped the old top-level key, so it rode through extra="allow"
    indefinitely -- see
    test_settings_migration.py::test_upgrade_settings_v1_4_cascade_migrates_notify_probe_and_platform).
    Had that pop regressed, this test would fail with 8 additional
    ("", "<service>") entries (apprise/ifttt/pushbullet/pushover/onesignal/
    influxdb/mqtt/wled) -- one per notify_services key -- on top of
    DOCUMENTED_EXTRAS.
    """
    migrated = _migrate_ancient_settings(_migration_env)
    model = SettingsSchema.model_validate(migrated)
    assert _collect_extras(model) == DOCUMENTED_EXTRAS


# ---------------------------------------------------------------------------
# Final-review Finding 1b: defaults-instantiation parity. Closes the other
# open mesh -- a model field could carry a default that has silently drifted
# from defaults.py's static value while both test_default_settings_round_trips
# (validates FROM defaults.py, so an inline model default is never
# exercised -- input always supplies the value) and the JSON-schema drift
# test (schema-shape only, doesn't care about default *values*) stay green.
#
# Construct SettingsSchema supplying ONLY the fields that have no default
# anywhere in the model tree (versions.*, server_info.uuid, lastupdated.time,
# notify_services.onesignal.uuid), dump it, and deep-compare against
# default_settings() with the dynamic/generated zones masked out on BOTH
# sides. Every remaining field in the comparison is therefore populated by an
# inline pydantic default, making each one load-bearing.
# ---------------------------------------------------------------------------

# (dotted path to delete, one-line reason it can't be part of a static-default
# comparison)
MASKED_PATHS = [
    (
        "versions",
        "dummy required values supplied for construction; real values are read "
        "live from updater/updater_manifest.json on every default_settings() "
        "call, not a static per-model default",
    ),
    (
        "server_info.uuid",
        "dummy required value supplied; real uuid is generated fresh per-install by generate_uuid()",
    ),
    (
        "lastupdated.time",
        "dummy required value supplied; real value is a live timestamp (math.trunc(time.time())) set at build time",
    ),
    (
        "notify_services.onesignal.uuid",
        "dummy required value supplied; real uuid is generated fresh by generate_uuid() in default_notify_services()",
    ),
    (
        "controller.config",
        "dynamic: populated per-controller from controller/controllers.json "
        "manifest option defaults, not a static per-model default",
    ),
    (
        "dashboard.dashboards",
        "dynamic: built from dashboard/*.json metadata files by _default_dashboard()",
    ),
    (
        "display.config",
        "dynamic: built from wizard/wizard_manifest.json display metadata per module",
    ),
    (
        "history_page.probe_config",
        "dynamic: generated per discovered probe by default_probe_config(), driven by probe_settings.probe_map",
    ),
    (
        "probe_settings.probe_map",
        "dynamic: hardware/profile-derived probe discovery (probe_devices / probe_info)",
    ),
    (
        "probe_settings.probe_profiles",
        "dynamic: loaded from probes/probes.json plus any user-added profiles",
    ),
    (
        "recipe.probe_map",
        "dynamic: derived from probe_settings.probe_map by _default_recipe_probe_map()",
    ),
]


def _delete_path(d: dict, dotted: str) -> None:
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur[p]
    cur.pop(parts[-1], None)


def _masked_defaults_instantiation_diff():
    """Builds SettingsSchema from only its required-no-default fields, dumps
    it, and returns (actual, expected) with every path in MASKED_PATHS
    deleted from both. Shared by the passing test and its mutation proof."""
    model = SettingsSchema(
        versions=Versions(server="dummy", cookfile="dummy", recipe="dummy", build=0),
        server_info=ServerInfo(uuid="dummy-uuid"),
        lastupdated=LastUpdated(time=0),
        notify_services=NotifyServices(onesignal=OneSignalService(uuid="dummy-onesignal-uuid")),
    )
    # by_alias=True: platform.system.one_wire must dump as "1WIRE" so it
    # matches default_settings()'s on-disk key (see assert_parity's comment).
    actual = model.model_dump(mode="json", by_alias=True)
    expected = default_settings()
    for path, _reason in MASKED_PATHS:
        _delete_path(actual, path)
        _delete_path(expected, path)
    return actual, expected


def test_defaults_instantiation_parity():
    """Makes every inline model default in the SettingsSchema tree
    load-bearing: proven by manually flipping SafetySettings.maxtemp from 550
    to 555, confirming this test fails, then reverting (see final-review fix
    report for the paste of both runs)."""
    actual, expected = _masked_defaults_instantiation_diff()
    assert actual == expected


# ---------------------------------------------------------------------------
# Task S2.1: strict validator entry point + partial model. Enforcement itself
# is a LATER task -- these pin the OBSERVED strict-mode semantics of
# validate_settings_tree() / SettingsSchema.model_validate(strict=True) so a
# pydantic upgrade that changes behavior is caught here, not silently at the
# (future) enforcement call site.
# ---------------------------------------------------------------------------

from common.settings_schema import (
    PartialSettingsSchema,
    SettingsValidationError,
    validate_partial_settings,
    validate_settings_tree,
)


def test_strict_string_for_int_rejects():
    s = default_settings()
    s["safety"]["maxtemp"] = "550"
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("safety.maxtemp" in msg for msg in ei.value.errors)


def test_strict_int_widens_to_float():
    s = default_settings()
    s["cycle_data"]["u_min"] = 0  # int into a float field — pydantic strict allows widening
    validate_settings_tree(s)  # must not raise


def test_strict_bool_for_int_rejects():
    s = default_settings()
    s["safety"]["reigniteretries"] = True
    with pytest.raises(SettingsValidationError):
        validate_settings_tree(s)


def test_unknown_keys_still_allowed_under_strict():
    s = default_settings()
    s["safety"]["future_knob"] = 42
    validate_settings_tree(s)


def test_validate_returns_normalized_dump():
    s = default_settings()
    out = validate_settings_tree(s)
    assert out == s  # parity holds through the strict path on a clean tree


def test_partial_model_accepts_sparse_delta_and_rejects_bad_field():
    PartialSettingsSchema.model_validate({"safety": {"maxtemp": 500}}, strict=True)
    with pytest.raises(Exception):  # pydantic ValidationError from the partial
        PartialSettingsSchema.model_validate({"safety": {"maxtemp": "500"}}, strict=True)


# ---------------------------------------------------------------------------
# Final-review fix: Layer 1 (validate_partial_settings, used by the API's
# _api_post_settings_update) must report precise FIELD-level errors but must
# NOT enforce cross-field/cross-section model_validator rules -- those run
# against absent sections' STATIC DEFAULTS on a sparse delta and can falsely
# reject a delta that's valid against the store's real merged tree (e.g.
# pwm.min_duty_cycle lowered via PwmTab, then a bare startup.pwm_duty_cycle
# delta checked against the *default* min_duty_cycle=20 instead). Layer 2
# (validate_settings_tree() on the merged tree, see
# tests/web/test_api_settings_update.py) remains the sole authority for
# cross-field rules.
# ---------------------------------------------------------------------------


def test_partial_validate_settings_ignores_cross_field_rule_on_absent_section():
    # pwm's default min_duty_cycle is 20 -- SettingsSchema.
    # _check_startup_pwm_duty_cycle would reject 15 if it ran here against
    # that default, even though the store may have pwm.min_duty_cycle
    # legitimately lowered (e.g. to 10) making 15 valid on the real tree.
    assert validate_partial_settings({"startup": {"pwm_duty_cycle": 15}}) == []


def test_partial_validate_settings_still_reports_field_level_errors():
    errors = validate_partial_settings({"safety": {"maxtemp": "nope"}})
    assert any("safety.maxtemp" in e for e in errors)


def test_partial_validate_settings_mixed_delta_keeps_field_error_drops_cross_field_error():
    # One delta tripping both a genuine field-level error (safety.maxtemp,
    # wrong type) and what would have been a cross-field error on an absent
    # section's default (startup.pwm_duty_cycle) -- only the field-level
    # error should survive filtering.
    errors = validate_partial_settings({"safety": {"maxtemp": "nope"}, "startup": {"pwm_duty_cycle": 15}})
    assert len(errors) == 1
    assert "safety.maxtemp" in errors[0]


def test_partial_validate_settings_rejects_structurally_bad_section():
    # A section replaced with a scalar is a genuine (non-cross-field) shape
    # error and must still be reported.
    errors = validate_partial_settings({"safety": 5})
    assert any("safety" in e for e in errors)


# ---------------------------------------------------------------------------
# Task S2.2: legacy clamps migrated to schema constraints (Field ge/le and
# cross-field model_validators), one representative pin per constraint found
# in Step 1's enumeration (see the Task 2 report for the full source-line
# list). Every constraint here traces to an actual clamp/invariant found in
# blueprints/settings/routes.py or the React settings tabs -- no new limits
# invented.
# ---------------------------------------------------------------------------


def test_prime_on_startup_range_rejects():
    # Clamp source: blueprints/settings/routes.py:479-483.
    s = default_settings()
    s["startup"]["prime_on_startup"] = 999
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("prime_on_startup" in m for m in ei.value.errors)


def test_sleep_timeout_rejects_negative():
    # Clamp source: blueprints/settings/routes.py:15.
    s = default_settings()
    s["display"]["sleep_timeout"] = -1
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("sleep_timeout" in m for m in ei.value.errors)


def test_wled_notify_duration_rejects_negative():
    # Clamp source: blueprints/settings/routes.py:179-180.
    s = default_settings()
    s["notify_services"]["wled"]["notify_duration"] = -1
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("notify_duration" in m for m in ei.value.errors)


def test_wled_idle_brightness_rejects_out_of_range():
    # Clamp source: blueprints/settings/routes.py:191-194.
    s = default_settings()
    s["notify_services"]["wled"]["suggested_config"]["idle_brightness"] = 0
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("idle_brightness" in m for m in ei.value.errors)


def test_wled_led_count_rejects_out_of_range():
    # Clamp source: blueprints/settings/routes.py:195-198.
    s = default_settings()
    s["notify_services"]["wled"]["suggested_config"]["led_count"] = 1001
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("led_count" in m for m in ei.value.errors)


def test_wled_profile_number_rejects_out_of_range():
    # Clamp source: blueprints/settings/routes.py:212-216.
    s = default_settings()
    s["notify_services"]["wled"]["profile_numbers"]["idle"] = 251
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("profile_numbers" in m and "idle" in m for m in ei.value.errors)


def test_smartstart_profile_count_invariant():
    # Clamp source: RangeProfileTable's boundaries+1 construction invariant.
    s = default_settings()
    s["startup"]["smartstart"]["profiles"] = s["startup"]["smartstart"]["profiles"][:-1]
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("profiles must have exactly one more entry than temp_range_list" in m for m in ei.value.errors)


def test_smartstart_profile_field_bounds_reject():
    # Clamp source: web-react StartupTab.tsx RangeProfileTable columns
    # (startuptime 30-1200 / augerontime 1-60 / p_mode 0-9).
    s = default_settings()
    s["startup"]["smartstart"]["profiles"][0]["startuptime"] = 29
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("startuptime" in m for m in ei.value.errors)


def test_pwm_profile_count_invariant():
    # Clamp source: RangeProfileTable's boundaries+1 construction invariant
    # (PwmTab.tsx).
    s = default_settings()
    s["pwm"]["profiles"] = s["pwm"]["profiles"][:-1]
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("profiles must have exactly one more entry than temp_range_list" in m for m in ei.value.errors)


def test_pwm_profile_duty_cycle_must_be_within_min_max():
    # Clamp source: PwmTab.tsx RangeProfileTable column min/max = pwm's own
    # min_duty_cycle/max_duty_cycle (cross-field within PwmSettings).
    s = default_settings()
    s["pwm"]["profiles"][0]["duty_cycle"] = s["pwm"]["min_duty_cycle"] - 1
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("profiles[0].duty_cycle must be within" in m for m in ei.value.errors)


def test_pwm_duty_cycle_must_be_within_min_max():
    # Clamp source: blueprints/settings/routes.py:493-497 (cross-SECTION:
    # startup.pwm_duty_cycle vs. pwm.min_duty_cycle/max_duty_cycle).
    s = default_settings()
    s["startup"]["pwm_duty_cycle"] = 10  # below min_duty_cycle=20
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("startup.pwm_duty_cycle must be within" in m for m in ei.value.errors)


# ---------------------------------------------------------------------------
# Task S2.2: 1WIRE promotion. platform.system's "1WIRE" is now a real,
# aliased field (one_wire = Field(alias="1WIRE"), populate_by_name=True on
# _SystemConfig) instead of an extra="allow" pass-through.
# ---------------------------------------------------------------------------


def test_one_wire_key_is_now_a_real_field():
    s = default_settings()
    validate_settings_tree(s)  # 1WIRE arrives via alias, no longer an extra
    model = SettingsSchema.model_validate(s)
    assert ("platform.system", "1WIRE") not in _collect_extras(model)


def test_partial_schema_accepts_one_wire_by_alias_strict():
    # CRITICAL re-verify (Task 2 brief): the recursive partial model has
    # never been exercised with a Field(alias=...) before -- confirm it still
    # populates correctly by alias under strict=True.
    PartialSettingsSchema.model_validate({"platform": {"system": {"1WIRE": 4}}}, strict=True)
