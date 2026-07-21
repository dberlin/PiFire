"""Coverage for probes/ads1115.py, probes/ads1115_adafruit.py, and
probes/ads1015_adafruit.py -- the I2C ADC probe modules.

read_all_ports itself is inherited unmodified from probes.base.ProbeInterface
(already covered in tests/unit/probes/test_base.py); what's module-specific
here is each ADSDevice.read_voltage() (hardware-library-specific) and
ReadProbes._init_device (bus-kind/address wiring + the init-failure
log-and-reraise branch). Each class's happy-path test constructs ReadProbes
normally (full __init__, no bypass -- the base class needs no hardware once
the ADC library is faked) with a known raw ADC reading and asserts it flows
through base._voltage_to_temp's Steinhart-Hart math to the same reference
temperature independently verified in test_base.py
(voltage=1500mV, profile=TWPS00, Rd=10000 default, Vs=3.28 default ->
tempF=193.74864976188468, Tr=8427 ohms).

One test class per module, per the task brief.
"""

import sys
import types
import importlib
import logging

import pytest


# --- Reference thermistor profile (real values) + expected math, matching
# tests/unit/probes/test_base.py so results are independently cross-checked. ---
PROFILE_A = 0.00073431401
PROFILE_B = 0.0002157437
PROFILE_C = 9.515686e-8
EXPECTED_TEMP_F = 193.74864976188468
EXPECTED_TR = 8427


def _profile():
    return {"A": PROFILE_A, "B": PROFILE_B, "C": PROFILE_C}


def _probe_info(entries):
    """entries: [(port, label, type), ...]"""
    return [
        {"device": "TESTDEV", "port": port, "label": label, "type": ptype, "profile": _profile()}
        for port, label, ptype in entries
    ]


def _device_info(config=None):
    return {
        "device": "TESTDEV",
        "ports": [
            "ADC0",
            "ADC1",
            "ADC2",
            "ADC3",
        ],  # pre-populated, as real callers do (see probes/base.py:set_profiles)
        "config": config or {},
    }


# ===========================================================================
# probes/ads1115.py  (import ADS1115)
# ===========================================================================


def _install_fake_ADS1115_lib(monkeypatch, voltages, fail_channels=()):
    fake = types.ModuleType("ADS1115")

    class FakeADS1115:
        def __init__(self, address=0x48):
            self.address = address

        def readADCSingleEnded(self, channel):
            if channel in fail_channels:
                raise OSError("simulated I2C failure")
            return voltages[channel]

    fake.ADS1115 = FakeADS1115
    monkeypatch.setitem(sys.modules, "ADS1115", fake)
    return fake


class TestADS1115:
    def _load(self, monkeypatch, voltages, fail_channels=()):
        _install_fake_ADS1115_lib(monkeypatch, voltages, fail_channels)
        import probes.ads1115 as probe

        importlib.reload(probe)  # bind the fake ADS1115 lib
        return probe

    def test_read_all_ports_maps_known_adc_count_to_temperature(self, monkeypatch):
        probe = self._load(monkeypatch, {0: 1500, 1: 1500, 2: 1500})
        probe_info = _probe_info([("ADC0", "Probe1", "Primary"), ("ADC1", "Probe2", "Food"), ("ADC2", "Probe3", "Aux")])
        obj = probe.ReadProbes(probe_info, _device_info(), "F")

        result = obj.read_all_ports(obj.output_data)

        assert result["primary"]["Probe1"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)
        assert result["tr"]["Probe1"] == EXPECTED_TR
        assert result["food"]["Probe2"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)
        assert result["aux"]["Probe3"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)

    def test_read_voltage_error_returns_zero(self, monkeypatch, caplog):
        probe = self._load(monkeypatch, {0: 1500}, fail_channels={0})
        dev = probe.ADSDevice(i2c_bus_addr=0x48)

        with caplog.at_level(logging.ERROR, logger="control"):
            voltage = dev.read_voltage("ADC0")

        assert voltage == 0
        assert "Exception occurred" in caplog.text

    def test_adsdevice_basic_bus_uses_default_address(self, monkeypatch):
        probe = self._load(monkeypatch, {0: 1500})
        dev = probe.ADSDevice()

        assert dev.ads.address == 0x48
        assert dev.get_status() == {}

    def test_adsdevice_extended_bus_repoints_i2c_via_smbus2(self, monkeypatch):
        probe = self._load(monkeypatch, {0: 1500})
        fake_smbus2 = types.ModuleType("smbus2")
        captured = {}

        class FakeSMBus:
            def __init__(self, bus_num):
                captured["bus_num"] = bus_num

        fake_smbus2.SMBus = FakeSMBus
        monkeypatch.setitem(sys.modules, "smbus2", fake_smbus2)
        monkeypatch.setattr(probe, "resolve_i2c_bus", lambda selector: 42)

        dev = probe.ADSDevice(i2c_bus_addr=0x49, i2c_bus_kind="extended", i2c_bus_num="serial:ABC123")

        assert captured["bus_num"] == 42
        assert isinstance(dev.ads.i2c, FakeSMBus)

    def test_init_device_wires_address_and_bus_kind(self, monkeypatch):
        probe = self._load(monkeypatch, {0: 1500})
        captured = {}

        class SpyADSDevice:
            def __init__(self, i2c_bus_addr, i2c_bus_kind, i2c_bus_num):
                captured["args"] = (i2c_bus_addr, i2c_bus_kind, i2c_bus_num)

        monkeypatch.setattr(probe, "ADSDevice", SpyADSDevice)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {"i2c_bus_addr": "0x49", "i2c_bus_kind": "extended", "i2c_bus_num": "3"}}
        obj._init_device()

        assert obj.device_info["ports"] == ["ADC0", "ADC1", "ADC2", "ADC3"]
        assert obj.time_delay == 0.008
        assert captured["args"] == (0x49, "extended", "3")

    def test_init_device_defaults(self, monkeypatch):
        probe = self._load(monkeypatch, {0: 1500})
        captured = {}

        class SpyADSDevice:
            def __init__(self, i2c_bus_addr, i2c_bus_kind, i2c_bus_num):
                captured["args"] = (i2c_bus_addr, i2c_bus_kind, i2c_bus_num)

        monkeypatch.setattr(probe, "ADSDevice", SpyADSDevice)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {}}
        obj._init_device()

        assert captured["args"] == (0x48, "basic", 0)

    def test_init_device_failure_logs_and_reraises(self, monkeypatch, caplog):
        probe = self._load(monkeypatch, {0: 1500})

        def boom(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(probe, "ADSDevice", boom)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {}}

        with caplog.at_level(logging.ERROR, logger="control"), pytest.raises(RuntimeError, match="boom"):
            obj._init_device()

        assert "Something went wrong" in caplog.text


