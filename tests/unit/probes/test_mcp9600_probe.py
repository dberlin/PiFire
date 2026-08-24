import json
import os
import sys
import types
import importlib

import pytest

from probes.thermocouple_health import ThermocoupleHealthState
from probes.thermocouple_inference import ThermocoupleJunctionSample


def _install_fakes(monkeypatch):
    """Install fake hardware modules so the probe imports without hardware."""
    # adafruit_mcp9600 with an MCP9600 that captures its constructor args
    mcp_mod = types.ModuleType("adafruit_mcp9600")

    class FakeMCP9600:
        def __init__(self, i2c, address=0x67, tctype="K"):
            self.i2c = i2c
            self.address = address
            self.tctype = tctype
            self.temp_c = 100.0
            self.ambient_c = 25.0
            self.accesses = []

        @property
        def temperature(self):
            self.accesses.append("temperature")
            if isinstance(self.temp_c, BaseException):
                raise self.temp_c
            return self.temp_c

        @property
        def ambient_temperature(self):
            self.accesses.append("ambient_temperature")
            if isinstance(self.ambient_c, BaseException):
                raise self.ambient_c
            return self.ambient_c

        @property
        def status(self):
            raise AssertionError("MCP9600 status register must not be read")

    mcp_mod.MCP9600 = FakeMCP9600
    monkeypatch.setitem(sys.modules, "adafruit_mcp9600", mcp_mod)

    # board / busio
    board_mod = types.ModuleType("board")
    board_mod.SCL = "SCL"
    board_mod.SDA = "SDA"
    monkeypatch.setitem(sys.modules, "board", board_mod)

    busio_mod = types.ModuleType("busio")
    busio_mod.I2C = lambda scl, sda: ("I2C", scl, sda)
    monkeypatch.setitem(sys.modules, "busio", busio_mod)

    # adafruit_extended_bus.ExtendedI2C
    ext_mod = types.ModuleType("adafruit_extended_bus")
    ext_mod.ExtendedI2C = lambda bus: ("ExtI2C", bus)
    monkeypatch.setitem(sys.modules, "adafruit_extended_bus", ext_mod)

    # adafruit_bus_device.i2c_device.I2CDevice
    busdev_pkg = types.ModuleType("adafruit_bus_device")
    i2cdev_mod = types.ModuleType("adafruit_bus_device.i2c_device")
    i2cdev_mod.I2CDevice = object
    busdev_pkg.i2c_device = i2cdev_mod
    monkeypatch.setitem(sys.modules, "adafruit_bus_device", busdev_pkg)
    monkeypatch.setitem(sys.modules, "adafruit_bus_device.i2c_device", i2cdev_mod)

    return mcp_mod


def _load_probe(monkeypatch):
    _install_fakes(monkeypatch)
    import probes._mcp960x_adafruit as shared
    import probes.mcp9600_adafruit as probe

    importlib.reload(shared)
    importlib.reload(probe)
    return probe


def _make_mcp_probe(monkeypatch, units):
    probe = _load_probe(monkeypatch)
    probe_info = [
        {
            "device": "mcp9600",
            "port": "KTT0",
            "label": "Grill",
            "type": "Primary",
            "profile": {"A": 0.00073431401, "B": 0.0002157437, "C": 9.515686e-8},
        }
    ]
    device_info = {
        "device": "mcp9600",
        "module": "mcp9600_adafruit",
        "ports": ["KTT0"],
        "config": {},
    }
    return probe.ReadProbes(probe_info, device_info, units)


@pytest.fixture
def mcp_probe(monkeypatch):
    return _make_mcp_probe(monkeypatch, "F")


@pytest.fixture
def mcp_probe_celsius(monkeypatch):
    return _make_mcp_probe(monkeypatch, "C")


def test_mcp9600_remains_unmonitored_and_does_not_read_status(mcp_probe):
    output = mcp_probe.read_all_ports({})
    report = mcp_probe.get_thermocouple_health()["Grill"]

    assert output["primary"]["Grill"] == 212.0
    assert report.state is ThermocoupleHealthState.UNMONITORED
    assert report.temperature_valid is True


def test_mcp9600_read_keeps_existing_units_and_port_contract(mcp_probe_celsius):
    output = mcp_probe_celsius.read_all_ports({})

    assert mcp_probe_celsius.device_info["ports"] == ["KTT0"]
    assert output["primary"]["Grill"] == 100.0
    assert output["tr"]["Grill"] == 0


