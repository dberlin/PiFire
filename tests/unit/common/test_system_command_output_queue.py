"""`queue_systemo` is shared by every consumer, so a poll must not eat entries
that are not its own.

`get_system_command_output(requested=...)` popped entries off `queue_systemo`
one at a time and **threw away** every entry whose command did not match
`requested`. The queue has many concurrent consumers -- system-command routes,
``get_supported_cmds`` for ``supported_commands``, ``common/system.py``'s
system-info gather for ``check_wifi_quality`` / ``check_throttled`` /
``check_cpu_temp`` / ``network_info`` / ``hardware_info``, and the wizard for
``scan_bluetooth`` -- all against the one table. A poll that ran while another
consumer's answer was sitting in the queue destroyed that answer, and the other
consumer then busy-waited out its whole timeout and returned the
"could not be found" error envelope for a command the control process had in
fact answered.

Non-matching entries are now left in the queue for the consumer they belong to.
"""

import pytest

from common.app import get_system_command_output
from common.sqlite_queue import SqliteQueue
from controller.runtime.clock import ManualClock


def _out(command, result="OK"):
    return {"command": [command, None, None, None], "result": result, "message": None, "data": {}}


@pytest.fixture
def systemo(ds):
    q = SqliteQueue("queue_systemo")
    q.flush()
    return q


def test_a_miss_leaves_another_consumers_entry_alone(systemo):
    systemo.push(_out("supported_commands"))

    # A different consumer polls for its own command and finds nothing.
    missed = get_system_command_output(requested="check_alive", timeout=0.05)
    assert missed["result"] == "ERROR"

    # ...and must not have destroyed the answer the other consumer is waiting for.
    assert systemo.list() == [_out("supported_commands")]
    assert get_system_command_output(requested="supported_commands", timeout=0.05)["result"] == "OK"


def test_a_hit_consumes_only_the_matching_entry(systemo):
    systemo.push(_out("check_wifi_quality"))
    systemo.push(_out("check_alive"))
    systemo.push(_out("check_cpu_temp"))

    data = get_system_command_output(requested="check_alive", timeout=0.05)
    assert data["command"][0] == "check_alive"

    remaining = [entry["command"][0] for entry in systemo.list()]
    assert sorted(remaining) == ["check_cpu_temp", "check_wifi_quality"]


def test_every_consumer_gets_its_own_answer_regardless_of_poll_order(systemo):
    """The end-to-end shape of the bug: N answers queued, N consumers, none lost."""
    commands = ["check_wifi_quality", "check_throttled", "check_cpu_temp", "network_info", "hardware_info"]
    for command in commands:
        systemo.push(_out(command))

    # Poll in an order unrelated to the queue order.
    for command in reversed(commands):
        data = get_system_command_output(requested=command, timeout=0.05)
        assert data["result"] == "OK", f"{command} was consumed by another consumer's poll"
        assert data["command"][0] == command

    assert systemo.list() == []


def test_missing_entry_still_returns_the_error_envelope(systemo):
    data = get_system_command_output(requested="check_alive", timeout=0.05)
    assert data == {
        "command": ["check_alive", None, None, None],
        "result": "ERROR",
        "message": "The requested command output could not be found.",
        "data": {"Response_Was": "To_Fast"},
    }


@pytest.mark.parametrize("wall_jump", [-3600, 3600])
def test_output_queue_timeout_ignores_wall_steps(systemo, monkeypatch, wall_jump):
    from common import common

    clock = ManualClock(wall_start=1_800_000_000)
    systemo.push(_out("supported_commands"))
    monkeypatch.setattr(common.time, "time", clock.wall_time)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.jump_wall(wall_jump)
        clock.advance(seconds)
        assert clock.monotonic() < 1, "queue wait exceeded its steady budget"

    monkeypatch.setattr(common.time, "sleep", sleep)
    missed = get_system_command_output("missing", timeout=0.1, monotonic=clock.monotonic)
    assert missed["result"] == "ERROR"
    assert clock.monotonic() == pytest.approx(0.1)
    assert sleeps == [0.025] * 4
    assert systemo.list() == [_out("supported_commands")]
    assert get_system_command_output("supported_commands", monotonic=clock.monotonic) == _out("supported_commands")
    assert clock.monotonic() == pytest.approx(0.1)
