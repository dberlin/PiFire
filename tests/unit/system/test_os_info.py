"""Tests for common/system.py's OS-info helpers: get_os_info (raw /etc/os-release
+ uname probe) and get_display_os_info (cached read with fallback/defaults/BITS
derivation), used by the admin page and mobile app system-info panel.
"""

from unittest import mock

import common.system as cc

FAKE_OS_RELEASE = (
    'NAME="Fedora Linux"\n'
    "\n"  # blank line: no '=' -> loop-continue branch
    "# a comment line without an equals sign\n"
    'VERSION="39 (Workstation Edition)"\n'
    'PRETTY_NAME="Fedora Linux 39 (Workstation Edition)"\n'
    "VERSION_ID=39\n"
)


def test_get_os_info_parses_os_release_and_appends_architecture():
    m = mock.mock_open(read_data=FAKE_OS_RELEASE)
    with (
        mock.patch("builtins.open", m),
        mock.patch.object(cc.subprocess, "check_output", return_value=b"x86_64\n") as check_output,
        mock.patch.object(cc, "store_os_info") as store,
    ):
        os_info = cc.get_os_info()

    # Quotes stripped, comment/blank lines skipped (152->151 continue branch)
    # -- exactly the 4 '='-bearing os-release lines plus ARCHITECTURE, no
    # stray key for the blank/comment lines.
    assert set(os_info.keys()) == {"NAME", "VERSION", "PRETTY_NAME", "VERSION_ID", "ARCHITECTURE"}
    assert os_info["NAME"] == "Fedora Linux"
    assert os_info["VERSION_ID"] == "39"
    assert os_info["ARCHITECTURE"] == "x86_64"

    check_output.assert_called_once_with(["/bin/uname", "-m"])
    store.assert_called_once_with(os_info)  # cached in the datastore, not a CWD file


def test_get_display_os_info_uses_cached_json_and_computes_64bit():
    cached = {
        "PRETTY_NAME": "Fedora Linux 39",
        "NAME": "Fedora Linux",
        "VERSION_ID": "39",
        "VERSION": "39 (Workstation)",
        "VERSION_CODENAME": "",
        "ARCHITECTURE": "x86_64",
    }
    with (
        mock.patch.object(cc, "load_os_info", return_value=cached) as load_cache,
        mock.patch.object(cc, "get_os_info") as get_os_info,
    ):
        info = cc.get_display_os_info()

    load_cache.assert_called_once_with()
    get_os_info.assert_not_called()  # cache hit -- no live fallback read
    assert info["BITS"] == "64-Bit"
    assert info["PRETTY_NAME"] == "Fedora Linux 39"


def test_get_display_os_info_32bit_architecture():
    cached = {"ARCHITECTURE": "armv7l"}
    with mock.patch.object(cc, "load_os_info", return_value=cached):
        info = cc.get_display_os_info()
    assert info["BITS"] == "32-Bit"


def test_get_display_os_info_unrecognized_architecture_is_unknown_bits():
    cached = {"ARCHITECTURE": "riscv64"}
    with mock.patch.object(cc, "load_os_info", return_value=cached):
        info = cc.get_display_os_info()
    assert info["BITS"] == "Unknown"


def test_get_display_os_info_falls_back_to_live_read_when_cache_empty():
    live = {"ARCHITECTURE": "aarch64", "PRETTY_NAME": "Live Read OS"}
    with (
        mock.patch.object(cc, "load_os_info", return_value={}),
        mock.patch.object(cc, "get_os_info", return_value=live) as get_os_info,
    ):
        info = cc.get_display_os_info()

    get_os_info.assert_called_once()
    assert info["PRETTY_NAME"] == "Live Read OS"
    assert info["BITS"] == "64-Bit"
    # Missing fields backfilled with the "Unknown" default (no trailing period).
    assert info["NAME"] == "Unknown"
    assert info["VERSION_CODENAME"] == "Unknown"


def test_get_display_os_info_logs_and_defaults_when_cache_read_raises():
    with (
        mock.patch.object(cc, "load_os_info", side_effect=RuntimeError("disk error")),
        mock.patch.object(cc, "write_log") as write_log,
    ):
        info = cc.get_display_os_info()

    write_log.assert_called_once()
    logged_msg = write_log.call_args.args[0]
    assert "Error reading OS info" in logged_msg
    assert "disk error" in logged_msg

    # Every field falls back to the "Unknown" default and BITS is derived accordingly.
    assert info["PRETTY_NAME"] == "Unknown"
    assert info["BITS"] == "Unknown"
