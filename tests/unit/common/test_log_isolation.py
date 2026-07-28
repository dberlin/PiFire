"""The test suite must not append to the operator's real ./logs/.

It did. logs/events.log on the development machine carried lines like
"Admin: Shutdown failed: boom", "[nonexistent_probe_module_xyz]" and a WLED
connection to 127.0.0.1:1 -- fixture strings, in the file the log viewer shows
the user. It also made the files and the `logs` table diverge, because tests use
a temporary database but were writing to the real log directory.

Two pieces are needed, not one. LOG_DIR redirects where new handlers write, and
reset_loggers() detaches the handlers that already exist -- app.py builds its
loggers at IMPORT time, before any fixture can run, so a redirect alone would
leave those pointed at ./logs/.
"""

import logging
import os

from common import common as common_mod


def test_log_path_derives_from_the_module_constant(monkeypatch, tmp_path):
    monkeypatch.setattr(common_mod, "LOG_DIR", str(tmp_path))
    assert common_mod.log_path("events.log") == os.path.join(str(tmp_path), "events.log")


def test_create_logger_writes_where_log_dir_points(monkeypatch, tmp_path):
    monkeypatch.setattr(common_mod, "LOG_DIR", str(tmp_path))
    common_mod.reset_loggers()

    logger = common_mod.create_logger("isolation-probe", filename=common_mod.log_path("probe.log"))
    logger.info("hello")

    assert (tmp_path / "probe.log").read_text().endswith("hello\n")


def test_reset_loggers_detaches_handlers_so_they_rebuild(monkeypatch, tmp_path):
    monkeypatch.setattr(common_mod, "LOG_DIR", str(tmp_path))
    common_mod.create_logger("isolation-probe-2", filename=common_mod.log_path("probe2.log"))
    assert logging.getLogger("isolation-probe-2").handlers

    common_mod.reset_loggers()

    assert not logging.getLogger("isolation-probe-2").handlers


def test_the_suite_is_already_redirected_away_from_the_repo_logs_dir():
    """The autouse fixture in tests/conftest.py runs before any test body."""
    assert os.path.abspath(common_mod.LOG_DIR) != os.path.abspath("./logs")


def test_writing_an_event_does_not_touch_the_repo_logs_dir(tmp_path):
    """write_log() is the call that polluted the real file."""
    before = sorted(os.listdir("./logs")) if os.path.isdir("./logs") else []
    sizes_before = {n: os.path.getsize(os.path.join("./logs", n)) for n in before}

    common_mod.write_log("isolation canary -- must never reach the real events.log")

    after = sorted(os.listdir("./logs")) if os.path.isdir("./logs") else []
    sizes_after = {n: os.path.getsize(os.path.join("./logs", n)) for n in after}
    assert after == before
    assert sizes_after == sizes_before
