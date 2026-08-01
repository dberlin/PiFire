import json
import os


def _manifest():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "wizard", "wizard_manifest.json")
    with open(path) as handle:
        return json.load(handle)


def test_vl53l0x_entry_uses_adafruit_circuitpython():
    manifest = _manifest()
    entry = manifest["modules"]["distance"]["vl53l0x"]
    assert entry["py_dependencies"] == ["adafruit-circuitpython-vl53l0x"]
    assert entry["apt_dependencies"] == []


def test_vl53l4cd_entry_present():
    manifest = _manifest()
    entry = manifest["modules"]["distance"]["vl53l4cd"]
    assert entry["filename"] == "vl53l4cd"
    assert entry["py_dependencies"] == ["adafruit-circuitpython-vl53l4cd"]
    assert entry["apt_dependencies"] == []
    assert entry["image"] == "vl53l4cd.png"


def test_vl53l1x_entry_present():
    manifest = _manifest()
    entry = manifest["modules"]["distance"]["vl53l1x"]
    assert entry["filename"] == "vl53l1x"
    assert entry["py_dependencies"] == ["adafruit-circuitpython-vl53l1x"]
    assert entry["apt_dependencies"] == []
    assert entry["image"] == "vl53l1x.png"


def test_sen0628_entry_present():
    manifest = _manifest()
    entry = manifest["modules"]["distance"]["sen0628"]
    assert entry["filename"] == "sen0628"
    assert entry["py_dependencies"] == []
    assert entry["apt_dependencies"] == []
    assert entry["image"] == "sen0628.png"
    device_field = entry["settings_dependencies"]["sen0628_device"]
    assert device_field["type"] == "usb_serial_device"
    assert device_field["settings"] == ["platform", "devices", "distance", "device"]
    assert device_field["vid"] is None
    assert device_field["pid"] is None


def test_all_platforms_have_distance_i2c_fields():
    manifest = _manifest()
    platforms = manifest["modules"]["grillplatform"]
    for name, entry in platforms.items():
        deps = entry.get("settings_dependencies", {})

        assert "device_distance_i2c_bus" in deps, name
        assert deps["device_distance_i2c_bus"]["type"] == "i2c_bus"
        assert deps["device_distance_i2c_bus"]["default"] == {"kind": "basic"}
        assert deps["device_distance_i2c_bus"]["settings"] == ["platform", "devices", "distance", "i2c_bus"]

        assert "device_distance_address" in deps, name
        assert deps["device_distance_address"]["settings"] == ["platform", "devices", "distance", "address"]
        assert "0x29" in deps["device_distance_address"]["options"]
