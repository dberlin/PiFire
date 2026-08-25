"""Parity: the pydantic shadow models must round-trip default_settings() exactly.

`_Section.model_config` is `extra="forbid"`: raw `SettingsSchema.model_validate()`
REJECTS any unmodeled key outright (see test_extra_keys_rejected_by_raw_model_validate).
The write-time gate, `validate_settings_tree()`, wraps that with a
self-healing repair pass -- see the "extra=\"forbid\" self-healing repair
behavior" section near the bottom of this file -- so a stray/legacy/future key
never permanently blocks a settings save; it's stripped, logged, and the
write proceeds.
"""

import copy
import json
import os

import pytest
from pydantic import BaseModel, ValidationError

from common import datastore
from common.defaults import default_settings
from common.persistence import runtime as runtime_persistence
from common.settings_migration import read_settings_file
from common.settings_schema import (
    LastUpdated,
    NotifyServices,
    OneSignalService,
    ServerInfo,
    SettingsSchema,
    Versions,
)
from common.web_contracts.export import SCHEMA_DIRECTORY, render_contract_artifacts
from common.web_contracts.registry import WEB_ROOT_CONTRACTS
from tests import conftest
from common.web_contracts.settings import (
    ModeResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
)


def assert_parity(settings: dict) -> None:
    # by_alias=True: platform.system.one_wire must dump back out as "1WIRE"
    # (its on-disk/defaults.py key) -- mirrors validate_settings_tree()'s dump.
    dumped = SettingsSchema.model_validate(settings).model_dump(mode="json", by_alias=True)
    assert dumped == settings


def test_default_settings_round_trips():
    assert_parity(default_settings())


def test_thermocouple_health_defaults_match_schema_and_round_trip():
    settings = default_settings()

    assert settings["thermocouple_health"] == {"inference_policy": "observe"}

    model = SettingsSchema.model_validate(settings, strict=True)
    assert model.thermocouple_health.model_dump(mode="json") == {"inference_policy": "observe"}
    assert model.model_dump(mode="json", by_alias=True) == settings


@pytest.mark.parametrize("policy", ["off", "observe", "enforce"])
def test_thermocouple_inference_policy_accepts_every_supported_value(policy):
    settings = default_settings()
    settings["thermocouple_health"]["inference_policy"] = policy

    model = SettingsSchema.model_validate(settings, strict=True)

    assert model.thermocouple_health.inference_policy == policy


def test_thermocouple_inference_policy_rejects_arbitrary_strings():
    settings = default_settings()
    settings["thermocouple_health"]["inference_policy"] = "sometimes"

    with pytest.raises(ValidationError):
        SettingsSchema.model_validate(settings, strict=True)


def test_settings_response_validates_the_live_settings_envelope():
    payload = {"settings": default_settings()}

    response = SettingsResponse.model_validate(payload, strict=True)

    assert response.model_dump(mode="json", by_alias=True) == payload


def test_settings_update_request_preserves_sparse_merge_patch_and_flags():
    payload = {
        "settings": {"notify_services": {"onesignal": {"devices": {"retired": None}}}},
        "flags": ["settings_update", "controller_update"],
    }

    request = SettingsUpdateRequest.model_validate(payload)

    assert request.model_dump(mode="json") == payload


def test_settings_update_request_rejects_unknown_flags():
    with pytest.raises(ValidationError):
        SettingsUpdateRequest.model_validate({"settings": {}, "flags": ["mode"]})


def test_settings_update_responses_validate_success_and_field_errors():
    settings = default_settings()
    success = {
        "result": "success",
        "message": "Settings updated.",
        "errors": [],
        "data": settings,
    }
    rejected = {
        "result": "error",
        "message": "Settings update failed: safety.maxtemp: Input should be a valid integer",
        "errors": [{"path": "safety.maxtemp", "message": "Input should be a valid integer"}],
        "data": {},
    }

    assert SettingsUpdateResponse.model_validate(success, strict=True).model_dump(mode="json", by_alias=True) == success
    assert (
        SettingsUpdateResponse.model_validate(rejected, strict=True).model_dump(mode="json", by_alias=True) == rejected
    )


def test_mode_response_preserves_the_command_envelope():
    payload = {"result": "OK", "message": "", "data": {"mode": "Stop"}}

    assert ModeResponse.model_validate(payload, strict=True).model_dump(mode="json") == payload


