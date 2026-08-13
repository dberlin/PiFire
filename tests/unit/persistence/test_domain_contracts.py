"""Observable persistence contracts shared across domain extractions."""

import sqlite3

import pytest

from common.control_delta import control_delta
from common.persistence.control import (
    read_pending_control_writes,
)
from common.persistence.runtime import (
    clear_warnings_through,
    read_warnings_snapshot,
    write_warning,
)
from common.persistence.install_state import (
    delete_wizard_install_info,
    get_updater_install_status,
    get_wizard_install_status,
    load_os_info,
    load_wizard_install_info,
    set_updater_install_status,
    set_wizard_install_status,
    store_wizard_install_info,
)
from controller.runtime.store import SqliteStore


def test_control_live_update_and_dequeue_roll_back_together(ds):
    store = SqliteStore()
    store.write_control_snapshot(
        {"mode": "Stop", "primary_setpoint": 100},
        origin="characterization",
    )
    store.enqueue_control_delta(
        control_delta(set_values={"primary_setpoint": 225}),
        origin="characterization",
    )
    pending = read_pending_control_writes()
    ds.connection().execute(
        """
        CREATE TEMP TRIGGER fail_control_dequeue
        BEFORE DELETE ON queue_control_write
        BEGIN
            SELECT RAISE(ABORT, 'simulated control dequeue failure');
        END
        """
    )

    try:
        with pytest.raises(sqlite3.IntegrityError, match="simulated control dequeue failure"):
            store.execute_control_writes()

        assert store.read_control() == {"mode": "Stop", "primary_setpoint": 100}
        assert read_pending_control_writes() == pending
    finally:
        ds.connection().execute("DROP TRIGGER fail_control_dequeue")

    store.execute_control_writes()
    assert store.read_control() == {"mode": "Stop", "primary_setpoint": 225}
    assert read_pending_control_writes() == ()


def test_warning_clear_failure_preserves_snapshot_and_later_writes(ds):
    write_warning("seen-first")
    write_warning("seen-second")
    snapshot = read_warnings_snapshot()
    write_warning("written-after-snapshot")
    ds.connection().execute(
        """
        CREATE TEMP TRIGGER fail_warning_clear
        BEFORE DELETE ON list_warnings
        BEGIN
            SELECT RAISE(ABORT, 'simulated warning clear failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="simulated warning clear failure"):
        clear_warnings_through(snapshot["max_id"])

    assert read_warnings_snapshot()["warnings"] == [
        "seen-first",
        "seen-second",
        "written-after-snapshot",
    ]

    ds.connection().execute("DROP TRIGGER fail_warning_clear")
    clear_warnings_through(snapshot["max_id"])
    assert read_warnings_snapshot()["warnings"] == ["written-after-snapshot"]


def test_install_state_absence_defaults_and_namespaces_are_stable(ds):
    assert load_os_info() == {}
    assert get_wizard_install_status() == (None, None, None)
    assert get_updater_install_status() == (None, None, None)

    set_wizard_install_status(25, "wizard-running", "wizard-output")
    assert get_wizard_install_status() == (25, "wizard-running", "wizard-output")
    assert get_updater_install_status() == (None, None, None)

    set_updater_install_status(75, "updater-running", "updater-output")
    assert get_updater_install_status() == (75, "updater-running", "updater-output")
    assert get_wizard_install_status() == (25, "wizard-running", "wizard-output")


def test_wizard_install_blob_round_trip_delete_and_absent_error(ds):
    with pytest.raises(TypeError):
        load_wizard_install_info()

    payload = {"platform": "raspberry-pi", "steps": ["os", "hardware"]}
    store_wizard_install_info(payload)
    assert load_wizard_install_info() == payload

    delete_wizard_install_info()
    with pytest.raises(TypeError):
        load_wizard_install_info()
