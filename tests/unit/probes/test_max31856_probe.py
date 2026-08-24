import sys
import types
import importlib

import pytest

from probes.thermocouple_inference import ThermocoupleJunctionSample


def _install_fake_adafruit(monkeypatch):
    """Install a fake adafruit_max31856 so the probe imports without hardware."""
    fake = types.ModuleType("adafruit_max31856")

    class ThermocoupleType:
        B = "TC_B"
        E = "TC_E"
        J = "TC_J"
        K = "TC_K"
        N = "TC_N"
        R = "TC_R"
        S = "TC_S"
        T = "TC_T"

    class FakeMAX31856:
        def __init__(self, spi, cs, thermocouple_type=None):
            self.spi = spi
            self.cs = cs
            self.thermocouple_type = thermocouple_type
            self.averaging = None
            self.noise_rejection = None
            self.temp_c = 100.0
            self.reference_c = 25.0
            self.accesses = []

        @property
        def temperature(self):
            self.accesses.append("temperature")
            if isinstance(self.temp_c, BaseException):
                raise self.temp_c
            return self.temp_c

        @property
        def reference_temperature(self):
            self.accesses.append("reference_temperature")
            if isinstance(self.reference_c, BaseException):
                raise self.reference_c
            return self.reference_c

    fake.ThermocoupleType = ThermocoupleType
    fake.MAX31856 = FakeMAX31856
    monkeypatch.setitem(sys.modules, "adafruit_max31856", fake)
    return fake


def _load_probe(monkeypatch):
    _install_fake_adafruit(monkeypatch)
    import probes.max31856_adafruit as probe

    importlib.reload(probe)  # bind the fake adafruit_max31856
    return probe


def _make_probe(monkeypatch, units):
    probe = _load_probe(monkeypatch)
    monkeypatch.setattr(
        probe,
        "resolve_spi_bus",
        lambda config, default_cs: ("SPI", "CS"),
    )
    probe_info = [
        {
            "device": "max31856",
            "port": "TC0",
            "label": "Grill",
            "type": "Primary",
            "profile": {"A": 0.00073431401, "B": 0.0002157437, "C": 9.515686e-8},
        }
    ]
    device_info = {
        "device": "max31856",
        "module": "max31856_adafruit",
        "ports": ["TC0"],
        "config": {},
    }
    return probe.ReadProbes(probe_info, device_info, units)


def test_init_device_wires_bus_type_and_settings(monkeypatch):
    probe = _load_probe(monkeypatch)

    captured = {}

    def fake_resolve(config, default_cs):
        captured["config"] = config
        captured["default_cs"] = default_cs
        return ("SPI", "CS")

    monkeypatch.setattr(probe, "resolve_spi_bus", fake_resolve)

    obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
    obj.device_info = {
        "config": {"spi_bus_kind": "mcp2210", "cs": "5", "tc_type": "J", "averaging": "8", "noise_rejection": "50"}
    }
    obj._init_device()

    assert captured["default_cs"] == "D6"
    assert obj.device_info["ports"] == ["TC0"]
    sensor = obj.device.sensor
    assert sensor.spi == "SPI" and sensor.cs == "CS"
    assert sensor.thermocouple_type == "TC_J"  # 'J' mapped via ThermocoupleType
    assert sensor.averaging == 8  # int-parsed
    assert sensor.noise_rejection == 50  # int-parsed


def test_init_device_defaults(monkeypatch):
    probe = _load_probe(monkeypatch)
    monkeypatch.setattr(probe, "resolve_spi_bus", lambda config, default_cs: ("SPI", "CS"))

    obj = probe.ReadProbes.__new__(probe.ReadProbes)
    obj.device_info = {"config": {}}  # no keys -> all defaults
    obj._init_device()

    sensor = obj.device.sensor
    assert sensor.thermocouple_type == "TC_K"  # default K
    assert sensor.averaging == 1  # default 1
    assert sensor.noise_rejection == 60  # default 60


def test_tcdevice_exposes_hot_and_reference_temperature(monkeypatch):
    probe = _load_probe(monkeypatch)
    dev = probe.TCDevice.__new__(probe.TCDevice)

    class S:
        temperature = 123.4
        reference_temperature = 24.5

    dev.sensor = S()

    assert dev.temperature == 123.4
    assert dev.reference_temperature == 24.5


@pytest.mark.parametrize(
    ("units", "expected_output"),
    [pytest.param("F", 212.0, id="fahrenheit"), pytest.param("C", 100.0, id="celsius")],
)
def test_read_captures_one_raw_celsius_junction_pair(
    monkeypatch,
    units,
    expected_output,
):
    obj = _make_probe(monkeypatch, units)
    obj.device.sensor.temp_c = 100.04
    obj.device.sensor.reference_c = 24.96

    output = obj.read_all_ports({})

    assert obj.device.sensor.accesses == ["temperature", "reference_temperature"]
    assert output["primary"]["Grill"] == expected_output
    assert obj.get_thermocouple_samples() == {
        "TC0": ThermocoupleJunctionSample(hot_c=100.04, cold_c=24.96)
    }


@pytest.mark.parametrize(
    ("failed_attribute", "expected_accesses"),
    [
        pytest.param("temp_c", ["temperature"], id="hot"),
        pytest.param(
            "reference_c",
            ["temperature", "reference_temperature"],
            id="cold",
        ),
    ],
)
def test_junction_exception_clears_previous_sample_and_reraises(
    monkeypatch,
    failed_attribute,
    expected_accesses,
):
    obj = _make_probe(monkeypatch, "F")
    obj.read_all_ports({})
    error = OSError(f"{failed_attribute} read failed")
    setattr(obj.device.sensor, failed_attribute, error)
    obj.device.sensor.accesses.clear()

    with pytest.raises(OSError) as caught:
        obj.read_all_ports({})

    assert caught.value is error
    assert obj.device.sensor.accesses == expected_accesses
    assert obj.get_thermocouple_samples() == {}
    assert obj.output_data["primary"]["Grill"] == 212.0


import json
import os


def test_manifest_max31856_entry():
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    manifest = json.load(open(os.path.join(repo_root, "wizard", "wizard_manifest.json")))
    probes = manifest["modules"]["probes"]
    assert "max31856_adafruit" in probes
    entry = probes["max31856_adafruit"]

    ds = entry["device_specific"]
    assert ds["type"] == "thermocouple"
    assert ds["ports"] == ["TC0"]

    labels = [item["label"] for item in ds["config"]]
    for required in ("cs", "spi_bus_kind", "mcp2210_serial", "tc_type", "averaging", "noise_rejection"):
        assert required in labels

    tc = next(i for i in ds["config"] if i["label"] == "tc_type")
    assert tc["list_values"] == ["B", "E", "J", "K", "N", "R", "S", "T"]
    assert tc["default"] == "K"

    avg = next(i for i in ds["config"] if i["label"] == "averaging")
    assert avg["list_values"] == ["1", "2", "4", "8", "16"]

    nr = next(i for i in ds["config"] if i["label"] == "noise_rejection")
    assert nr["list_values"] == ["60", "50"]

    deps = " ".join(entry["py_dependencies"])
    assert "adafruit-circuitpython-max31856" in deps
    # hid is the bridge driver's only external requirement. The driver itself is
    # vendored at grillplat/mcp2210, so pip-installing a distribution of that
    # name fetches something nothing imports -- and would shadow the vendored
    # package for anyone who still imports the bare name.
    assert "hid" in deps
    assert "mcp2210" not in deps
