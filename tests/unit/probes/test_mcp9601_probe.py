import importlib
import json
import logging
import sys
import types
from pathlib import Path

import pytest
from probes.thermocouple_health import (
    ThermocoupleFault,
    ThermocoupleHealthState,
)
from probes.thermocouple_inference import ThermocoupleJunctionSample


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_NOTE = (
    "Hardware fault detection is disabled by default. A disconnected or electrically "
    "shorted/collapsed thermocouple can read as ambient temperature instead of "
    "reporting a fault. Enable hardware detection only when the board includes the "
    "required MCP9601 VSENSE network; SEN-30010-W is verified."
)


def _install_fakes(monkeypatch):
    """Install hardware fakes before importing the MCP9601 probe module."""
    mcp_mod = types.ModuleType("adafruit_mcp9600")

    class FakeMCP9600:
        def __init__(self, i2c, address=0x67, tctype="K"):
            self.i2c = i2c
            self.address = address
            self.tctype = tctype
            self.status_value = 0
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

    setattr(mcp_mod, "MCP9600", FakeMCP9600)
    monkeypatch.setitem(sys.modules, "adafruit_mcp9600", mcp_mod)

    register_pkg = types.ModuleType("adafruit_register")
    struct_mod = types.ModuleType("adafruit_register.i2c_struct")
    descriptor_specs = []

    class FakeROUnaryStruct:
        def __init__(self, register_address, struct_format):
            descriptor_specs.append((register_address, struct_format))

        def __get__(self, instance, _):
            if instance is None:
                return self
            instance.accesses.append("status")
            if isinstance(instance.status_value, BaseException):
                raise instance.status_value
            return instance.status_value

    setattr(struct_mod, "ROUnaryStruct", FakeROUnaryStruct)
    setattr(register_pkg, "i2c_struct", struct_mod)
    monkeypatch.setitem(sys.modules, "adafruit_register", register_pkg)
    monkeypatch.setitem(sys.modules, "adafruit_register.i2c_struct", struct_mod)

    board_mod = types.ModuleType("board")
    setattr(board_mod, "SCL", "SCL")
    setattr(board_mod, "SDA", "SDA")
    monkeypatch.setitem(sys.modules, "board", board_mod)

    busio_mod = types.ModuleType("busio")
    setattr(busio_mod, "I2C", lambda scl, sda: ("I2C", scl, sda))
    monkeypatch.setitem(sys.modules, "busio", busio_mod)

    ext_mod = types.ModuleType("adafruit_extended_bus")
    setattr(ext_mod, "ExtendedI2C", lambda bus: ("ExtI2C", bus))
    monkeypatch.setitem(sys.modules, "adafruit_extended_bus", ext_mod)

    busdev_pkg = types.ModuleType("adafruit_bus_device")
    i2cdev_mod = types.ModuleType("adafruit_bus_device.i2c_device")
    setattr(i2cdev_mod, "I2CDevice", object)
    setattr(busdev_pkg, "i2c_device", i2cdev_mod)
    monkeypatch.setitem(sys.modules, "adafruit_bus_device", busdev_pkg)
    monkeypatch.setitem(sys.modules, "adafruit_bus_device.i2c_device", i2cdev_mod)

    return FakeMCP9600, descriptor_specs


@pytest.fixture
def probe(monkeypatch):
    fake_sensor_class, descriptor_specs = _install_fakes(monkeypatch)
    import probes._mcp960x_adafruit as shared

    importlib.reload(shared)
    sys.modules.pop("probes.mcp9601_adafruit", None)
    module = importlib.import_module("probes.mcp9601_adafruit")
    return module, fake_sensor_class, descriptor_specs


def _new_read_probes(probe, config):
    module, _, _ = probe
    obj = module.ReadProbes.__new__(module.ReadProbes)
    obj.device_info = {"config": config}
    return obj


def _configured_probe(
    probe,
    *,
    primary,
    detection,
    status,
    temp_c,
    ambient_c=25.0,
):
    module, _, _ = probe
    probe_info = [
        {
            "device": "mcp9601",
            "port": "KTT0",
            "label": "Grill",
            "type": "Primary" if primary else "Food",
            "profile": {"A": 0.00073431401, "B": 0.0002157437, "C": 9.515686e-8},
        }
    ]
    device_info = {
        "device": "mcp9601",
        "module": "mcp9601_adafruit",
        "ports": ["KTT0"],
        "config": {"hardware_fault_detection": detection},
    }
    obj = module.ReadProbes(probe_info, device_info, "F")
    obj.device.sensor.status_value = status
    obj.device.sensor.temp_c = temp_c
    obj.device.sensor.ambient_c = ambient_c
    return obj