@pytest.mark.parametrize(
    ("units", "expected_output"),
    [pytest.param("F", 212.0, id="fahrenheit"), pytest.param("C", 100.0, id="celsius")],
)
def test_mcp9600_read_captures_one_raw_celsius_junction_pair(
    monkeypatch,
    units,
    expected_output,
):
    obj = _make_mcp_probe(monkeypatch, units)
    obj.device.sensor.temp_c = 100.04
    obj.device.sensor.ambient_c = 24.96

    output = obj.read_all_ports({})

    assert obj.device.sensor.accesses == ["temperature", "ambient_temperature"]
    assert output["primary"]["Grill"] == expected_output
    assert obj.get_thermocouple_samples() == {"KTT0": ThermocoupleJunctionSample(hot_c=100.04, cold_c=24.96)}


@pytest.mark.parametrize(
    ("failed_attribute", "expected_accesses"),
    [
        pytest.param("temp_c", ["temperature"], id="hot"),
        pytest.param(
            "ambient_c",
            ["temperature", "ambient_temperature"],
            id="cold",
        ),
    ],
)
def test_mcp9600_junction_exception_clears_previous_sample_and_reraises(
    monkeypatch,
    failed_attribute,
    expected_accesses,
):
    obj = _make_mcp_probe(monkeypatch, "F")
    obj.read_all_ports({})
    error = OSError(f"{failed_attribute} read failed")
    setattr(obj.device.sensor, failed_attribute, error)
    obj.device.sensor.accesses.clear()

    with pytest.raises(OSError) as caught:
        obj.read_all_ports({})

    assert caught.value is error
    assert obj.device.sensor.accesses == expected_accesses
    assert obj.get_thermocouple_samples() == {}
    assert obj.get_thermocouple_health()["Grill"].state is ThermocoupleHealthState.UNMONITORED


def test_init_device_wires_tc_type(monkeypatch):
    probe = _load_probe(monkeypatch)

    obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
    obj.device_info = {"config": {"i2c_bus_addr": "0x66", "tc_type": "J"}}
    obj._init_device()

    assert obj.device_info["ports"] == ["KTT0"]
    sensor = obj.device.sensor
    assert sensor.tctype == "J"  # configured type passed through
    assert sensor.address == 0x66  # parsed from hex string


def test_init_device_defaults(monkeypatch):
    probe = _load_probe(monkeypatch)

    obj = probe.ReadProbes.__new__(probe.ReadProbes)
    obj.device_info = {"config": {}}  # no keys -> all defaults
    obj._init_device()

    sensor = obj.device.sensor
    assert sensor.tctype == "K"  # default K
    assert sensor.address == 0x67  # default address


def test_kttdevice_exposes_ambient_temperature(monkeypatch):
    probe = _load_probe(monkeypatch)

    dev = probe.KTTDevice()

    assert dev.ambient_temperature == 25.0


def test_manifest_mcp9600_entry():
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    manifest = json.load(open(os.path.join(repo_root, "wizard", "wizard_manifest.json")))
    probes = manifest["modules"]["probes"]
    assert "mcp9600_adafruit" in probes
    entry = probes["mcp9600_adafruit"]

    ds = entry["device_specific"]
    assert ds["type"] == "thermocouple"
    assert ds["ports"] == ["KTT0"]

    labels = [item["label"] for item in ds["config"]]
    assert "tc_type" in labels

    tc = next(i for i in ds["config"] if i["label"] == "tc_type")
    assert tc["list_values"] == ["B", "E", "J", "K", "N", "R", "S", "T"]
    assert tc["default"] == "K"


def test_kttdevice_opens_bus_via_factory(monkeypatch):
    from unittest import mock
    from common.i2c_bus_config import FT232HBus

    probe = _load_probe(monkeypatch)
    shared = sys.modules["probes._mcp960x_adafruit"]

    fake_bus = object()
    opened = {}

    def fake_open(bus):
        opened["bus"] = bus
        return fake_bus

    monkeypatch.setattr(shared, "open_i2c_bus", fake_open)
    sensor_factory = mock.Mock()
    monkeypatch.setattr(shared.MCP960xDevice, "sensor_class", sensor_factory)

    dev = probe.KTTDevice(i2c_bus_addr=0x67, bus=FT232HBus(url="1"), tc_type="K")
    assert dev.i2c is fake_bus
    assert opened["bus"] == FT232HBus(url="1")
    sensor_factory.assert_called_once()
