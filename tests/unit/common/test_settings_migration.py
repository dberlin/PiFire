"""Coverage for common/settings_migration.py: the settings.json FILE reader
and the versioned upgrade/downgrade migration steps it drives.

Scope:
  - upgrade_settings(): pure dict transformation, exercised directly with
    real OLD-schema settings dict fixtures built from default_settings() --
    no datastore/backups infra required for these.
  - read_settings_file()/downgrade_settings()/restore_settings(): these call
    backup_settings()/write_warning()/write_log()/write_settings_store(),
    which need a live (test-isolated) SQLite datastore, and read/write
    "<BACKUP_PATH>/manifest.json" (settings_migration.py uses the same
    common.common.BACKUP_PATH as common/backups.py, so the `real_backups_dir`
    fixture below redirects it -- via a per-module monkeypatch, since each
    module bound its own copy at import time -- to a tmp_path dir instead of
    the real repo ./backups/, modeled on the equivalent fixture in
    tests/unit/bootstrap/test_startup_migration.py).

LATENT BUG (documented, not silently worked around -- see
test_upgrade_settings_v1_4_cascade_loses_start_to_mode_BUG below):
  common/settings_migration.py:127-128 and :167-170. Upgrading directly from
  a v1.4.x-or-earlier install to current wipes settings["startup"]["start_to_mode"]
  down to an EMPTY dict, discarding both the user's migrated primary_setpoint
  AND the sensible defaults. controller/runtime/controller.py:457/492 index
  settings["startup"]["start_to_mode"]["primary_setpoint"] with no .get()
  fallback, so this is a live KeyError-on-first-startup landmine for anyone
  upgrading from a sufficiently old install in one shot, not just quiet data
  loss. Severity: HIGH (crash-on-first-use after a realistic upgrade path).
"""

import copy
import json
import os

import pytest