def test_extra_keys_rejected_by_raw_model_validate():
    """`_Section` is `extra="forbid"`. Raw `SettingsSchema.model_validate()`
    (i.e. NOT going through the write_settings() gate's repair wrapper) rejects
    unknown keys outright -- self-healing lives ONLY in
    validate_settings_tree(), see the repair-wrapper tests near the bottom of
    this file."""
    s = default_settings()
    s["safety"]["future_knob"] = 42
    with pytest.raises(ValidationError):
        SettingsSchema.model_validate(s, strict=True)

    s2 = default_settings()
    s2["totally_new_section"] = {"a": 1}
    with pytest.raises(ValidationError):
        SettingsSchema.model_validate(s2, strict=True)


def test_all_sections_are_modeled():
    # No top-level section may be passing through extra="allow" anymore.
    modeled = set(SettingsSchema.model_fields.keys())
    assert modeled == set(default_settings().keys())


def test_a_fresh_tree_is_stamped_with_the_current_shape_version():
    """A tree the defaults just built needs no migration, so it starts at
    CURRENT -- the same reasoning as _ensure_schema's `0 <` guards."""
    from common.settings_schema import SETTINGS_SCHEMA_VERSION

    assert default_settings()["schema_version"] == SETTINGS_SCHEMA_VERSION


# These three are deliberately NOT parametrized: each docstring records why
# that specific setting was retired and what replaced it. A shared body would
# delete the only place that reasoning is written down.
def test_current_schema_omits_retired_fan_pid_setting():
    settings = default_settings()

    assert "FanPidEnabled" not in settings["cycle_data"]
    assert "FanPidEnabled" not in SettingsSchema.model_fields["cycle_data"].annotation.model_fields


def test_current_schema_omits_retired_u_min_setting():
    """u_min had no reader: the duty floor is pulse_s/frame_s, from the pulse
    geometry (AUGER_TIMING in grillplat/actuator_capabilities.py), not from a
    configured ratio."""
    settings = default_settings()

    assert "u_min" not in settings["cycle_data"]
    assert "u_min" not in SettingsSchema.model_fields["cycle_data"].annotation.model_fields


def test_current_schema_omits_retired_hold_cycle_time_setting():
    """HoldCycleTime never set the hold cycle: Hold paces the auger from
    PulseScheduler's frame (AUGER_TIMING in grillplat/actuator_capabilities.py),
    and pid_sp's three-cycle guards read that same frame."""
    settings = default_settings()

    assert "HoldCycleTime" not in settings["cycle_data"]
    assert "HoldCycleTime" not in SettingsSchema.model_fields["cycle_data"].annotation.model_fields


def test_the_shape_version_is_not_derived_from_the_release_version():
    """schema_version must never move because a release number moved."""
    from common.settings_schema import SETTINGS_SCHEMA_VERSION

    settings = default_settings()
    settings["versions"]["build"] = settings["versions"]["build"] + 1000
    settings["versions"]["server"] = "9.9.9"

    assert settings["schema_version"] == SETTINGS_SCHEMA_VERSION


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
    runtime_persistence.write_settings_store(d)

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
    old["cycle_data"] = {"SmokeCycleTime": 30}

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
# Drift test: schema committed to web-react/schema/settings.schema.json
# must match the registered exporter, including deterministic TypeScript
# ownership metadata derived from the Pydantic schema.
# ---------------------------------------------------------------------------


from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "web-react" / "schema" / "settings.schema.json"


def test_committed_schema_is_current():
    """Fails when the registered exporter has not regenerated settings schema."""
    artifact = next(item for item in WEB_ROOT_CONTRACTS if item.name == "settings")
    generated = render_contract_artifacts()[SCHEMA_DIRECTORY / artifact.schema_output]

    assert SCHEMA_PATH.read_bytes() == generated


def test_settings_schema_is_registered_at_its_established_direct_root_path():
    artifact = next(item for item in WEB_ROOT_CONTRACTS if item.name == "settings")

    assert artifact.model is SettingsSchema
    assert artifact.schema_output == "../settings.schema.json"
    assert artifact.typescript_output == "../settings/settingsTypes.gen.ts"


