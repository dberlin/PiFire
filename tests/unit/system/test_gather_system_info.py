"""Tests for common/system.py's gather_system_info(): the shared 'sys' command
dispatch sequence (wifi quality / throttle / cpu temp / network / hardware
info) that used to be independently reimplemented in admin routes and the
mobile socketio handler.

gather_system_info() does deferred imports (`from common.api_commands import
process_command` and `from common.app import get_supported_cmds,
get_system_command_output`) specifically to dodge a module-load-time circular
import (system -> app -> api_commands -> system). Because those imports
happen at call time, patching the *source* module's attribute
(common.api_commands.process_command, common.app.get_supported_cmds/
get_system_command_output) -- rather than common.system's local name -- is
what actually takes effect.
"""

from unittest import mock

import common.system as cc
from common.control_delta import CONTROL_DELTA_KEY


def _ok(requested, data):
    return {"command": [requested, None, None, None], "result": "OK", "message": "ok", "data": data}


def _err(requested, message="boom"):
    return {"command": [requested, None, None, None], "result": "ERROR", "message": message, "data": {}}


def _base_control():
    return {"system": {}}


def test_gather_system_info_empty_supported_cmds_keeps_defaults_and_writes_control():
    control = _base_control()
    popen_result = mock.Mock()
    popen_result.readline.return_value = " 12:00:00 up 1 day\n"

    with (
        mock.patch("common.app.get_supported_cmds", return_value=[]),
        mock.patch("common.api_commands.process_command") as process_command,
        mock.patch.object(cc, "enqueue_control_delta") as enqueue_control_delta,
        mock.patch.object(cc, "get_display_os_info", return_value={"PRETTY_NAME": "Test OS"}),
        mock.patch.object(cc.os, "popen", return_value=popen_result),
    ):
        system_info, failures = cc.gather_system_info(control, origin="unit-test")

    process_command.assert_not_called()
    assert failures == []
    assert system_info["uptime"] == " 12:00:00 up 1 day\n"
    assert system_info["os_info"] == {"PRETTY_NAME": "Test OS"}
    # Defaults for network/hardware info are left untouched since neither
    # 'network_info' nor 'hardware_info' was in supported_cmds.
    assert system_info["network_info"] == {"Unknown": {"ip_address": "0.0.0.0", "mac_address": "00:00:00:00:00:00"}}
    assert system_info["hardware_info"]["total_ram"] == "Unknown"
    # Nothing was probed, so the delta names nothing under "system". It is still
    # WRITTEN -- the call itself is the observable this pins -- but it imposes no
    # stale reading on a concurrent writer, which the old whole-dict write did.
    enqueue_control_delta.assert_called_once_with({CONTROL_DELTA_KEY: 1, "set": {"system": {}}}, origin="unit-test")


def test_gather_system_info_all_commands_ok_populates_control_and_system_info():
    control = _base_control()
    outputs = {
        "check_wifi_quality": _ok(
            "check_wifi_quality",
            {"wifi_quality_value": 42, "wifi_quality_max": 70, "wifi_quality_percentage": 60.0},
        ),
        "check_throttled": _ok("check_throttled", {"cpu_throttled": False, "cpu_under_voltage": False}),
        "check_cpu_temp": _ok("check_cpu_temp", {"cpu_temp": 55.5}),
        "network_info": _ok("network_info", {"eth0": {"ip_address": "192.168.1.5", "mac_address": "aa:bb"}}),
        "hardware_info": _ok(
            "hardware_info",
            {"total_ram": "4GB", "available_ram": "2GB", "cpu_info": {"cores": 4}},
        ),
    }
    supported = list(outputs.keys())

    with (
        mock.patch("common.app.get_supported_cmds", return_value=supported),
        mock.patch("common.api_commands.process_command") as process_command,
        mock.patch("common.app.get_system_command_output", side_effect=lambda requested, **kw: outputs[requested]),
        mock.patch.object(cc, "enqueue_control_delta") as enqueue_control_delta,
        mock.patch.object(cc, "get_display_os_info", return_value={}),
        mock.patch.object(cc.os, "popen", return_value=mock.Mock(readline=mock.Mock(return_value="up\n"))),
    ):
        system_info, failures = cc.gather_system_info(control, origin="admin")

    assert failures == []
    assert process_command.call_count == 5

    assert control["system"]["wifi_quality_value"] == 42
    assert control["system"]["wifi_quality_max"] == 70
    assert control["system"]["wifi_quality_percentage"] == 60.0
    assert control["system"]["cpu_throttled"] is False
    assert control["system"]["cpu_under_voltage"] is False
    assert control["system"]["cpu_temp"] == 55.5

    assert system_info["network_info"] == {"eth0": {"ip_address": "192.168.1.5", "mac_address": "aa:bb"}}
    assert system_info["hardware_info"]["total_ram"] == "4GB"

    # The delta names exactly the six members this call assigned -- not the whole
    # control dict, and not the system members it never probed.
    enqueue_control_delta.assert_called_once_with(
        {
            CONTROL_DELTA_KEY: 1,
            "set": {
                "system": {
                    "wifi_quality_value": 42,
                    "wifi_quality_max": 70,
                    "wifi_quality_percentage": 60.0,
                    "cpu_throttled": False,
                    "cpu_under_voltage": False,
                    "cpu_temp": 55.5,
                }
            },
        },
        origin="admin",
    )


