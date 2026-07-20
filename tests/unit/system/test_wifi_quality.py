import subprocess
from unittest import mock

import pytest

import common.system as cc
from common.system import get_wifi_quality


IWCONFIG_OUTPUT = b"""wlan0     IEEE 802.11  ESSID:"MyNet"
          Mode:Managed  Frequency:5.18 GHz  Access Point: AA:BB:CC:DD:EE:FF
          Bit Rate=433 Mb/s   Tx-Power=22 dBm
          Link Quality=61/70  Signal level=-49 dBm
          Rx invalid nwid:0  Rx invalid crypt:0  Rx invalid frag:0
"""


def _iw_output(dbm):
    return (
        "Connected to aa:bb:cc:dd:ee:ff (on wlan0)\n"
        "\tSSID: MyNet\n"
        "\tfreq: 5180\n"
        f"\tsignal: {dbm} dBm\n"
        "\ttx bitrate: 433.3 MBit/s\n"
    ).encode("utf-8")


def test_iwconfig_present_parses_link_quality():
    with mock.patch.object(cc.subprocess, "check_output", return_value=IWCONFIG_OUTPUT):
        data = get_wifi_quality(interface="wlan0")

    assert data["result"] == "OK"
    assert data["data"]["wifi_quality_value"] == 61
    assert data["data"]["wifi_quality_max"] == 70
    assert data["data"]["wifi_quality_percentage"] == pytest.approx(87.14, abs=0.01)


def test_falls_back_to_iw_when_iwconfig_missing():
    def fake(cmd, **kwargs):
        if cmd[0] == "iwconfig":
            raise FileNotFoundError("iwconfig")
        return _iw_output(-70)

    with mock.patch.object(cc.subprocess, "check_output", side_effect=fake):
        data = get_wifi_quality(interface="wlan0")

    # 2 * (-70 + 100) = 60
    assert data["result"] == "OK"
    assert data["data"]["wifi_quality_value"] == 60
    assert data["data"]["wifi_quality_max"] == 100
    assert data["data"]["wifi_quality_percentage"] == pytest.approx(60.0)


@pytest.mark.parametrize(
    "dbm,expected",
    [
        (-40, 100),  # 2*60=120 clamps to 100
        (-30, 100),  # above ceiling clamps to 100
        (-95, 10),  # 2*5=10
        (-100, 0),  # floor
        (-105, 0),  # below floor clamps to 0
    ],
)
def test_iw_dbm_to_percentage_conversion_and_clamping(dbm, expected):
    def fake(cmd, **kwargs):
        if cmd[0] == "iwconfig":
            raise FileNotFoundError("iwconfig")
        return _iw_output(dbm)

    with mock.patch.object(cc.subprocess, "check_output", side_effect=fake):
        data = get_wifi_quality(interface="wlan0")

    assert data["result"] == "OK"
    assert data["data"]["wifi_quality_value"] == expected
    assert data["data"]["wifi_quality_max"] == 100


def test_error_when_both_tools_missing():
    with mock.patch.object(cc.subprocess, "check_output", side_effect=FileNotFoundError):
        data = get_wifi_quality(interface="wlan0")

    assert data["result"] == "ERROR"
    assert data["data"] == {}


def test_detect_wireless_interface_prefers_sysfs():
    def fake_isdir(path):
        return path == "/sys/class/net/wlp2s0/wireless"

    with (
        mock.patch.object(cc.os, "listdir", return_value=["eth0", "lo", "wlp2s0"]),
        mock.patch.object(cc.os.path, "isdir", side_effect=fake_isdir),
    ):
        assert cc._detect_wireless_interface() == "wlp2s0"


def test_detect_wireless_interface_falls_back_to_wlan0():
    with (
        mock.patch.object(cc.os, "listdir", return_value=["eth0", "lo"]),
        mock.patch.object(cc.os.path, "isdir", return_value=False),
    ):
        assert cc._detect_wireless_interface() == "wlan0"