# ---------------------------------------------------------------------------
# Extras allowlist: under extra="allow", any unmodeled key in defaults.py
# would silently ride through every nested model's __pydantic_extra__ -- this
# closes that mesh by walking the FULL validated tree and asserting no
# undocumented extras exist anywhere.
# platform.system's "1WIRE" is now a real aliased field -- one_wire =
# Field(alias="1WIRE") on _SystemConfig -- so it no longer rides through
# extra="allow" and has left this list. ProbeChartConfig's Primary-only
# setpoint colors -- bg_color_setpoint/line_color_setpoint (defaults.py:
# 329-331) -- were promoted to real optional fields for the same reason,
# leaving the allowlist EMPTY: the default tree now has zero live extras.
# A NEW unmodeled key anywhere in defaults.py must fail this test; that claim
# is proven (not just asserted) by test_extras_walker_flags_unmodeled_key
# below, which injects a synthetic extra into a COPY of the input and shows
# the walker catches it.
# ---------------------------------------------------------------------------

# Dynamic dict keys (probe labels, controller names, etc.) are normalized to
# "*" in the collected path so the assertion is stable regardless of which
# probes/labels happen to exist in defaults.py.
DOCUMENTED_EXTRAS: set[tuple[str, str]] = set()


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
    """No extras anywhere in the validated default tree -- DOCUMENTED_EXTRAS
    is empty (the last live extras were promoted to real fields, and the
    extra="forbid" flip means a model with any undeclared key can no longer
    even construct, so __pydantic_extra__ is always empty for any
    successfully-validated model -- this walk is now a belt-and-suspenders
    check, not the primary enforcement)."""
    model = SettingsSchema.model_validate(default_settings())
    assert _collect_extras(model) == DOCUMENTED_EXTRAS == set()


def test_unmodeled_key_now_rejected_at_construction_not_walked_as_extra():
    """Negative proof for test_documented_extras_allowlist: under the old
    extra="allow" config, an unmodeled key silently became a walkable
    __pydantic_extra__ entry the walker could catch after the fact -- see
    test_extra_keys_rejected_by_raw_model_validate. Under extra="forbid",
    SettingsSchema.model_validate() itself rejects the key at construction
    time, before any walk could ever run: the walker's drift-catching job is
    now subsumed by strict construction rejecting the key outright, which is
    the stronger guarantee."""
    s = copy.deepcopy(default_settings())
    s["safety"]["totally_unmodeled_future_knob"] = "surprise"
    with pytest.raises(ValidationError) as ei:
        SettingsSchema.model_validate(s)
    assert "totally_unmodeled_future_knob" in str(ei.value)


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
# Defaults-instantiation parity. Closes the other
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
        (
            "dummy required values supplied for construction; real values are read "
            "live from updater/updater_manifest.json on every default_settings() "
            "call, not a static per-model default"
        ),
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
        (
            "dynamic: populated per-controller from controller/controllers.json "
            "manifest option defaults, not a static per-model default"
        ),
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
    #  The SHIPPED defaults, not the ones this suite runs on: tests/conftest.py
    #  rebinds default_settings to answer `real_hw` False, and what this test is
    #  about is whether the pydantic tree still mirrors what an installation
    #  gets. Reading the rebound one would compare the schema against the test
    #  harness and call the disagreement a schema drift.
    expected = conftest.shipped_default_settings()
    for path, _reason in MASKED_PATHS:
        _delete_path(actual, path)
        _delete_path(expected, path)
    return actual, expected


def test_defaults_instantiation_parity():
    """Makes every inline model default in the SettingsSchema tree
    load-bearing: proven by manually flipping SafetySettings.maxtemp from 550
    to 555, confirming this test fails, then reverting."""
    actual, expected = _masked_defaults_instantiation_diff()
    assert actual == expected


# ---------------------------------------------------------------------------
# Strict validator entry point + partial model: these pin the OBSERVED
# strict-mode semantics of validate_settings_tree() /
# SettingsSchema.model_validate(strict=True) so a pydantic upgrade that
# changes behavior is caught here, not silently at the enforcement call site.
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


def test_unknown_keys_repaired_not_rejected_under_strict():
    # Under extra="forbid", this no longer "passes through" -- it's stripped
    # by validate_settings_tree()'s repair wrapper (see the dedicated section
    # below), but the call still must not raise.
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
# Layer 1 (validate_partial_settings, used by the API's
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
# Legacy clamps migrated to schema constraints (Field ge/le and cross-field
# model_validators), one representative pin per constraint. Every constraint
# here traces to an actual clamp/invariant found in
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