from common import datastore
from common.persistence import runtime as runtime_persistence
from common.defaults import default_settings
from common.settings_migration import (
    downgrade_settings,
    read_settings_file,
    restore_settings,
    upgrade_settings,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A test-isolated SQLite datastore (mirrors tests/unit/bootstrap/test_startup_migration.py)."""
    monkeypatch.setenv("PIFIRE_DB_PATH", str(tmp_path / "t.db"))
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    yield tmp_path
    datastore._reset_for_tests(None)


@pytest.fixture
def real_backups_dir(tmp_path, monkeypatch):
    """downgrade_settings()/restore_settings() (in common.settings_migration)
    and backup_settings()/backup_pellet_db() (in common.backups) each read
    BACKUP_PATH via their own module's `from common.common import BACKUP_PATH`
    -- a bind-by-value import, so patching common.common.BACKUP_PATH alone
    would not reach either module. This fixture points a tmp_path-based
    directory at BOTH bound names, so these code paths never touch the real
    repo ./backups/ dir.
    """
    backups_path = tmp_path / "backups"
    backups_path.mkdir()
    backups_path_str = str(backups_path) + os.sep
    monkeypatch.setattr("common.settings_migration.BACKUP_PATH", backups_path_str)
    monkeypatch.setattr("common.backups.BACKUP_PATH", backups_path_str)

    yield str(backups_path)


def _base():
    """A full default settings dict, safe to mutate per-test."""
    return default_settings()


# ---------------------------------------------------------------------------
# upgrade_settings(): pure dict transforms, no datastore needed
# ---------------------------------------------------------------------------


def test_upgrade_settings_v1_4_cascade_migrates_notify_probe_and_platform():
    """A v1.4.x-or-earlier install upgrading straight to current cascades
    through EVERY subsequent version block in one call (each `if` only
    checks `prev_ver`, which stays fixed for the whole call) -- this is the
    realistic "upgrading after a long gap" scenario. Assert the individual
    transformations that survive it correctly.
    """
    d = _base()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.4.0", "build": 0}
    old["startup"] = {"start_to_mode": {}}
    old["start_to_mode"] = {"grill1_setpoint": 225}
    old["dashboard"] = {"sentinel_old_dash": True}
    for key in d["notify_services"].keys():
        old[key] = {"legacy_marker": key}
    del old["notify_services"]
    old["probe_settings"]["probe_options"] = {"x": 1}
    old["probe_settings"]["probe_sources"] = {"x": 1}
    old["probe_settings"]["probes_enabled"] = {"x": 1}
    old["modules"]["adc"] = "mcp3008"
    old["modules"]["grillplat"] = "some_real_platform"
    prof_key = next(iter(old["probe_settings"]["probe_profiles"].keys()))
    old["probe_settings"]["probe_profiles"][prof_key].pop("id", None)
    old["cycle_data"] = {"SmokeCycleTime": 30}
    old["globals"]["startup_timer"] = 999
    old["globals"]["startup_exit_temp"] = 111
    old["globals"]["shutdown_timer"] = 222
    old["globals"]["auto_power_off"] = True
    old["globals"]["buttonslevel"] = "LOW"
    old["dev_pins"] = {"display": {"dc": 42}}

    result = upgrade_settings([1, 4, 0], old, d)

    # Notification settings moved from top-level keys into notify_services.
    for key in d["notify_services"].keys():
        assert result["notify_services"][key] == {"legacy_marker": key}
        # ...and the legacy top-level copy is popped, not left behind as a
        # stray extra key (common/settings_migration.py:135-138).
        assert key not in result

    # Old probe_settings/modules fields removed.
    assert "probe_options" not in result["probe_settings"]
    assert "probe_sources" not in result["probe_settings"]
    assert "probes_enabled" not in result["probe_settings"]
    assert "adc" not in result["modules"]

    # Probe profiles missing an "id" get one backfilled from their key.
    assert result["probe_settings"]["probe_profiles"][prof_key]["id"] == prof_key

    # cycle_data renamed/reset.
    assert "SmokeCycleTime" not in result["cycle_data"]
    assert result["cycle_data"]["SmokeOnCycleTime"] == 15
    assert result["cycle_data"]["SmokeOffCycleTime"] == 45

    # dashboard reset to default (v1.6/1.7build<=7 block also cascades in).
    assert result["dashboard"] == d["dashboard"]

    # startup/shutdown timers pulled out of globals.
    assert result["startup"]["duration"] == 999
    assert result["startup"]["startup_exit_temp"] == 111
    assert result["shutdown"]["shutdown_duration"] == 222
    assert result["shutdown"]["auto_power_off"] is True
    assert "startup_timer" not in result["globals"]
    assert "startup_exit_temp" not in result["globals"]
    assert "shutdown_timer" not in result["globals"]
    assert "auto_power_off" not in result["globals"]

    # platform section created; globals platform fields moved+popped.
    assert result["platform"]["buttonslevel"] == "LOW"
    assert "buttonslevel" not in result["globals"]
    assert result["platform"]["devices"]["display"]["dc"] == 42
    assert "dev_pins" not in result
    # non-"prototype" grillplat -> raspberry_pi_all system_type.
    assert result["platform"]["system_type"] == "raspberry_pi_all"

    assert result["globals"]["updated_message"] is True


def test_upgrade_settings_v1_4_cascade_preserves_start_to_mode():
    """Regression for a HIGH-severity upgrade crash (was
    common/settings_migration.py:127-128 + :167-170).

    Block 1 (prev_ver<=1.4) carries the legacy top-level
    `start_to_mode.grill1_setpoint` forward into the modern
    `start_to_mode.primary_setpoint`. Previously it wrote straight into
    `settings["startup"]["start_to_mode"]` and popped only the
    `grill1_setpoint` SUB-key, leaving an empty
    `settings["start_to_mode"] == {}` at the top level. The later
    v1.6/1.7-build<=45 block then did:
        settings["startup"]["start_to_mode"] = settings.get("start_to_mode", <default>)
    and `.get()` treated "present but empty" as real data, overwriting the
    populated startup dict with `{}` -- wiping the user's setpoint AND the
    defaults (after_startup_mode, primary_setpoint, start_to_hold_prompt).
    controller/runtime/controller.py then KeyError'd on first startup after
    such an upgrade.

    Fix reshapes the legacy top-level start_to_mode into the modern shape so
    the later startup-split block moves the real, populated value (and the
    later block now also falls back to the default on an empty dict). This
    test asserts the CORRECT end state.
    """
    d = _base()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.4.0", "build": 0}
    old["startup"] = {"start_to_mode": {}}
    old["start_to_mode"] = {"grill1_setpoint": 225}
    for key in d["notify_services"].keys():
        old[key] = {}
    del old["notify_services"]
    old["probe_settings"]["probe_options"] = {}
    old["probe_settings"]["probe_sources"] = {}
    old["probe_settings"]["probes_enabled"] = {}
    old["modules"]["adc"] = "mcp3008"
    old["cycle_data"] = {"SmokeCycleTime": 30}

    result = upgrade_settings([1, 4, 0], old, d)

    # Correct behavior: the user's legacy grill1_setpoint (225) survives the
    # whole cascade into startup.start_to_mode.primary_setpoint, and the modern
    # start_to_mode retains its full default shape (never an empty dict).
    stm = result["startup"]["start_to_mode"]
    assert stm["primary_setpoint"] == 225  # migrated setpoint survives
    assert stm["after_startup_mode"] == d["startup"]["start_to_mode"]["after_startup_mode"]
    assert stm["start_to_hold_prompt"] == d["startup"]["start_to_mode"]["start_to_hold_prompt"]
    # legacy top-level start_to_mode key fully removed
    assert "start_to_mode" not in result


def test_upgrade_settings_block4_platform_migration_prototype_branch():
    """Isolate the v1.7.x platform-section migration block (a high build
    number keeps the earlier v1.6/1.7build<=7/<=45 blocks from also firing,
    since prev_ver[1]==7 there but their build thresholds are exceeded)."""
    d = _base()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.7.0", "build": 100}
    old["modules"]["grillplat"] = "prototype"
    old["globals"]["dc_fan"] = True
    old["globals"]["real_hw"] = False
    old["globals"]["standalone"] = False
    old["globals"]["triggerlevel"] = "LOW"
    old["inpins"] = {"selector": 55}
    old["outpins"] = {"auger": 77}

    result = upgrade_settings([1, 7, 0], old, d)

    assert result["platform"]["current"] == "custom"
    assert result["platform"]["system_type"] == "prototype"
    assert result["platform"]["dc_fan"] is True
    assert result["platform"]["real_hw"] is False
    assert result["platform"]["inputs"]["selector"] == 55
    assert result["platform"]["outputs"]["auger"] == 77
    assert "inpins" not in result
    assert "outpins" not in result
    assert "dc_fan" not in result["globals"]


@pytest.mark.parametrize("build,expect_reset", [(7, True), (8, False)])
def test_upgrade_settings_dashboard_build7_boundary(build, expect_reset):
    """v1.7.0 build<=7 resets dashboard to default; build 8 must not (an
    off-by-one here would silently wipe a build-8 user's dashboard, or leave
    a build-7 user's stale dashboard in place)."""
    d = _base()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.7.0", "build": build}
    old["dashboard"] = {"sentinel": True}

    result = upgrade_settings([1, 7, 0], old, d)

    if expect_reset:
        assert result["dashboard"] == d["dashboard"]
    else:
        assert result["dashboard"] == {"sentinel": True}


@pytest.mark.parametrize("build,expect_rename", [(32, True), (33, False)])
def test_upgrade_settings_v1_9_bt_meater_rename_boundary(build, expect_rename):
    """v1.9.0 build<=32 renames bt_meater_alt->bt_meater and
    bt_meater->bt_meater_exp on every probe_map device; build 33 must not."""
    d = _base()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.9.0", "build": build}
    old["probe_settings"]["probe_map"]["probe_devices"] = [
        {"device": "d1", "module": "bt_meater_alt", "module_filename": "x"},
        {"device": "d2", "module": "bt_meater", "module_filename": "x"},
        # A device matching neither rename target -- exercises the loop's
        # no-match/continue path (elif False -> next iteration).
        {"device": "d3", "module": "prototype", "module_filename": "x"},
    ]

    result = upgrade_settings([1, 9, 0], old, d)
    devices = result["probe_settings"]["probe_map"]["probe_devices"]

    if expect_rename:
        assert devices[0]["module"] == "bt_meater"
        assert devices[1]["module"] == "bt_meater_exp"
    else:
        assert devices[0]["module"] == "bt_meater_alt"
        assert devices[1]["module"] == "bt_meater"
    assert devices[2]["module"] == "prototype"


@pytest.mark.parametrize("venv,expected_exec", [(True, "bin/python"), (False, "python")])
def test_upgrade_settings_v1_10_build0_python_exec(venv, expected_exec):
    """v1.10.0 build 0 (or anything <1.10) sets python_exec based on the
    old globals.venv flag. Note: BOTH branches force uv=False -- even the
    non-venv branch, which never sets it True -- matching the explicit
    "TODO: Upgrade to VENV for older configs?" comment in the source; not
    flagged as a bug since it's a deliberate, documented placeholder."""
    d = _base()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.10.0", "build": 0}
    old["globals"]["venv"] = venv

    result = upgrade_settings([1, 10, 0], old, d)

    assert result["globals"]["python_exec"] == expected_exec
    assert result["globals"]["uv"] is False


@pytest.mark.parametrize("build,expect_added", [(51, True), (52, False)])
def test_upgrade_settings_v1_10_build51_module_filename_boundary(build, expect_added):
    """v1.10.0 build<=51 backfills probe_map device["module_filename"] from
    device["module"] when missing; build 52 must not touch it."""
    d = _base()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.10.0", "build": build}
    old["probe_settings"]["probe_map"]["probe_devices"] = [{"device": "d1", "module": "prototype"}]

    result = upgrade_settings([1, 10, 0], old, d)
    device = result["probe_settings"]["probe_map"]["probe_devices"][0]

    if expect_added:
        assert device["module_filename"] == "prototype"
    else:
        assert "module_filename" not in device


def test_upgrade_settings_imports_new_probe_profiles_and_always_sets_updated_message():
    """Even for a "current version" settings dict (no legacy blocks fire at
    all), upgrade_settings() (a) backfills any probe profile present in
    defaults but missing from the old settings, and (b) unconditionally
    sets globals.updated_message -- this always runs regardless of how far
    behind (or not) the source version is."""
    d = _base()
    old = copy.deepcopy(d)
    old["versions"] = dict(d["versions"])
    old["globals"]["updated_message"] = False
    removed_key = next(iter(old["probe_settings"]["probe_profiles"].keys()))
    removed_val = old["probe_settings"]["probe_profiles"].pop(removed_key)

    prev_ver = [int(x) for x in d["versions"]["server"].split(".")]
    result = upgrade_settings(prev_ver, old, d)

    assert result["probe_settings"]["probe_profiles"][removed_key] == removed_val
    assert result["globals"]["updated_message"] is True


# ---------------------------------------------------------------------------
# read_settings_file(): version-detection branches that don't need a
# datastore (no backup_settings()/downgrade_settings() call on these paths)
# ---------------------------------------------------------------------------


def test_read_settings_file_missing_file_returns_defaults(tmp_path):
    """A nonexistent settings file hits the IOError/OSError branch and
    returns default_settings() immediately -- it does NOT go through the
    init-overlay/upgrade logic at all (the function returns before that
    code is reached)."""
    missing = tmp_path / "does_not_exist.json"

    result = read_settings_file(filename=str(missing))

    assert result["versions"] == default_settings()["versions"]
    assert "globals" in result


def test_read_settings_file_backfills_missing_first_time_setup(tmp_path):
    d = _base()
    del d["globals"]["first_time_setup"]
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(d))

    result = read_settings_file(filename=str(p), init=True)

    assert result["globals"]["first_time_setup"] is False


def test_read_settings_file_extremely_old_missing_versions_key(tmp_path):
    """No "versions" key at all: read_settings_file backfills
    settings["versions"] from defaults directly WITHOUT invoking
    upgrade_settings() at all (that branch is a separate elif from the
    upgrade path) -- any legacy-shaped fields in such a settings dict would
    pass through untransformed. Not flagged as a bug: a "versions" key has
    existed since very early releases, so this is a defensive fallback for
    an implausible case, not a realistic upgrade path."""
    d = _base()
    del d["versions"]
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(d))

    result = read_settings_file(filename=str(p), init=True)

    assert result["versions"] == default_settings()["versions"]