# ===========================================================================
# Shared fake for the two Adafruit-based modules
# (adafruit_ads1x15.ads1115 / adafruit_ads1x15.ads1015 + .analog_in.AnalogIn)
# ===========================================================================


def _install_fake_adafruit_ads1x15(monkeypatch, submodule_name, class_name, voltages_by_channel, fail_channels=()):
    pkg = types.ModuleType("adafruit_ads1x15")
    sub = types.ModuleType(f"adafruit_ads1x15.{submodule_name}")

    class FakeADS:
        def __init__(self, i2c, address=0x48):
            self.i2c = i2c
            self.address = address

    setattr(sub, class_name, FakeADS)
    sub.P0, sub.P1, sub.P2, sub.P3 = 0, 1, 2, 3

    analog_in_mod = types.ModuleType("adafruit_ads1x15.analog_in")

    class FakeAnalogIn:
        def __init__(self, ads, channel):
            self.ads = ads
            self.channel = channel

        @property
        def voltage(self):
            if self.channel in fail_channels:
                raise OSError("simulated I2C read failure")
            return voltages_by_channel[self.channel]

    analog_in_mod.AnalogIn = FakeAnalogIn

    setattr(pkg, submodule_name, sub)
    monkeypatch.setitem(sys.modules, "adafruit_ads1x15", pkg)
    monkeypatch.setitem(sys.modules, f"adafruit_ads1x15.{submodule_name}", sub)
    monkeypatch.setitem(sys.modules, "adafruit_ads1x15.analog_in", analog_in_mod)
    return sub, analog_in_mod


# ===========================================================================
# probes/ads1115_adafruit.py  (adafruit_ads1x15.ads1115 / .analog_in)
# ===========================================================================


class TestADS1115Adafruit:
    def _load(self, monkeypatch, voltages, fail_channels=()):
        _install_fake_adafruit_ads1x15(monkeypatch, "ads1115", "ADS1115", voltages, fail_channels)
        import probes.ads1115_adafruit as probe

        importlib.reload(probe)  # bind the fake adafruit_ads1x15
        monkeypatch.setattr(probe, "open_i2c_bus", lambda kind, num: "FAKE_I2C_BUS")
        return probe

    def test_read_all_ports_maps_known_adc_voltage_to_temperature(self, monkeypatch):
        # 1.5V -> AnalogIn.voltage -> math.floor(1.5*1000) = 1500mV, matching
        # the reference case computed in test_base.py.
        probe = self._load(monkeypatch, {0: 1.5, 1: 1.5, 2: 1.5})
        probe_info = _probe_info([("ADC0", "Probe1", "Primary"), ("ADC1", "Probe2", "Food"), ("ADC2", "Probe3", "Aux")])
        obj = probe.ReadProbes(probe_info, _device_info(), "F")

        result = obj.read_all_ports(obj.output_data)

        assert result["primary"]["Probe1"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)
        assert result["tr"]["Probe1"] == EXPECTED_TR
        assert result["food"]["Probe2"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)
        assert result["aux"]["Probe3"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)

    def test_read_voltage_error_returns_zero(self, monkeypatch, caplog):
        probe = self._load(monkeypatch, {0: 1.5}, fail_channels={0})
        dev = probe.ADSDevice(i2c_bus_addr=0x48)

        with caplog.at_level(logging.ERROR, logger="control"):
            voltage = dev.read_voltage("ADC0")

        assert voltage == 0
        assert "Exception occurred" in caplog.text

    def test_adsdevice_opens_bus_via_factory(self, monkeypatch):
        probe = self._load(monkeypatch, {0: 1.5})
        opened = {}

        def fake_open(kind, num):
            opened["args"] = (kind, num)
            return "FAKE_BUS"

        monkeypatch.setattr(probe, "open_i2c_bus", fake_open)

        dev = probe.ADSDevice(i2c_bus_addr=0x49, i2c_bus_kind="ft232h", i2c_bus_num="1")

        assert opened["args"] == ("ft232h", "1")
        assert dev.i2c == "FAKE_BUS"
        assert dev.ads.address == 0x49

    def test_init_device_defaults(self, monkeypatch):
        probe = self._load(monkeypatch, {0: 1.5})
        captured = {}

        class SpyADSDevice:
            def __init__(self, i2c_bus_addr, i2c_bus_kind, i2c_bus_num):
                captured["args"] = (i2c_bus_addr, i2c_bus_kind, i2c_bus_num)

        monkeypatch.setattr(probe, "ADSDevice", SpyADSDevice)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {}}
        obj._init_device()

        assert obj.device_info["ports"] == ["ADC0", "ADC1", "ADC2", "ADC3"]
        assert obj.time_delay == 0.008
        assert captured["args"] == (0x48, "basic", 0)

    def test_init_device_failure_logs_and_reraises(self, monkeypatch, caplog):
        probe = self._load(monkeypatch, {0: 1.5})

        def boom(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(probe, "ADSDevice", boom)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {}}

        with caplog.at_level(logging.ERROR, logger="control"), pytest.raises(RuntimeError, match="boom"):
            obj._init_device()

        assert "Something went wrong" in caplog.text


