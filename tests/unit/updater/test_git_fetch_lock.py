"""Serialization contracts for updater Git ref writes."""

from __future__ import annotations

import subprocess
import sys
from contextlib import contextmanager

import pytest

import updater


def _probe_lock(lock_path):
    script = """
import fcntl
import pathlib
import sys

with pathlib.Path(sys.argv[1]).open("a+") as lock_file:
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(73)
"""
    return subprocess.run([sys.executable, "-c", script, str(lock_path)], check=False)


def test_git_fetch_transaction_excludes_another_process(tmp_path):
    lock_path = tmp_path / "controller" / "_native" / "git-fetch.lock"

    with updater.git_fetch_transaction(tmp_path):
        assert _probe_lock(lock_path).returncode == 73

    assert _probe_lock(lock_path).returncode == 0


def test_git_fetch_transaction_releases_after_exception(tmp_path):
    with pytest.raises(RuntimeError, match="fetch failed"), updater.git_fetch_transaction(tmp_path):
        raise RuntimeError("fetch failed")

    with updater.git_fetch_transaction(tmp_path):
        pass


def test_every_updater_fetch_holds_the_fetch_transaction(monkeypatch):
    transaction_depth = 0
    fetches = []

    @contextmanager
    def guarded_fetch_transaction(repo_root=None):
        nonlocal transaction_depth
        transaction_depth += 1
        try:
            yield
        finally:
            transaction_depth -= 1

    def run(command, **kwargs):
        if command[:2] == ["git", "fetch"]:
            assert transaction_depth == 1, f"fetch escaped transaction: {command}"
            fetches.append(tuple(command))
        if command[:3] == ["git", "remote", "set-branches"]:
            assert transaction_depth == 1, "remote refspec changed outside fetch transaction"
        stdout = ""
        if command[:2] == ["git", "rev-list"]:
            stdout = "0\n"
        elif command[:2] == ["git", "tag"]:
            stdout = "v1.25.1\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(updater, "git_fetch_transaction", guarded_fetch_transaction, raising=False)
    monkeypatch.setattr(updater.subprocess, "run", run)
    monkeypatch.setattr(updater, "get_remote_url", lambda: ("https://example.invalid/PiFire", ""))
    monkeypatch.setattr(updater, "get_branch", lambda: ("massive-reworks-and-new-ui", ""))

    assert updater.update_remote_branches() == ""
    assert updater.get_available_updates()["success"] is True
    assert updater.do_update() == ("<br>", "")
    assert updater.get_remote_version() == ("v1.25.1", "")
    assert updater._install_update_checkout()[0] is True

    assert fetches == [
        ("git", "fetch"),
        ("git", "fetch"),
        ("git", "fetch", "--all"),
        ("git", "fetch", "--tags", "--force"),
        ("git", "fetch"),
    ]
