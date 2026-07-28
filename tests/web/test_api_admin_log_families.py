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


#  ---- stitching -------------------------------------------------------------


@pytest.fixture
def stitchdir(tmp_path):
    (tmp_path / "events.log.2").write_text("oldest\n")
    (tmp_path / "events.log.1").write_text("middle\n")
    (tmp_path / "events.log").write_text("newest\n")
    return tmp_path


def test_concatenates_oldest_first(stitchdir):
    assert admin_api.stitch_family("events", str(stitchdir)) == b"oldest\nmiddle\nnewest\n"


def test_unknown_stem_is_none(stitchdir):
    assert admin_api.stitch_family("nosuch", str(stitchdir)) is None


def test_a_member_missing_its_trailing_newline_does_not_join_two_lines(stitchdir):
    """Without the guard, the last line of one member and the first of the next
    render as a single corrupt line in the viewer."""
    (stitchdir / "events.log.1").write_text("middle")
    assert admin_api.stitch_family("events", str(stitchdir)) == b"oldest\nmiddle\nnewest\n"


def test_a_path_shaped_stem_reads_nothing(tmp_path):
    """A REAL decoy sitting where `../` resolves.

    If containment were an os.path.isfile check, a nonexistent target would fake
    the pass and this test would prove nothing. The decoy exists, is readable,
    and is named exactly what the traversal would reach.
    """
    (tmp_path / "secret.log").write_text("SECRET\n")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "events.log").write_text("ok\n")

    assert (tmp_path / "secret.log").is_file()  # the decoy is real
    assert admin_api.stitch_family("../secret", str(logs)) is None