# ===========================================================================
# probes/ads1015_adafruit.py  (adafruit_ads1x15.ads1015 / .analog_in)
# ===========================================================================


class TestADS1015Adafruit:
    def _load(self, monkeypatch, voltages, fail_channels=()):
        _install_fake_adafruit_ads1x15(monkeypatch, "ads1015", "ADS1015", voltages, fail_channels)
        import probes.ads1015_adafruit as probe

        importlib.reload(probe)  # bind the fake adafruit_ads1x15
        monkeypatch.setattr(probe, "open_i2c_bus", lambda kind, num: "FAKE_I2C_BUS")
        return probe

    def test_read_all_ports_maps_known_adc_voltage_to_temperature(self, monkeypatch):
        probe = self._load(monkeypatch, {0: 1.5, 1: 1.5, 2: 1.5})
        probe_info = _probe_info([("ADC0", "Probe1", "Primary"), ("ADC1", "Probe2", "Food"), ("ADC2", "Probe3", "Aux")])
        obj = probe.ReadProbes(probe_info, _device_info(), "F")

        result = obj.read_all_ports(obj.output_data)

        assert result["primary"]["Probe1"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)
        assert result["tr"]["Probe1"] == EXPECTED_TR
        assert result["food"]["Probe2"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)
        assert result["aux"]["Probe3"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)

    def test_read_voltage_error_returns_zero(self, monkeypatch, caplog):
        probe = self._load(monkeypatch, {0: 1.5}, fail_channels={0})
        dev = probe.ADSDevice(i2c_bus_addr=0x48)

        with caplog.at_level(logging.ERROR, logger="control"):
            voltage = dev.read_voltage("ADC0")

        assert voltage == 0
        assert "Exception occurred" in caplog.text

    def test_adsdevice_opens_bus_via_factory(self, monkeypatch):
        probe = self._load(monkeypatch, {0: 1.5})
        opened = {}

        def fake_open(kind, num):
            opened["args"] = (kind, num)
            return "FAKE_BUS"

        monkeypatch.setattr(probe, "open_i2c_bus", fake_open)

        dev = probe.ADSDevice(i2c_bus_addr=0x49, i2c_bus_kind="mcp2221", i2c_bus_num="serial:XYZ")

        assert opened["args"] == ("mcp2221", "serial:XYZ")
        assert dev.i2c == "FAKE_BUS"
        assert dev.ads.address == 0x49

    def test_init_device_defaults(self, monkeypatch):
        probe = self._load(monkeypatch, {0: 1.5})
        captured = {}

        class SpyADSDevice:
            def __init__(self, i2c_bus_addr, i2c_bus_kind, i2c_bus_num):
                captured["args"] = (i2c_bus_addr, i2c_bus_kind, i2c_bus_num)

        monkeypatch.setattr(probe, "ADSDevice", SpyADSDevice)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {}}
        obj._init_device()

        assert obj.device_info["ports"] == ["ADC0", "ADC1", "ADC2", "ADC3"]
        assert obj.time_delay == 0.008
        assert captured["args"] == (0x48, "basic", 0)

    def test_init_device_failure_logs_and_reraises(self, monkeypatch, caplog):
        probe = self._load(monkeypatch, {0: 1.5})

        def boom(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(probe, "ADSDevice", boom)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {}}

        with caplog.at_level(logging.ERROR, logger="control"), pytest.raises(RuntimeError, match="boom"):
            obj._init_device()

        assert "Something went wrong" in caplog.text