def _probe_manifest():
    with (REPO_ROOT / "wizard" / "wizard_manifest.json").open() as manifest_file:
        return json.load(manifest_file)["modules"]["probes"]


def test_module_exposes_thin_mcp9601_classes_and_one_status_descriptor(probe):
    module, fake_sensor_class, descriptor_specs = probe

    assert issubclass(module.MCP9601Sensor, fake_sensor_class)
    assert module.KTTDevice.sensor_class is module.MCP9601Sensor
    assert module.ReadProbes.device_class is module.KTTDevice
    assert descriptor_specs == [(0x04, ">B")]


def test_mcp9601_defaults_to_sen_30010_address_and_detection_off(probe):
    obj = _new_read_probes(probe, config={})

    obj._init_device()

    assert obj.device.sensor.address == 0x61
    assert obj.device.sensor.tctype == "K"
    assert obj.hardware_fault_detection is False


def test_manifest_exposes_opt_in_hardware_detection():
    from PIL import Image

    probes = _probe_manifest()
    entry = probes["mcp9601_adafruit"]
    config = {item["label"]: item for item in entry["device_specific"]["config"]}

    assert entry["friendly_name"] == "MCP9601 Thermocouple Amplifier (SEN-30010-W)"
    assert entry["filename"] == "mcp9601_adafruit"
    assert entry["image"] == "mcp9601.png"
    assert entry["py_dependencies"] == probes["mcp9600_adafruit"]["py_dependencies"]
    assert entry["device_specific"]["ports"] == ["KTT0"]
    assert entry["device_specific"]["type"] == "thermocouple"
    assert list(config) == [
        "i2c_bus_addr",
        "tc_type",
        "hardware_fault_detection",
        "i2c_bus",
        "transient",
    ]
    assert config["i2c_bus_addr"]["list_values"] == [
        "0x67",
        "0x66",
        "0x65",
        "0x64",
        "0x63",
        "0x62",
        "0x61",
        "0x60",
    ]
    assert config["i2c_bus_addr"]["default"] == "0x61"
    assert config["tc_type"]["list_values"] == ["B", "E", "J", "K", "N", "R", "S", "T"]
    assert config["tc_type"]["default"] == "K"
    assert config["hardware_fault_detection"] == {
        "label": "hardware_fault_detection",
        "friendly_name": "Hardware Thermocouple Fault Detection",
        "description": (
            "Enable only when the installed MCP9601 board includes the required VSENSE "
            "open/short detection network. SEN-30010-W is verified."
        ),
        "type": "list",
        "list_values": ["False", "True"],
        "list_labels": ["Disabled", "Enabled — board has VSENSE detection"],
        "default": "False",
        "hidden": False,
    }
    assert config["i2c_bus"]["default"] == {"kind": "basic"}
    assert config["transient"]["default"] == "False"
    assert entry["notes"] == EXPECTED_NOTE

    asset = REPO_ROOT / "static" / "img" / "wizard" / entry["image"]
    assert asset.is_file()
    with Image.open(asset) as image:
        assert image.size == (128, 128)
        assert image.mode == "RGBA"


def test_detection_off_never_reads_status(probe):
    obj = _configured_probe(
        probe,
        primary=True,
        detection="False",
        status=0x30,
        temp_c=100.04,
        ambient_c=24.96,
    )

    output = obj.read_all_ports({})
    report = obj.get_thermocouple_health()["Grill"]

    assert obj.device.sensor.accesses == ["temperature", "ambient_temperature"]
    assert output["primary"]["Grill"] == 212.0
    assert obj.get_thermocouple_samples() == {"KTT0": ThermocoupleJunctionSample(hot_c=100.04, cold_c=24.96)}
    assert report.state is ThermocoupleHealthState.UNMONITORED


def test_enabled_clean_status_is_read_once_before_temperature(probe):
    obj = _configured_probe(
        probe,
        primary=True,
        detection="True",
        status=0x00,
        temp_c=100.04,
        ambient_c=24.96,
    )

    output = obj.read_all_ports({})
    report = obj.get_thermocouple_health()["Grill"]

    assert obj.device.sensor.accesses == [
        "status",
        "temperature",
        "ambient_temperature",
    ]
    assert output["primary"]["Grill"] == 212.0
    assert obj.get_thermocouple_samples() == {"KTT0": ThermocoupleJunctionSample(hot_c=100.04, cold_c=24.96)}
    assert report.state is ThermocoupleHealthState.HEALTHY
    assert report.faults == ()


def test_enabled_open_fault_is_read_before_temperature_and_invalidates_output(probe):
    obj = _configured_probe(
        probe,
        primary=True,
        detection="True",
        status=0x10,
        temp_c=250.0,
    )

    output = obj.read_all_ports({})
    report = obj.get_thermocouple_health()["Grill"]

    assert obj.device.sensor.accesses == ["status"]
    assert obj.get_thermocouple_samples() == {}
    assert output["primary"]["Grill"] is None
    assert report.faults == (ThermocoupleFault.OPEN,)
    assert report.confirmed
    assert report.detail["status"] == 0x10