def test_read_settings_file_minor_upgrade_when_build_lower(tmp_path):
    d = default_settings()
    old = copy.deepcopy(d)
    old["versions"]["build"] = d["versions"]["build"] - 1
    old["globals"]["updated_message"] = False
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(old))

    result = read_settings_file(filename=str(p), init=True)

    assert result["versions"] == d["versions"]
    # upgrade_settings() ran (proven by its unconditional side effect).
    assert result["globals"]["updated_message"] is True


def test_read_settings_file_no_migration_when_build_higher(tmp_path):
    """Same server version but a HIGHER build than the running code matches
    none of the four if/elif branches (not missing, not lower, not higher,
    and the minor-upgrade elif requires build<=default) -- falls straight
    through to the standalone build-stamp fixup, without calling
    upgrade_settings() at all."""
    d = default_settings()
    old = copy.deepcopy(d)
    old["versions"]["build"] = d["versions"]["build"] + 1
    old["globals"]["updated_message"] = False
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(old))

    result = read_settings_file(filename=str(p), init=True)

    assert result["versions"]["build"] == d["versions"]["build"]
    # upgrade_settings() did NOT run -- its unconditional side effect is absent.
    assert result["globals"]["updated_message"] is False


# ---------------------------------------------------------------------------
# read_settings_file(): upgrade/downgrade paths (call backup_settings(),
# need a live datastore + the real ./backups/ dir)
# ---------------------------------------------------------------------------