def test_detect_wireless_interface_falls_back_to_wlan0_on_oserror():
    """/sys/class/net may not exist at all (e.g. sandboxed/odd environments);
    os.listdir raising OSError should be swallowed, not propagated."""
    with mock.patch.object(cc.os, "listdir", side_effect=OSError("no such directory")):
        assert cc._detect_wireless_interface() == "wlan0"


def test_get_wifi_quality_auto_detects_interface_when_not_supplied():
    with (
        mock.patch.object(cc, "_detect_wireless_interface", return_value="wlp3s0") as detect,
        mock.patch.object(cc.subprocess, "check_output", return_value=IWCONFIG_OUTPUT) as check_output,
    ):
        data = get_wifi_quality()

    detect.assert_called_once()
    assert data["result"] == "OK"
    # The auto-detected interface name is what's passed through to iwconfig.
    assert check_output.call_args.args[0] == ["iwconfig", "wlp3s0"]


def test_iwconfig_output_without_link_quality_falls_back_to_iw():
    """iwconfig succeeds (no FileNotFoundError) but its output has no
    'Link Quality=' field -- the parser returns None rather than raising, so
    the loop must fall through to the iw parser (400->389 continue branch)."""
    no_quality_output = b'wlan0     IEEE 802.11  ESSID:"MyNet"\n          Mode:Managed\n'

    def fake(cmd, **kwargs):
        if cmd[0] == "iwconfig":
            return no_quality_output
        return _iw_output(-60)

    with mock.patch.object(cc.subprocess, "check_output", side_effect=fake):
        data = get_wifi_quality(interface="wlan0")

    assert data["result"] == "OK"
    # 2 * (-60 + 100) = 80
    assert data["data"]["wifi_quality_value"] == 80


def test_iw_output_without_signal_line_yields_error_result():
    no_signal_output = b"Not connected.\n"

    def fake(cmd, **kwargs):
        if cmd[0] == "iwconfig":
            raise FileNotFoundError("iwconfig")
        return no_signal_output

    with mock.patch.object(cc.subprocess, "check_output", side_effect=fake):
        data = get_wifi_quality(interface="wlan0")

    assert data["result"] == "ERROR"
    assert data["data"] == {}


def test_called_process_error_from_iwconfig_falls_back_to_iw_and_logs():
    logger = mock.Mock()

    def fake(cmd, **kwargs):
        if cmd[0] == "iwconfig":
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
        return _iw_output(-50)

    with mock.patch.object(cc.subprocess, "check_output", side_effect=fake):
        data = get_wifi_quality(interface="wlan0", logger=logger)

    assert data["result"] == "OK"
    # logger.debug called at least once for the iwconfig failure and once for
    # the final "get_wifi_quality called" trace line.
    assert logger.debug.call_count >= 2
    assert any("failed to obtain wifi quality" in str(c) for c in logger.debug.call_args_list)
    assert any("get_wifi_quality called" in str(c) for c in logger.debug.call_args_list)


def test_called_process_error_without_logger_does_not_raise():
    """Same as the CalledProcessError case above but with no logger supplied
    -- the `if logger:` guard inside the except block must skip cleanly
    (397->399 branch) rather than erroring on a None.debug() call."""

    def fake(cmd, **kwargs):
        if cmd[0] == "iwconfig":
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
        return _iw_output(-50)

    with mock.patch.object(cc.subprocess, "check_output", side_effect=fake):
        data = get_wifi_quality(interface="wlan0")

    assert data["result"] == "OK"


def test_logger_debug_called_on_missing_tool_and_final_trace():
    logger = mock.Mock()

    def fake(cmd, **kwargs):
        if cmd[0] == "iwconfig":
            raise FileNotFoundError("iwconfig")
        return _iw_output(-70)

    with mock.patch.object(cc.subprocess, "check_output", side_effect=fake):
        get_wifi_quality(interface="wlan0", logger=logger)

    assert any("not found; trying next method" in str(c) for c in logger.debug.call_args_list)
