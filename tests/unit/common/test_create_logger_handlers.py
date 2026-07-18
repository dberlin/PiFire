# tests/unit/common/test_create_logger_handlers.py
"""Regression tests for create_logger()'s handler-attachment guard.

create_logger() decided whether to attach its RotatingFileHandler /
SqliteLogHandler with `if not logger.hasHandlers():`. `Logger.hasHandlers()`
walks ANCESTOR loggers too (per the stdlib docs), so once anything -- pytest's
own logging plugin, or any embedding host -- has attached a handler to the
ROOT logger, hasHandlers() returns True for every child logger regardless of
whether that child has handlers of its own. create_logger() then never
attaches its own handlers, so write_log()/write_event() never reach
./logs/events.log; messages only flow to whatever captured the root logger.

The fix checks `logger.handlers` instead -- THIS logger's own handler list --
which is also the correct (and only correct) guard against re-adding
duplicate handlers on repeated create_logger() calls for the same name.

See tests/web/test_page_smallpages.py's module docstring for the workaround
the web-suite had to use (appending directly to events.log) before this fix.
"""

import logging
import uuid

import pytest

from common.common import create_logger


@pytest.fixture
def configured_root_logger():
    """Simulate the pytest/embedding-host condition that defeats
    hasHandlers(): a handler already sits on the ROOT logger before
    create_logger() ever runs."""
    root = logging.getLogger()
    handler = logging.NullHandler()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)


def _unique_logger_name():
    # A fresh name per test so loggers (module-level singletons in the
    # logging package) never leak handlers or state across tests.
    return f"events-test-{uuid.uuid4().hex}"


def test_logger_reaches_file_when_root_logger_is_already_configured(tmp_path, ds, configured_root_logger):
    """End-to-end: with the root logger pre-configured (the pytest/embedding
    condition), a line logged the way write_log() does must still land in the
    target file."""
    log_file = tmp_path / "events.log"
    logger_name = _unique_logger_name()

    # Mirrors what write_log() does: create_logger(...) then logger.info(event).
    logger = create_logger(
        logger_name,
        filename=str(log_file),
        messageformat="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.INFO,
    )
    marker = f"marker-{uuid.uuid4().hex}"
    logger.info(marker)
    for handler in logger.handlers:
        handler.flush()

    assert log_file.exists(), "FileHandler never attached -- create_logger()'s guard suppressed it"
    assert marker in log_file.read_text()


def test_repeated_create_logger_calls_do_not_duplicate_handlers(tmp_path, ds, configured_root_logger):
    """Regression check for the fix itself: `logger.handlers` must still
    guard against re-adding handlers when create_logger() is called again
    for the same logger name (e.g. every write_log() call)."""
    log_file = tmp_path / "events.log"
    logger_name = _unique_logger_name()

    logger_first = create_logger(logger_name, filename=str(log_file))
    handler_count_after_first = len(logger_first.handlers)
    assert handler_count_after_first > 0, "expected handlers to attach on first call"

    logger_second = create_logger(logger_name, filename=str(log_file))
    assert logger_second is logger_first, "create_logger should return the same named logger"
    assert len(logger_second.handlers) == handler_count_after_first, (
        "calling create_logger() again for the same name must not add duplicate handlers"
    )

    marker = f"marker-{uuid.uuid4().hex}"
    logger_second.info(marker)
    for handler in logger_second.handlers:
        handler.flush()

    lines = log_file.read_text().splitlines()
    matching_lines = [line for line in lines if marker in line]
    assert len(matching_lines) == 1, f"expected exactly one log line for the marker, got {matching_lines}"
