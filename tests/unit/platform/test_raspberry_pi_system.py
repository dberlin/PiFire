import logging
import sys
import types
from unittest import mock

# raspberry_pi_all imports `from rpi_hardware_pwm import HardwarePWM` at module
# load; that package is Pi-only and absent in the test venv. Stub it so the
# module imports on a generic host. (gpiozero IS installed.)
if "rpi_hardware_pwm" not in sys.modules:
    _stub = types.ModuleType("rpi_hardware_pwm")
    _stub.HardwarePWM = type("HardwarePWM", (), {"__init__": lambda self, *a, **k: None})
    sys.modules["rpi_hardware_pwm"] = _stub

import grillplat.raspberry_pi_all as rpi  # noqa: E402


def _bare():
    obj = object.__new__(rpi.GrillPlatform)
    obj.logger = logging.getLogger("test.rpi")
    return obj


def test_supported_commands_lists_all_nine():
    cmds = _bare().supported_commands([])["data"]["supported_cmds"]
    for name in (
        "check_throttled",
        "check_wifi_quality",
        "check_cpu_temp",
        "supported_commands",
        "check_alive",
        "scan_bluetooth",
        "os_info",
        "network_info",
        "hardware_info",
    ):
        assert name in cmds


def test_check_alive_ok():
    assert _bare().check_alive([]) == {
        "result": "OK",
        "message": "The control script is running.",
        "data": {},
    }


def test_os_info_ok_shape():
    data = _bare().os_info([])
    assert data["result"] == "OK"
    assert data["message"] == "OS information retrieved successfully."


def test_network_info_ok_shape():
    data = _bare().network_info([])
    assert data["result"] == "OK"
    assert data["message"] == "Network information retrieved successfully."


def test_check_wifi_quality_delegates_to_common(monkeypatch):
    # Pi version is identical to the mixin: `return get_wifi_quality(logger=...)`.
    sentinel = {"result": "OK", "message": "x", "data": {"wifi_quality_value": 1}}
    monkeypatch.setattr(rpi, "get_wifi_quality", lambda logger=None: sentinel)
    assert _bare().check_wifi_quality([]) is sentinel


def test_scan_bluetooth_no_devices(monkeypatch):
    async def _no_devices(*a, **k):
        return []

    fake_scanner = mock.Mock()
    fake_scanner.discover = _no_devices
    monkeypatch.setitem(sys.modules, "bleak", mock.Mock(BleakScanner=fake_scanner))
    data = _bare().scan_bluetooth([])
    assert data["result"] == "OK"
    assert data["data"]["bt_devices"] == []


# --- KEPT Pi-specific overrides: MUST stay vcgencmd/proc-based (NOT the mixin) ---


def test_check_throttled_parses_vcgencmd(monkeypatch):
    # Under-voltage bit (0x10000) set -> WARNING. Proves the vcgencmd override,
    # not the mixin's hardcoded-False stub, is in effect. subprocess mocked:
    # no real `sudo vcgencmd` runs.
    monkeypatch.setattr(rpi.subprocess, "check_output", lambda *a, **k: b"throttled=0x10000")
    data = _bare().check_throttled([])
    assert data["result"] == "OK"
    assert data["data"]["cpu_under_voltage"] is True
    assert data["data"]["cpu_throttled"] is False


def test_check_throttled_clean(monkeypatch):
    monkeypatch.setattr(rpi.subprocess, "check_output", lambda *a, **k: b"throttled=0x0")
    data = _bare().check_throttled([])
    assert data["data"] == {"cpu_under_voltage": False, "cpu_throttled": False}


def test_check_cpu_temp_parses_vcgencmd(monkeypatch):
    # Proves the vcgencmd override (not the mixin's psutil variant) is in effect.
    monkeypatch.setattr(rpi.subprocess, "check_output", lambda *a, **k: b"temp=42.0'C\n")
    data = _bare().check_cpu_temp([])
    assert data["result"] == "OK"
    assert data["data"]["cpu_temp"] == 42.0


def test_hardware_info_populates_model_name():
    info = _bare().hardware_info([])
    assert info["result"] == "OK"
    assert "model" in info["data"]["cpu_info"]  # 3-field variant, not the mixin