def test_read_settings_file_upgrade_path_backs_up_and_upgrades(fresh, real_backups_dir):
    d = default_settings()
    runtime_persistence.write_settings_store(d)

    old = copy.deepcopy(d)
    old["versions"]["server"] = "1.10.9"  # just below current -- avoids the ancient cascade blocks
    old["globals"]["updated_message"] = False
    p = fresh / "settings.json"
    p.write_text(json.dumps(old))

    result = read_settings_file(filename=str(p), init=True)

    assert result["versions"] == d["versions"]
    assert result["globals"]["updated_message"] is True
    # backup_settings() wrote a dated backup file into the tmp backups dir.
    assert any(name.startswith("PiFire_") for name in os.listdir(real_backups_dir))


def test_read_settings_file_downgrade_path_resets_to_defaults_when_no_backup(fresh, real_backups_dir):
    d = default_settings()
    runtime_persistence.write_settings_store(d)

    old = copy.deepcopy(d)
    old["versions"]["server"] = "99.99.99"  # newer than current code -> downgrade path
    p = fresh / "settings.json"
    p.write_text(json.dumps(old))

    result = read_settings_file(filename=str(p), init=True)

    assert result["versions"] == d["versions"]
    assert result["globals"]["grill_name"] == d["globals"]["grill_name"]
    assert any(name.startswith("PiFire_") for name in os.listdir(real_backups_dir))


