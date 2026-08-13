"""init() reports a settings tree that does not match the models, and does
nothing else about it.

Write-gating, the migration registry and the shape digest between them mean a
tree that fails here got that way outside this code -- a hand-edited database, a
downgrade, a migration bug. Each is worth a log line and none is worth refusing
to start a control loop over.
"""

from unittest import mock

from common import datastore
from common.persistence.runtime import read_settings, write_settings_store


def test_a_valid_tree_reports_nothing(ds, caplog):
    # write_log() (common/common.py) always logs at INFO -- it takes no level
    # argument -- so the capture level here has to be INFO, not WARNING, or
    # the line below would pass regardless of whether anything was reported.
    with caplog.at_level("INFO", logger="events"):
        datastore._validate_settings_in_store()

    assert "settings" not in caplog.text.lower()


def test_a_broken_tree_is_reported_with_its_paths(ds, caplog):
    settings = read_settings()
    settings["startup"]["duration"] = "not a number"
    write_settings_store(settings)  # bypasses the write gate on purpose

    with caplog.at_level("INFO", logger="events"):
        datastore._validate_settings_in_store()

    assert "startup.duration" in caplog.text


def test_a_broken_tree_does_not_raise(ds):
    settings = read_settings()
    settings["startup"]["duration"] = "not a number"
    write_settings_store(settings)

    datastore._validate_settings_in_store()  # must not raise


def test_a_broken_tree_is_left_exactly_as_it_was(ds):
    # Observe-only: no stripping, no coercion, no normalised dump written back.
    settings = read_settings()
    settings["startup"]["duration"] = "not a number"
    write_settings_store(settings)

    datastore._validate_settings_in_store()

    assert read_settings()["startup"]["duration"] == "not a number"


def test_init_runs_it_after_the_migrations(ds):
    # Ordering matters: anything it reports has to be something the migration
    # steps could not fix, or every pre-migration tree would log on every boot.
    calls = []
    with (
        mock.patch.object(datastore, "connection", lambda: calls.append("connection")),
        mock.patch.object(datastore, "_drop_legacy_error_blobs", lambda: calls.append("drop")),
        mock.patch.object(datastore, "_first_boot_import", lambda: calls.append("import")),
        mock.patch.object(datastore, "_upgrade_settings_in_store", lambda: calls.append("settings")),
        mock.patch.object(datastore, "_upgrade_pellets_in_store", lambda: calls.append("pellets")),
        mock.patch.object(datastore, "_validate_settings_in_store", lambda: calls.append("validate")),
    ):
        datastore.init()

    assert calls == ["connection", "drop", "import", "settings", "pellets", "validate"]