def _set_nested(tree, path, value):
    node = tree
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "expected_substrings"),
    [
        # Clamp source: blueprints/settings/routes.py:191-194.
        (("notify_services", "wled", "suggested_config", "idle_brightness"), 0, ("idle_brightness",)),
        # Clamp source: blueprints/settings/routes.py:195-198.
        (("notify_services", "wled", "suggested_config", "led_count"), 1001, ("led_count",)),
        # Clamp source: blueprints/settings/routes.py:212-216.
        (("notify_services", "wled", "profile_numbers", "idle"), 251, ("profile_numbers", "idle")),
    ],
    ids=["idle_brightness", "led_count", "profile_number"],
)
def test_wled_field_rejects_out_of_range(path, value, expected_substrings):
    s = default_settings()
    _set_nested(s, path, value)
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any(all(sub in m for sub in expected_substrings) for m in ei.value.errors)


def test_smartstart_profile_count_invariant():
    # Clamp source: RangeProfileTable's boundaries+1 construction invariant.
    s = default_settings()
    s["startup"]["smartstart"]["profiles"] = s["startup"]["smartstart"]["profiles"][:-1]
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("profiles must have exactly one more entry than temp_range_list" in m for m in ei.value.errors)


def test_smartstart_profile_field_bounds_reject():
    # Clamp source: the Flask smartstart profile forms in
    # blueprints/settings/templates/settings/index.html
    # (startuptime 30-1200 / augerontime 1-1000 / p_mode 0-9).
    s = default_settings()
    s["startup"]["smartstart"]["profiles"][0]["startuptime"] = 29
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("startuptime" in m for m in ei.value.errors)


def test_smartstart_augerontime_accepts_the_full_flask_range():
    """augerontime's ceiling is 1000, matching the Flask form's max.

    A 60 s ceiling would reject settings a Flask user can legitimately save,
    so a value above 60 must validate.
    """
    s = default_settings()
    s["startup"]["smartstart"]["profiles"][0]["augerontime"] = 1000
    validate_settings_tree(s)

    s["startup"]["smartstart"]["profiles"][0]["augerontime"] = 1001
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("augerontime" in m for m in ei.value.errors)


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
# 1WIRE promotion: platform.system's "1WIRE" is now a real, aliased field
# (one_wire = Field(alias="1WIRE"), populate_by_name=True on _SystemConfig)
# instead of an extra="allow" pass-through.
# ---------------------------------------------------------------------------


def test_one_wire_key_is_now_a_real_field():
    s = default_settings()
    validate_settings_tree(s)  # 1WIRE arrives via alias, no longer an extra
    model = SettingsSchema.model_validate(s)
    assert ("platform.system", "1WIRE") not in _collect_extras(model)


def test_partial_schema_accepts_one_wire_by_alias_strict():
    # CRITICAL re-verify: the recursive partial model has never been
    # exercised with a Field(alias=...) before -- confirm it still populates
    # correctly by alias under strict=True.
    PartialSettingsSchema.model_validate({"platform": {"system": {"1WIRE": 4}}}, strict=True)


# ---------------------------------------------------------------------------
# extra="forbid" self-healing repair behavior.
#
# _Section.model_config is ConfigDict(extra="forbid") -- raw
# model_validate() rejects any unmodeled key (see
# test_extra_keys_rejected_by_raw_model_validate above). But validate_settings_tree()
# (the write_settings() gate, ``common.persistence.runtime.write_settings``) wraps that
# with a repair pass: if EVERY error a failed validation produces is
# "extra_forbidden", it strips exactly those dotted paths from a COPY of the
# input, logs each stripped path via common.common.write_log, and retries
# ONCE. Any OTHER error type (bad value, missing required field, cross-field
# model_validator failure) -- alone or mixed in with an extra key -- must
# still raise SettingsValidationError exactly as before; repair only ever
# masks the "stray/legacy/future key" failure mode, never a real bug.
# ---------------------------------------------------------------------------

import common.settings_schema as settings_schema_module


@pytest.fixture
def _captured_write_log(monkeypatch):
    calls = []
    monkeypatch.setattr(settings_schema_module, "write_log", lambda event, *a, **k: calls.append(event))
    return calls


