"""Log rotation families -- grouping and stitching.

RotatingFileHandler(maxBytes=1 MiB, backupCount=3) shifts suffixes UPWARD on
rollover: `x.log` becomes `x.log.1`, `x.log.1` becomes `x.log.2`. So the
highest-numbered member is the OLDEST, and a viewer that wants chronological
order must read them in descending-index order.

Both Flask's logs page and the first cut of admin_api.list_logs() filtered on a
`.log` extension and therefore could not see a single rotated file. On the
development machine that hid 5 of 15 files, including the three largest.
"""

import pytest

import blueprints.api_admin.admin_api as admin_api


@pytest.fixture
def logdir(tmp_path):
    for name in (
        "events.log",
        "events.log.1",
        "events.log.2",
        "mqtt.log",
        "logfiles.txt",
        "notes.md",
    ):
        (tmp_path / name).write_text(f"{name}\n")
    return str(tmp_path)


def test_groups_members_by_stem(logdir):
    assert set(admin_api.list_log_families(logdir)) == {"events", "mqtt"}


def test_orders_members_oldest_first(logdir):
    assert admin_api.list_log_families(logdir)["events"] == [
        "events.log.2",
        "events.log.1",
        "events.log",
    ]


def test_excludes_non_log_names(logdir):
    """logs/logfiles.txt is a supervisord placeholder, not a log."""
    flat = [name for members in admin_api.list_log_families(logdir).values() for name in members]
    assert "logfiles.txt" not in flat
    assert "notes.md" not in flat


def test_missing_folder_is_empty_not_an_error(tmp_path):
    assert admin_api.list_log_families(str(tmp_path / "nope")) == {}