def test_direct_hardware_fault_clears_previous_sample_without_junction_reads(probe):
    obj = _configured_probe(
        probe,
        primary=True,
        detection="True",
        status=0x00,
        temp_c=100.0,
    )
    obj.read_all_ports({})
    obj.device.sensor.status_value = 0x10
    obj.device.sensor.accesses.clear()

    output = obj.read_all_ports({})

    assert obj.device.sensor.accesses == ["status"]
    assert obj.get_thermocouple_samples() == {}
    assert output["primary"]["Grill"] is None


@pytest.mark.parametrize(
    ("status", "expected_faults"),
    [
        pytest.param(0x20, (ThermocoupleFault.SHORT,), id="short"),
        pytest.param(
            0x30,
            (ThermocoupleFault.OPEN, ThermocoupleFault.SHORT),
            id="open-and-short",
        ),
    ],
)
def test_enabled_status_decodes_all_direct_hardware_fault_bits(
    probe,
    status,
    expected_faults,
):
    obj = _configured_probe(
        probe,
        primary=True,
        detection="True",
        status=status,
        temp_c=250.0,
    )

    output = obj.read_all_ports({})
    report = obj.get_thermocouple_health()["Grill"]

    assert obj.device.sensor.accesses == ["status"]
    assert output["primary"]["Grill"] is None
    assert report.faults == expected_faults
    assert report.detail["status"] == status


def test_primary_fault_remains_latched_after_clean_hardware_status(probe, monkeypatch):
    obj = _configured_probe(
        probe,
        primary=True,
        detection="True",
        status=0x10,
        temp_c=250.0,
    )
    shared = sys.modules["probes._mcp960x_adafruit"]
    clock = {"now": 10.0}
    monkeypatch.setattr(shared.time, "monotonic", lambda: clock["now"])
    assert obj.read_all_ports({})["primary"]["Grill"] is None

    obj.device.sensor.status_value = 0x00
    obj.device.sensor.accesses.clear()
    clock["now"] = 10_000.0
    output = obj.read_all_ports({})
    report = obj.get_thermocouple_health()["Grill"]

    assert obj.device.sensor.accesses == ["status"]
    assert output["primary"]["Grill"] is None
    assert report.state is ThermocoupleHealthState.CONFIRMED
    assert report.faults == (ThermocoupleFault.OPEN,)


def test_secondary_fault_recovers_after_sixty_consecutive_clean_seconds(
    probe,
    monkeypatch,
):
    obj = _configured_probe(
        probe,
        primary=False,
        detection="True",
        status=0x10,
        temp_c=100.0,
    )
    shared = sys.modules["probes._mcp960x_adafruit"]
    clock = {"now": 0.0}
    monkeypatch.setattr(shared.time, "monotonic", lambda: clock["now"])
    assert obj.read_all_ports({})["food"]["Grill"] is None

    obj.device.sensor.status_value = 0x00
    for now in (10.0, 69.9):
        clock["now"] = now
        obj.device.sensor.accesses.clear()
        assert obj.read_all_ports({})["food"]["Grill"] is None
        assert obj.device.sensor.accesses == [
            "status",
            "temperature",
            "ambient_temperature",
        ]
    clock["now"] = 70.0
    obj.device.sensor.accesses.clear()
    output = obj.read_all_ports({})
    report = obj.get_thermocouple_health()["Grill"]

    assert obj.device.sensor.accesses == [
        "status",
        "temperature",
        "ambient_temperature",
    ]
    assert output["food"]["Grill"] == 212.0
    assert report.state is ThermocoupleHealthState.HEALTHY
    assert report.faults == ()