def test_repair_valid_tree_validates_unchanged_no_log(_captured_write_log):
    s = default_settings()
    out = validate_settings_tree(s)
    assert out == s
    assert _captured_write_log == []


def test_repair_strips_single_extra_key_logs_dotted_path_no_mutation(_captured_write_log):
    s = default_settings()
    original = copy.deepcopy(s)
    s["safety"]["future_knob"] = 42

    out = validate_settings_tree(s)

    assert "future_knob" not in out["safety"]
    assert len(_captured_write_log) == 1
    assert "safety.future_knob" in _captured_write_log[0]
    # The caller's dict must NOT be mutated by the repair pass -- it's a
    # deepcopy that gets stripped, not the original.
    assert s["safety"]["future_knob"] == 42
    assert s["safety"] == {**original["safety"], "future_knob": 42}


def test_repair_strips_multiple_extra_keys_at_different_paths_each_logged(_captured_write_log):
    s = default_settings()
    s["safety"]["future_knob"] = 42
    s["pwm"]["another_future_knob"] = "x"

    out = validate_settings_tree(s)

    assert "future_knob" not in out["safety"]
    assert "another_future_knob" not in out["pwm"]
    assert len(_captured_write_log) == 2
    joined = " ".join(_captured_write_log)
    assert "safety.future_knob" in joined
    assert "pwm.another_future_knob" in joined


def test_repair_leaves_real_type_error_alone_raises_no_log(_captured_write_log):
    s = default_settings()
    s["safety"]["maxtemp"] = "not-an-int"
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("safety.maxtemp" in m for m in ei.value.errors)
    assert _captured_write_log == []


def test_repair_leaves_missing_required_field_alone_raises_no_log(_captured_write_log):
    s = default_settings()
    del s["server_info"]
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("server_info" in m for m in ei.value.errors)
    assert _captured_write_log == []


def test_repair_leaves_cross_field_validator_failure_alone_raises_no_log(_captured_write_log):
    # Clamp source: blueprints/settings/routes.py:493-497 (cross-SECTION:
    # startup.pwm_duty_cycle vs. pwm.min_duty_cycle/max_duty_cycle) -- same
    # invariant as test_pwm_duty_cycle_must_be_within_min_max above, repeated
    # here to pin that a real model_validator failure is untouched by repair.
    s = default_settings()
    s["startup"]["pwm_duty_cycle"] = 10  # below min_duty_cycle=20
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("startup.pwm_duty_cycle must be within" in m for m in ei.value.errors)
    assert _captured_write_log == []


def test_repair_does_not_mask_real_error_when_extra_key_in_same_section(_captured_write_log):
    # One extra key AND one real (type) error, same section: must still
    # raise -- the real error must not be silently stripped-and-passed.
    s = default_settings()
    s["safety"]["future_knob"] = 42  # extra
    s["safety"]["maxtemp"] = "not-an-int"  # real error
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("safety.maxtemp" in m for m in ei.value.errors)
    assert _captured_write_log == []


def test_repair_does_not_mask_real_error_when_extra_key_in_different_section(_captured_write_log):
    # One extra key in one section, a real cross-field-validator error in an
    # unrelated section: must still raise as a whole (the extra key alone
    # would have been repairable, but a co-occurring real error anywhere in
    # the tree must veto the whole repair-and-retry).
    s = default_settings()
    s["safety"]["future_knob"] = 42  # extra, elsewhere
    s["startup"]["pwm_duty_cycle"] = 10  # real cross-field error, below min_duty_cycle=20
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("startup.pwm_duty_cycle must be within" in m for m in ei.value.errors)
    assert _captured_write_log == []


# ---------------------------------------------------------------------------
# platform.devices.distance.address: the wizard writes a HEX STRING ("0x29")
# and distance/_tof_base.py accepts str | int | None. An int-only annotation
# here rejected every real configured value -- and because a wrong TYPE is not
# an extra_forbidden error, the repair gate above correctly refused to touch
# it, so the failure escalated to "every settings write on the install 500s".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("address", ["0x29", "0x2f", 41, None])
def test_distance_address_accepts_every_form_the_tof_driver_reads(address, _captured_write_log):
    s = default_settings()
    s["platform"]["devices"]["distance"]["address"] = address
    out = validate_settings_tree(s)
    assert out["platform"]["devices"]["distance"]["address"] == address
    assert _captured_write_log == []


