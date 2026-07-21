"""Coverage for controller/runtime/system_commands.py's process_system_commands().

This module wraps grill_platform command dispatch: pop a queued command,
check it against the platform's supported-commands list, and either invoke
the corresponding grill_platform method or push back an "unsupported" error.

Neutralization note: process_system_commands() itself contains no
os.system/subprocess/reboot/shutdown calls -- those would live inside a real
grillplat command implementation (e.g. reboot, shutdown), reached only via
`getattr(grill_platform, command[0])`. Every test here uses a fully
in-process fake/Mock grill_platform (never a real platform module), so no
command implementation -- destructive or otherwise -- is ever actually
invoked; we only assert on how process_system_commands() dispatches to it.
"""

from unittest.mock import MagicMock

from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import InMemoryStore
from controller.runtime.system_commands import process_system_commands


def _ctx(grill_platform):
    store = InMemoryStore()
    return ControllerContext(
        devices=Devices(grill_platform=grill_platform, probe_complex=None, dist_device=None),
        store=store,
        notifications=None,
        clock=None,
    )


def test_empty_queue_does_nothing():
    grill_platform = MagicMock()
    ctx = _ctx(grill_platform)

    process_system_commands(ctx)

    grill_platform.supported_commands.assert_not_called()
    assert ctx.store.system_output().length() == 0


def test_supported_command_is_dispatched_and_result_pushed():
    grill_platform = MagicMock()
    grill_platform.supported_commands.return_value = {"data": {"supported_cmds": ["reboot"]}}
    grill_platform.reboot.return_value = {"result": "OK", "message": "Rebooting.", "data": {}}
    ctx = _ctx(grill_platform)
    ctx.store.system_commands().push(["reboot"])

    process_system_commands(ctx)

    grill_platform.reboot.assert_called_once_with(["reboot"])
    output = ctx.store.system_output().drain()
    assert len(output) == 1
    assert output[0]["result"] == "OK"
    assert output[0]["command"] == ["reboot"]


def test_unsupported_command_pushes_error_without_dispatch():
    grill_platform = MagicMock()
    grill_platform.supported_commands.return_value = {"data": {"supported_cmds": ["check_alive"]}}
    ctx = _ctx(grill_platform)
    ctx.store.system_commands().push(["shutdown"])

    process_system_commands(ctx)

    grill_platform.shutdown.assert_not_called()
    output = ctx.store.system_output().drain()
    assert len(output) == 1
    result = output[0]
    assert result["result"] == "ERROR"
    assert result["command"] == ["shutdown"]
    assert "shutdown" in result["message"]
    assert "not supported" in result["message"]


def test_supported_cmds_fetched_once_for_multiple_queued_commands():
    """supported_cmds is only looked up when the local cache is empty, so
    draining several queued commands in one call should hit
    supported_commands() exactly once, not once per command."""
    grill_platform = MagicMock()
    grill_platform.supported_commands.return_value = {"data": {"supported_cmds": ["check_alive"]}}
    grill_platform.check_alive.return_value = {"result": "OK", "message": "alive", "data": {}}
    ctx = _ctx(grill_platform)
    ctx.store.system_commands().push(["check_alive"])
    ctx.store.system_commands().push(["check_alive"])
    ctx.store.system_commands().push(["check_alive"])

    process_system_commands(ctx)

    assert grill_platform.supported_commands.call_count == 1
    assert grill_platform.check_alive.call_count == 3
    assert ctx.store.system_output().length() == 3


def test_command_with_extra_args_is_passed_through_whole():
    grill_platform = MagicMock()
    grill_platform.supported_commands.return_value = {"data": {"supported_cmds": ["set_pwm_frequency"]}}
    grill_platform.set_pwm_frequency.return_value = {"result": "OK", "message": "", "data": {}}
    ctx = _ctx(grill_platform)
    ctx.store.system_commands().push(["set_pwm_frequency", 100])

    process_system_commands(ctx)

    grill_platform.set_pwm_frequency.assert_called_once_with(["set_pwm_frequency", 100])
