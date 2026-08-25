"""/api/admin/logs/view -- one rotation family as Range-capable plain text.

The endpoint takes a family STEM, never a filename and never a path. That is the
containment story: with no client-supplied path component to concatenate, the
two holes in blueprints/logs/routes.py (send_file and read_log_file, both of
which join a request field onto the logs folder) have nowhere to land.

Range support is not a nicety. The client tails the log by asking for
`bytes=<offset>-`, and the 416 response's `Content-Range: bytes * /<size>` is how
it learns the family rotated out from under its cursor. Without that header the
tail silently stops updating, which on a grill reads as a dead appliance.
"""

import os

import pytest

from blueprints.api_admin import admin_api


@pytest.fixture
def env(ds, tmp_path, monkeypatch):
    from app import app as flask_app

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "events.log.1").write_text("old\n")
    (log_dir / "events.log").write_text("new\n")
    monkeypatch.setattr(admin_api, "LOG_FOLDER", str(log_dir) + os.sep)

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        yield {"client": client, "dir": log_dir}


def test_serves_the_whole_family_as_plain_text(env):
    resp = env["client"].get("/api/admin/logs/view?log=events")
    assert resp.status_code == 200
    assert resp.data == b"old\nnew\n"
    assert resp.mimetype == "text/plain"
    assert resp.headers["Accept-Ranges"] == "bytes"


def test_a_range_returns_only_the_tail(env):
    resp = env["client"].get("/api/admin/logs/view?log=events", headers={"Range": "bytes=4-"})
    assert resp.status_code == 206
    assert resp.data == b"new\n"
    assert resp.headers["Content-Range"] == "bytes 4-7/8"


def test_past_the_end_reports_the_total_size(env):
    """The client compares this total against its cursor to detect rotation."""
    resp = env["client"].get("/api/admin/logs/view?log=events", headers={"Range": "bytes=9999-"})
    assert resp.status_code == 416
    assert resp.headers["Content-Range"] == "bytes */8"


def test_unknown_family_is_404(env):
    resp = env["client"].get("/api/admin/logs/view?log=nosuch")
    assert resp.status_code == 404
    assert resp.get_json()["message"] == "not_found"
    assert resp.get_json()["data"] == {"log": "nosuch"}


def test_a_path_shaped_family_is_404(env, tmp_path):
    """A REAL decoy where `../` resolves -- see test_api_admin_log_families."""
    (tmp_path / "secret.log").write_text("SECRET\n")
    assert (tmp_path / "secret.log").is_file()

    resp = env["client"].get("/api/admin/logs/view?log=../secret")
    assert resp.status_code == 404
    assert b"SECRET" not in resp.data


def test_download_flag_sets_an_attachment_name(env):
    resp = env["client"].get("/api/admin/logs/view?log=events&download=1")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "events.log" in resp.headers["Content-Disposition"]


def test_without_the_download_flag_it_renders_inline(env):
    resp = env["client"].get("/api/admin/logs/view?log=events")
    assert "attachment" not in resp.headers.get("Content-Disposition", "")


def test_listing_reports_families_alongside_the_flat_list(env):
    body = env["client"].get("/api/admin/logs").get_json()["data"]
    #  `logs` keeps its exact shipped contract: the admin page's LogsCard is
    #  built against it, and a rotated member appearing there would put
    #  `events.log.1` in a list whose every other entry is downloadable by name.
    assert body["logs"] == ["events.log"]
    #  `bytes` is the STITCHED total across the family, which is what the
    #  client seeds its tail cursor from -- the size of events.log alone would
    #  start the cursor mid-stream and the first poll would replay old lines.
    assert body["families"] == [{"stem": "events", "members": ["events.log.1", "events.log"], "bytes": 8}]