def test_distance_address_rejects_a_type_the_driver_cannot_read(_captured_write_log):
    s = default_settings()
    s["platform"]["devices"]["distance"]["address"] = 41.5
    with pytest.raises(SettingsValidationError) as ei:
        validate_settings_tree(s)
    assert any("platform.devices.distance.address" in m for m in ei.value.errors)


# ---------------------------------------------------------------------------
# Renamed settings. The repair gate strips unmodeled keys, which for a RENAME
# would discard the user's configured value. _carry_renamed_keys() moves it to
# the new name first, so repair stays lossless.
# ---------------------------------------------------------------------------


@pytest.fixture
def _synthetic_rename(monkeypatch):
    """Install a rename nobody ships, so these tests exercise the MECHANISM.

    `_RENAMED_SETTINGS` is empty whenever no rename is mid-flight, and it is
    empty now. Binding these tests to whichever entry happened to be listed
    meant they died with that entry -- and the carry has to keep working for
    the next rename, which is the only time anyone will find out if it does
    not. `grill_name` is the destination because a rename's target must be a
    MODELED field; an unmodeled one would be stripped by the same repair pass
    the carry runs inside, and the test would pass for the wrong reason.
    """
    monkeypatch.setattr(settings_schema_module, "_RENAMED_SETTINGS", {"globals.old_grill_name": "globals.grill_name"})


def test_repair_carries_renamed_key_preserving_the_users_value(_captured_write_log, _synthetic_rename):
    s = default_settings()
    # A pre-rename install has no key under the NEW name at all -- the store is
    # read raw, with no defaults merge -- so the destination is absent, not
    # empty. That distinction is load-bearing: the carry only fills a
    # destination that is absent or None, so seeding "" here would model a
    # shape that never occurs and prove nothing.
    s["globals"].pop("grill_name", None)
    s["globals"]["old_grill_name"] = "Backyard"  # the pre-rename name, still holding the value

    out = validate_settings_tree(s)

    assert out["globals"]["grill_name"] == "Backyard"
    assert "old_grill_name" not in out["globals"]
    # Logged as a carry, not as a silent strip.
    assert len(_captured_write_log) == 1
    assert "carried renamed key" in _captured_write_log[0]
    assert "globals.old_grill_name" in _captured_write_log[0]
    assert "globals.grill_name" in _captured_write_log[0]


def test_repair_rename_does_not_clobber_a_value_already_under_the_new_name(_captured_write_log, _synthetic_rename):
    s = default_settings()
    s["globals"]["grill_name"] = "Backyard"  # already migrated
    s["globals"]["old_grill_name"] = "Stale"  # stale leftover

    out = validate_settings_tree(s)

    assert out["globals"]["grill_name"] == "Backyard"
    assert "old_grill_name" not in out["globals"]
    # Nothing was carried, so the stale key is reported as an ordinary strip.
    assert len(_captured_write_log) == 1
    assert "stripped unmodeled key" in _captured_write_log[0]


def test_repair_leaves_the_callers_tree_unmutated_when_carrying(_captured_write_log, _synthetic_rename):
    s = default_settings()
    s["globals"].pop("grill_name", None)
    s["globals"]["old_grill_name"] = "Backyard"
    original = copy.deepcopy(s)

    validate_settings_tree(s)

    assert s == original


def test_no_rename_configured_carries_nothing(_captured_write_log):
    """Guards the empty resting state. With `_RENAMED_SETTINGS` empty -- as it
    is whenever no rename is mid-flight -- a stale key is an ordinary strip and
    the carry path must stay silent rather than log an empty carry."""
    assert settings_schema_module._RENAMED_SETTINGS == {}

    s = default_settings()
    s["globals"]["old_grill_name"] = "Backyard"

    out = validate_settings_tree(s)

    assert "old_grill_name" not in out["globals"]
    assert len(_captured_write_log) == 1
    assert "stripped unmodeled key" in _captured_write_log[0]
    assert "carried renamed key" not in _captured_write_log[0]