# ---------------------------------------------------------------------------
# downgrade_settings(): direct unit tests
# ---------------------------------------------------------------------------


def test_downgrade_settings_restores_from_matching_backup_file(fresh, real_backups_dir):
    d = default_settings()
    server_ver = d["versions"]["server"]

    backup_content = default_settings()
    backup_content["globals"]["grill_name"] = "SENTINEL_DOWNGRADE_BACKUP"
    backup_file = os.path.join(real_backups_dir, "PiFire_test_backup.json")
    with open(backup_file, "w") as fh:
        json.dump(backup_content, fh)

    manifest = {"server_settings": {server_ver: backup_file}, "pelletdb": {"current": ""}}
    with open(os.path.join(real_backups_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh)

    result = downgrade_settings(default_settings(), d)

    assert result["globals"]["grill_name"] == "SENTINEL_DOWNGRADE_BACKUP"


def test_downgrade_settings_resets_to_defaults_when_no_matching_backup(fresh, real_backups_dir):
    d = default_settings()
    manifest = {"server_settings": {}, "pelletdb": {"current": ""}}
    with open(os.path.join(real_backups_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh)

    result = downgrade_settings(default_settings(), d)

    assert result is d


def test_downgrade_settings_creates_manifest_when_missing(fresh, real_backups_dir):
    """No manifest.json at all (distinct from an existing-but-empty-for-this-
    version manifest, covered above): downgrade_settings must create a
    default manifest structure rather than raising."""
    manifest_path = os.path.join(real_backups_dir, "manifest.json")
    assert not os.path.exists(manifest_path)
    d = default_settings()

    result = downgrade_settings(default_settings(), d)

    assert result is d
    assert os.path.exists(manifest_path)


# ---------------------------------------------------------------------------
# restore_settings(): direct unit test for the no-manifest-yet path (also
# exercises the subsequent no-matching-backup else branch in the same call)
# ---------------------------------------------------------------------------


def test_restore_settings_creates_manifest_and_resets_to_defaults_when_none_exists(fresh, real_backups_dir):
    manifest_path = os.path.join(real_backups_dir, "manifest.json")
    assert not os.path.exists(manifest_path)
    d = default_settings()

    result = restore_settings(d)

    assert result is d
    assert os.path.exists(manifest_path)
    with open(manifest_path) as fh:
        written = json.load(fh)
    assert written == {"server_settings": {}, "pelletdb": {"current": ""}}
    # restore_settings() makes the recovered settings the new current state.
    assert runtime_persistence.read_settings()["globals"]["grill_name"] == d["globals"]["grill_name"]


def test_restore_settings_uses_existing_manifest_and_matching_backup(fresh, real_backups_dir):
    """Complements the "no manifest yet" test above: a manifest.json already
    exists (so the empty-manifest fallback at lines 295-297 is skipped) AND
    has a matching entry for the current server version (the "found" branch
    at lines 300-304), restoring from that backup file's content rather
    than resetting to defaults."""
    d = default_settings()
    server_ver = d["versions"]["server"]

    backup_content = default_settings()
    backup_content["globals"]["grill_name"] = "SENTINEL_RESTORE_BACKUP"
    backup_file = os.path.join(real_backups_dir, "PiFire_restore_backup.json")
    with open(backup_file, "w") as fh:
        json.dump(backup_content, fh)

    manifest = {"server_settings": {server_ver: backup_file}, "pelletdb": {"current": ""}}
    with open(os.path.join(real_backups_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh)

    result = restore_settings(d)

    assert result["globals"]["grill_name"] == "SENTINEL_RESTORE_BACKUP"
    assert runtime_persistence.read_settings()["globals"]["grill_name"] == "SENTINEL_RESTORE_BACKUP"


# ---------------------------------------------------------------------------
# read_settings_file(): ValueError retry-then-restore chain
# ---------------------------------------------------------------------------


def test_read_settings_file_retries_on_corrupt_json_then_restores(fresh, real_backups_dir):
    """A JSON-decode error (e.g. a torn write mid-read) retries up to 5
    times against the same file before giving up and falling back to
    restore_settings() -- proving this terminates (no RecursionError/hang)
    and produces a usable defaults-shaped result."""
    p = fresh / "corrupt_settings.json"
    p.write_text("{not valid json")

    result = read_settings_file(filename=str(p))

    assert result["versions"] == default_settings()["versions"]
    assert "globals" in result