@pytest.mark.parametrize(
    ("failed_attribute", "expected_accesses"),
    [
        pytest.param(
            "temp_c",
            ["status", "temperature"],
            id="hot",
        ),
        pytest.param(
            "ambient_c",
            ["status", "temperature", "ambient_temperature"],
            id="cold",
        ),
    ],
)
def test_secondary_junction_read_exception_restarts_full_clean_recovery_window(
    probe,
    monkeypatch,
    failed_attribute,
    expected_accesses,
):
    obj = _configured_probe(
        probe,
        primary=False,
        detection="True",
        status=0x10,
        temp_c=100.0,
    )
    shared = sys.modules["probes._mcp960x_adafruit"]
    clock = {"now": 0.0}
    monkeypatch.setattr(shared.time, "monotonic", lambda: clock["now"])
    assert obj.read_all_ports({})["food"]["Grill"] is None

    obj.device.sensor.status_value = 0x00
    clock["now"] = 10.0
    assert obj.read_all_ports({})["food"]["Grill"] is None
    assert obj.get_thermocouple_samples() == {"KTT0": ThermocoupleJunctionSample(hot_c=100.0, cold_c=25.0)}

    read_error = OSError(f"{failed_attribute} read failed")
    setattr(obj.device.sensor, failed_attribute, read_error)
    obj.device.sensor.accesses.clear()
    clock["now"] = 70.0
    with pytest.raises(OSError) as caught:
        obj.read_all_ports({})

    report = obj.get_thermocouple_health()["Grill"]
    assert caught.value is read_error
    assert obj.device.sensor.accesses == expected_accesses
    assert obj.get_thermocouple_samples() == {}
    assert obj.output_data["food"]["Grill"] is None
    assert report.state is ThermocoupleHealthState.CONFIRMED
    assert report.faults == (ThermocoupleFault.OPEN,)

    recovered_value = 100.0 if failed_attribute == "temp_c" else 25.0
    setattr(obj.device.sensor, failed_attribute, recovered_value)
    for now in (71.0, 130.9):
        clock["now"] = now
        obj.device.sensor.accesses.clear()
        assert obj.read_all_ports({})["food"]["Grill"] is None
        assert obj.device.sensor.accesses == [
            "status",
            "temperature",
            "ambient_temperature",
        ]

    clock["now"] = 131.0
    obj.device.sensor.accesses.clear()
    output = obj.read_all_ports({})
    report = obj.get_thermocouple_health()["Grill"]

    assert obj.device.sensor.accesses == [
        "status",
        "temperature",
        "ambient_temperature",
    ]
    assert output["food"]["Grill"] == 212.0
    assert report.state is ThermocoupleHealthState.HEALTHY
    assert report.faults == ()


def test_status_exception_propagates_without_becoming_a_hardware_fault(probe):
    status_error = OSError("status read failed")
    obj = _configured_probe(
        probe,
        primary=True,
        detection="True",
        status=status_error,
        temp_c=250.0,
    )

    with pytest.raises(OSError) as caught:
        obj.read_all_ports({})

    report = obj.get_thermocouple_health()["Grill"]
    assert caught.value is status_error
    assert obj.device.sensor.accesses == ["status"]
    assert report.state is ThermocoupleHealthState.UNMONITORED
    assert report.faults == ()


def test_status_exception_cancels_secondary_clean_recovery_window(
    probe,
    monkeypatch,
):
    obj = _configured_probe(
        probe,
        primary=False,
        detection="True",
        status=0x10,
        temp_c=100.0,
    )
    shared = sys.modules["probes._mcp960x_adafruit"]
    clock = {"now": 0.0}
    monkeypatch.setattr(shared.time, "monotonic", lambda: clock["now"])
    assert obj.read_all_ports({})["food"]["Grill"] is None

    obj.device.sensor.status_value = 0x00
    clock["now"] = 10.0
    assert obj.read_all_ports({})["food"]["Grill"] is None

    status_error = OSError("status read failed")
    obj.device.sensor.status_value = status_error
    clock["now"] = 20.0
    with pytest.raises(OSError) as caught:
        obj.read_all_ports({})
    assert caught.value is status_error

    obj.device.sensor.status_value = 0x00
    for now in (70.0, 129.9):
        clock["now"] = now
        obj.device.sensor.accesses.clear()
        assert obj.read_all_ports({})["food"]["Grill"] is None
        assert obj.device.sensor.accesses == [
            "status",
            "temperature",
            "ambient_temperature",
        ]
    clock["now"] = 130.0
    obj.device.sensor.accesses.clear()
    output = obj.read_all_ports({})
    report = obj.get_thermocouple_health()["Grill"]

    assert obj.device.sensor.accesses == [
        "status",
        "temperature",
        "ambient_temperature",
    ]
    assert output["food"]["Grill"] == 212.0
    assert report.state is ThermocoupleHealthState.HEALTHY


def test_init_device_propagates_constructor_exception_unchanged(
    probe,
    monkeypatch,
    caplog,
):
    module, _, _ = probe
    init_error = OSError("device unavailable")

    class FailingDevice:
        def __init__(self, **_):
            raise init_error

    monkeypatch.setattr(module.ReadProbes, "device_class", FailingDevice)
    obj = _new_read_probes(probe, config={})
    obj.logger = logging.getLogger("test_mcp9601_init")

    with caplog.at_level(logging.ERROR, logger="test_mcp9601_init"):
        with pytest.raises(OSError) as caught:
            obj._init_device()

    assert caught.value is init_error
    assert "address=0x61" in caplog.text
