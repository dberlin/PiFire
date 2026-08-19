"""Pins common/server_revision.py, which is how a stale gunicorn worker is caught.

Both ends matter: this module publishes the fields, and
web-react/tests/e2e/globalSetup.ts refuses to run the suite when `stale` is
true. A rename here silently disarms the guard there, so the field NAMES are
asserted, not just the behaviour.
"""

import os
import time

from common import server_revision


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


SHA = "1b7b2f5a3c973512b8861a90eb50dda060dd45d9"


def test_reads_a_detached_head_inline(tmp_path):
    _write(os.path.join(str(tmp_path), ".git", "HEAD"), SHA + "\n")
    assert server_revision.read_revision(str(tmp_path)) == SHA


def test_follows_a_ref_to_its_file(tmp_path):
    root = str(tmp_path)
    _write(os.path.join(root, ".git", "HEAD"), "ref: refs/heads/main\n")
    _write(os.path.join(root, ".git", "refs", "heads", "main"), SHA + "\n")
    assert server_revision.read_revision(root) == SHA


def test_falls_back_to_packed_refs(tmp_path):
    """A ref that has been packed has no file of its own -- the common case on a
    freshly cloned PiFire, where nothing has written a loose ref yet."""
    root = str(tmp_path)
    _write(os.path.join(root, ".git", "HEAD"), "ref: refs/heads/main\n")
    _write(
        os.path.join(root, ".git", "packed-refs"),
        f"# pack-refs with: peeled fully-peeled sorted\n{SHA} refs/heads/main\n",
    )
    assert server_revision.read_revision(root) == SHA


def test_no_git_directory_is_none_not_an_exception(tmp_path):
    """A release tarball has no .git. That must degrade, not 500 the endpoint."""
    assert server_revision.read_revision(str(tmp_path)) is None


def test_status_publishes_the_field_names_globalsetup_reads():
    """globalSetup.ts indexes these by name; renaming one disarms the guard."""
    payload = server_revision.status()
    assert set(payload) == {"revision", "started_at", "newest_source_mtime", "stale"}
    assert isinstance(payload["stale"], bool)


def test_a_source_file_touched_after_startup_reports_stale(monkeypatch):
    """The actual failure this exists for: the worker forked, then code changed."""
    monkeypatch.setattr(server_revision, "STARTED_AT", time.time())
    monkeypatch.setattr(server_revision, "newest_source_mtime", lambda *a, **k: time.time() + 60)
    assert server_revision.status()["stale"] is True


def test_an_untouched_tree_is_not_stale(monkeypatch):
    monkeypatch.setattr(server_revision, "STARTED_AT", time.time() + 60)
    monkeypatch.setattr(server_revision, "newest_source_mtime", lambda *a, **k: time.time())
    assert server_revision.status()["stale"] is False


def test_newest_source_mtime_ignores_modules_outside_the_repo():
    """Otherwise a dependency's mtime -- reinstalled by any `uv sync` -- would
    report the app as stale."""
    assert server_revision.newest_source_mtime("/nonexistent-root") == 0.0
