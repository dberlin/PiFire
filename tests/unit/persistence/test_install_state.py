import json
import sqlite3

import pytest

from common.persistence.install_state import (
    delete_wizard_install_info,
    get_update_restart_pending,
    get_updater_install_status,
    get_wizard_install_status,
    load_os_info,
    load_wizard_install_info,
    set_update_restart_pending,
    set_updater_install_status,
    set_wizard_install_status,
    store_os_info,
    store_wizard_install_info,
)


def test_os_info_missing_returns_a_fresh_empty_payload(ds):
    first = load_os_info()
    first["PRETTY_NAME"] = "changed by caller"

    assert load_os_info() == {}


def test_os_info_round_trip_uses_system_key_and_owns_copies(ds):
    os_info = {
        "PRETTY_NAME": "PiFire OS",
        "ARCHITECTURE": "aarch64",
        "metadata": {"versions": [1, 2]},
    }
    store_os_info(os_info)

    os_info["metadata"]["versions"].append(3)
    assert json.loads(ds.get_blob("system:os_info")) == {
        "PRETTY_NAME": "PiFire OS",
        "ARCHITECTURE": "aarch64",
        "metadata": {"versions": [1, 2]},
    }

    loaded = load_os_info()
    loaded["metadata"]["versions"].append(4)
    assert load_os_info()["metadata"] == {"versions": [1, 2]}


def test_os_info_rejects_corrupt_payload_at_storage_boundary(ds):
    with pytest.raises(sqlite3.IntegrityError):
        ds.set_blob("system:os_info", "not-json")

    assert load_os_info() == {}


def test_wizard_install_blob_missing_and_corrupt_writes_are_rejected(ds):
    with pytest.raises(TypeError):
        load_wizard_install_info()

    with pytest.raises(sqlite3.IntegrityError):
        ds.set_blob("wizard:install", "not-json")
    with pytest.raises(TypeError):
        load_wizard_install_info()


def test_wizard_install_blob_round_trip_is_verbatim_and_owns_copies(ds):
    install_info = {
        "platform": "raspberry-pi",
        "steps": ["os", "hardware"],
        "manifest_fingerprint": "stale-is-installation-policy-not-storage-policy",
    }
    store_wizard_install_info(install_info)

    install_info["steps"].append("mutated-after-store")
    assert json.loads(ds.get_blob("wizard:install")) == {
        "platform": "raspberry-pi",
        "steps": ["os", "hardware"],
        "manifest_fingerprint": "stale-is-installation-policy-not-storage-policy",
    }

    loaded = load_wizard_install_info()
    loaded["steps"].append("mutated-after-load")
    assert load_wizard_install_info()["steps"] == ["os", "hardware"]


def test_delete_wizard_install_blob_restores_missing_behavior(ds):
    store_wizard_install_info({"platform": "raspberry-pi"})

    delete_wizard_install_info()
    delete_wizard_install_info()

    assert ds.get_blob("wizard:install") is None
    with pytest.raises(TypeError):
        load_wizard_install_info()


def test_install_status_missing_fields_remain_none(ds):
    assert get_wizard_install_status() == (None, None, None)
    assert get_updater_install_status() == (None, None, None)

    ds.set_blob("wizard:percent", json.dumps(15))
    ds.set_blob("wizard:output", json.dumps("first line"))

    assert get_wizard_install_status() == (15, None, "first line")
    assert get_updater_install_status() == (None, None, None)


def test_wizard_and_updater_statuses_preserve_fields_and_namespaces(ds):
    set_wizard_install_status(25, "wizard-running", "wizard-output")

    assert ds.get_blob("wizard:percent") == "25"
    assert ds.get_blob("wizard:status") == '"wizard-running"'
    assert ds.get_blob("wizard:output") == '"wizard-output"'
    assert get_wizard_install_status() == (25, "wizard-running", "wizard-output")
    assert get_updater_install_status() == (None, None, None)

    set_updater_install_status(75, "updater-running", "updater-output")

    assert ds.get_blob("updater:percent") == "75"
    assert ds.get_blob("updater:status") == '"updater-running"'
    assert ds.get_blob("updater:output") == '"updater-output"'
    assert get_updater_install_status() == (75, "updater-running", "updater-output")
    assert get_wizard_install_status() == (25, "wizard-running", "wizard-output")


def test_install_status_values_are_owned_by_storage(ds):
    percent = {"completed": 2}
    status = ["running"]
    output = {"lines": ["one"]}
    set_wizard_install_status(percent, status, output)

    percent["completed"] = 99
    status.append("mutated-after-store")
    output["lines"].append("mutated-after-store")
    assert get_wizard_install_status() == (
        {"completed": 2},
        ["running"],
        {"lines": ["one"]},
    )

    loaded_percent, loaded_status, loaded_output = get_wizard_install_status()
    assert isinstance(loaded_percent, dict)
    assert isinstance(loaded_status, list)
    assert isinstance(loaded_output, dict)
    loaded_percent["completed"] = 100
    loaded_status.append("mutated-after-load")
    loaded_output["lines"].append("mutated-after-load")
    assert get_wizard_install_status() == (
        {"completed": 2},
        ["running"],
        {"lines": ["one"]},
    )


def test_corrupt_install_status_write_is_rejected_without_losing_prior_state(ds):
    set_updater_install_status(50, "running", "line")
    with pytest.raises(sqlite3.IntegrityError):
        ds.set_blob("updater:status", "not-json")

    assert get_updater_install_status() == (50, "running", "line")


def test_pending_restart_defaults_to_nothing_owed(ds):
    """An install that has never deferred a restart must not look like one that
    has -- the flag drives a modal on the updater page."""
    assert get_update_restart_pending() is False


def test_pending_restart_round_trips(ds):
    set_update_restart_pending(True)
    assert get_update_restart_pending() is True
    set_update_restart_pending(False)
    assert get_update_restart_pending() is False
