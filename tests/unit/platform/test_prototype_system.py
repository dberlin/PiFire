import logging
from unittest import mock

import grillplat.prototype as proto


def _bare():
    # System methods only need self.logger; skip __init__ (no GPIO on host).
    obj = object.__new__(proto.GrillPlatform)
    obj.logger = logging.getLogger("test.prototype")
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


def test_check_throttled_stub_all_false():
    data = _bare().check_throttled([])
    assert data["result"] == "OK"
    assert data["message"] == "No under-voltage or throttling detected."
    assert data["data"] == {"cpu_under_voltage": False, "cpu_throttled": False}


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
    assert isinstance(data["data"], dict)


def test_network_info_ok_shape():
    data = _bare().network_info([])
    assert data["result"] == "OK"
    assert data["message"] == "Network information retrieved successfully."
    assert isinstance(data["data"], dict)


def test_scan_bluetooth_no_devices(monkeypatch):
    async def _no_devices(*a, **k):
        return []

    fake_scanner = mock.Mock()
    fake_scanner.discover = _no_devices
    monkeypatch.setitem(__import__("sys").modules, "bleak", mock.Mock(BleakScanner=fake_scanner))
    data = _bare().scan_bluetooth([])
    assert data["result"] == "OK"
    assert data["data"]["bt_devices"] == []


# --- KEPT simulator overrides: these MUST stay prototype-specific ---


def test_check_wifi_quality_is_fake_constant():
    # Prototype override returns fixed simulator values (NOT get_wifi_quality()).
    assert _bare().check_wifi_quality([])["data"] == {
        "wifi_quality_value": 60,
        "wifi_quality_max": 70,
        "wifi_quality_percentage": 80,
    }


def test_check_cpu_temp_is_fixed_40():
    # Prototype override returns a constant, NOT a live psutil reading.
    assert _bare().check_cpu_temp([])["data"]["cpu_temp"] == 40.0


def test_hardware_info_populates_model_name():
    # Prototype override reads /proc/cpuinfo (3 fields); the mixin's variant
    # would leave model/hardware as "Unknown". Pin that it is NOT the mixin.
    info = _bare().hardware_info([])
    assert info["result"] == "OK"
    assert "cpu_info" in info["data"]
    assert "model" in info["data"]["cpu_info"]  # key present in the 3-field variant