def test_live_install_shape_validates_end_to_end(_captured_write_log):
    """The exact combination found on a real install: settings deleted from the
    schema, a dead unmodeled section, and a correctly-configured hex I2C
    address. Every settings write on that install failed until the address
    annotation was widened -- the dead keys alone were always repairable, but
    the address type error vetoed the repair.

    Both theme keys are here on purpose. An install predating the rename holds
    `page_theme`, one that saw it holds `bootstrap_page_theme`, and the setting
    is now deleted outright, so BOTH are strays and both must be stripped
    without failing the write. There is nothing left to carry them to.
    """
    s = default_settings()
    s["globals"]["page_theme"] = "dark"
    s["globals"]["bootstrap_page_theme"] = "dark"
    s["globals"]["global_control_panel"] = False
    s["platform"]["emc2301"] = {"address": "0x2f", "i2c_bus_kind": "basic", "i2c_bus_num": 1}
    s["platform"]["devices"]["distance"]["address"] = "0x29"
    s["platform"]["fan_controller"]["address"] = "0x2f"

    out = validate_settings_tree(s)

    assert "page_theme" not in out["globals"]  # stripped
    assert "bootstrap_page_theme" not in out["globals"]  # stripped
    assert "global_control_panel" not in out["globals"]  # stripped
    assert "emc2301" not in out["platform"]  # stripped
    assert out["platform"]["devices"]["distance"]["address"] == "0x29"  # kept
    assert out["platform"]["fan_controller"]["address"] == "0x2f"  # kept


def test_i2c_bus_accepts_every_variant():
    from common.settings_schema import validate_settings_tree

    for bus in (
        {"kind": "basic"},
        {"kind": "kernel", "bus_num": 3},
        {"kind": "kernel", "adapter": "CP2112"},
        {"kind": "kernel", "serial": "0012AB34"},
        {"kind": "ft232h", "url": "ftdi://ftdi:232h:FT9ABC/1"},
        {"kind": "mcp2221", "serial": "01234567"},
    ):
        settings = copy.deepcopy(default_settings())
        settings["platform"]["devices"]["distance"]["i2c_bus"] = bus
        settings["platform"]["fan_controller"]["i2c_bus"] = bus
        validate_settings_tree(settings)


def test_i2c_bus_rejects_a_field_from_another_kind():
    """The schema mirrors the dataclass hierarchy: extra="forbid" on each
    variant is what makes only one member of the union match."""
    from common.settings_schema import SettingsValidationError, validate_settings_tree

    settings = copy.deepcopy(default_settings())
    settings["platform"]["fan_controller"]["i2c_bus"] = {"kind": "ft232h", "adapter": "CP2112"}
    with pytest.raises(SettingsValidationError):
        validate_settings_tree(settings)


def test_i2c_bus_defaults_to_basic():
    settings = default_settings()
    assert settings["platform"]["devices"]["distance"]["i2c_bus"] == {"kind": "basic"}
    assert settings["platform"]["fan_controller"]["i2c_bus"] == {"kind": "basic"}
    assert "i2c_bus_kind" not in settings["platform"]["fan_controller"]
    assert "i2c_bus_num" not in settings["platform"]["fan_controller"]


def test_i2c_bus_rejects_negative_bus_num():
    from common.settings_schema import SettingsValidationError, validate_settings_tree

    settings = copy.deepcopy(default_settings())
    settings["platform"]["fan_controller"]["i2c_bus"] = {"kind": "kernel", "bus_num": -1}
    with pytest.raises(SettingsValidationError):
        validate_settings_tree(settings)


@pytest.mark.parametrize(
    "i2c_bus",
    [
        {"kind": "kernel", "adapter": ""},
        {"kind": "kernel", "adapter": "   "},
        {"kind": "kernel", "serial": ""},
        {"kind": "kernel", "adapter": "X", "bus_num": 3},
    ],
    ids=["blank_adapter", "whitespace_only_adapter", "blank_serial", "two_selectors_of_the_same_kind"],
)
def test_i2c_bus_rejects_a_malformed_selector(i2c_bus):
    from common.settings_schema import SettingsValidationError, validate_settings_tree

    settings = copy.deepcopy(default_settings())
    settings["platform"]["fan_controller"]["i2c_bus"] = i2c_bus
    with pytest.raises(SettingsValidationError):
        validate_settings_tree(settings)


def test_atomic_settings_paths_is_exactly_the_i2c_bus_fields():
    from common.settings_schema import atomic_settings_paths

    assert atomic_settings_paths() == {
        ("platform", "devices", "distance", "i2c_bus"),
        ("platform", "fan_controller", "i2c_bus"),
    }
