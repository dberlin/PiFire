# tests/unit/common/test_get_os_info_error_path.py
"""Regression test for get_os_info()'s error-handling path.

get_os_info() (common/system.py) wraps its os-release/uname probing in a
broad try/except and, on failure, is supposed to log the error and return
gracefully (the partial os_info dict built so far) rather than raising.

Its except-block used to call:

    write_log(event, level="error", loggername=loggername)

but write_log()'s signature is `write_log(event, loggername="events")` --
there is no `level` parameter. Python raises TypeError while binding the
call's arguments (before write_log's body ever runs) whenever an unexpected
`level` kwarg is passed. So the very handler meant to swallow the original
failure and return gracefully instead replaced it with a new, unhandled
TypeError that propagated out of get_os_info() -- the opposite of graceful
degradation.

This test forces get_os_info()'s try-block to fail (subprocess.check_output,
used for the `uname -m` architecture probe, raising OSError) and asserts
get_os_info() returns normally (the os_info dict) instead of raising.

Isolation notes:
  * write_log()'s create_logger() call hardcodes filename="./logs/events.log"
    (not derived from the `loggername` argument), so the effective log
    destination follows the CWD. The test chdirs into a tmp_path with its own
    logs/ directory so it neither depends on nor pollutes the real project's
    logs/events.log.
  * A per-test unique loggername avoids reusing (and re-attaching handlers
    to) a logger object some other test in the same process already created
    under the name "events".
  * The `ds` fixture repoints the sqlite datastore at a tmp DB so
    SqliteLogHandler's emit() (attached by create_logger) doesn't touch the
    real pifire.db.
"""

import uuid
from unittest import mock

from common.system import get_os_info


def test_get_os_info_returns_gracefully_when_uname_probe_fails(tmp_path, monkeypatch, ds):
    (tmp_path / "logs").mkdir()
    monkeypatch.chdir(tmp_path)

    logger_name = f"events-test-{uuid.uuid4().hex}"

    with mock.patch("common.system.subprocess.check_output", side_effect=OSError("boom")):
        os_info = get_os_info(filepath=str(tmp_path / "os_info.json"), loggername=logger_name)

    # The bug: write_log(event, level="error", loggername=...) raised
    # TypeError before this line was ever reached. Reaching it -- and getting
    # back the dict rather than an exception -- is the fix under test.
    assert isinstance(os_info, dict)

    log_file = tmp_path / "logs" / "events.log"
    assert log_file.exists(), "the except-block's write_log() call never completed"
    assert "Error getting OS info" in log_file.read_text()
    assert "boom" in log_file.read_text()