def test_gather_system_info_throttled_or_undervoltage_adds_failure_message():
    control = _base_control()
    outputs = {
        "check_throttled": _ok("check_throttled", {"cpu_throttled": True, "cpu_under_voltage": False}),
    }

    with (
        mock.patch("common.app.get_supported_cmds", return_value=["check_throttled"]),
        mock.patch("common.api_commands.process_command"),
        mock.patch("common.app.get_system_command_output", side_effect=lambda requested, **kw: outputs[requested]),
        mock.patch.object(cc, "enqueue_control_delta"),
        mock.patch.object(cc, "get_display_os_info", return_value={}),
        mock.patch.object(cc.os, "popen", return_value=mock.Mock(readline=mock.Mock(return_value="up\n"))),
    ):
        _, failures = cc.gather_system_info(control, origin="admin")

    assert control["system"]["cpu_throttled"] is True
    assert any("Throttled / Undervoltage" in f for f in failures)


def test_gather_system_info_command_error_results_append_message_and_null_data():
    control = _base_control()
    outputs = {
        "check_wifi_quality": _err("check_wifi_quality", "wifi tool not found"),
        "check_throttled": _err("check_throttled", "throttle tool not found"),
        "check_cpu_temp": _err("check_cpu_temp", "temp tool not found"),
        "network_info": _err("network_info", "net tool not found"),
        "hardware_info": _err("hardware_info", "hw tool not found"),
    }
    supported = list(outputs.keys())

    with (
        mock.patch("common.app.get_supported_cmds", return_value=supported),
        mock.patch("common.api_commands.process_command"),
        mock.patch("common.app.get_system_command_output", side_effect=lambda requested, **kw: outputs[requested]),
        mock.patch.object(cc, "enqueue_control_delta"),
        mock.patch.object(cc, "get_display_os_info", return_value={}),
        mock.patch.object(cc.os, "popen", return_value=mock.Mock(readline=mock.Mock(return_value="up\n"))),
    ):
        system_info, failures = cc.gather_system_info(control, origin="admin")

    assert "wifi tool not found" in failures
    assert "throttle tool not found" in failures
    assert "temp tool not found" in failures
    assert "net tool not found" in failures
    assert "hw tool not found" in failures
    # control["system"] fields are still set (to None) from the empty data
    # dict on an ERROR result.
    assert control["system"]["wifi_quality_value"] is None
    assert control["system"]["cpu_throttled"] is None
    assert control["system"]["cpu_temp"] is None
    # network_info/hardware_info system_info entries are left at their defaults
    # since the ERROR branch does not overwrite them.
    assert system_info["network_info"] == {"Unknown": {"ip_address": "0.0.0.0", "mac_address": "00:00:00:00:00:00"}}
    assert system_info["hardware_info"]["total_ram"] == "Unknown"


def test_gather_system_info_network_info_ok_but_empty_data_keeps_default():
    """A network_info command that returns result=OK but an empty/falsy
    'data' payload must NOT overwrite the seeded default (the `if
    network_info:` guard)."""
    control = _base_control()
    outputs = {"network_info": _ok("network_info", {})}

    with (
        mock.patch("common.app.get_supported_cmds", return_value=["network_info"]),
        mock.patch("common.api_commands.process_command"),
        mock.patch("common.app.get_system_command_output", side_effect=lambda requested, **kw: outputs[requested]),
        mock.patch.object(cc, "enqueue_control_delta"),
        mock.patch.object(cc, "get_display_os_info", return_value={}),
        mock.patch.object(cc.os, "popen", return_value=mock.Mock(readline=mock.Mock(return_value="up\n"))),
    ):
        system_info, failures = cc.gather_system_info(control, origin="admin")

    assert failures == []
    assert system_info["network_info"] == {"Unknown": {"ip_address": "0.0.0.0", "mac_address": "00:00:00:00:00:00"}}
