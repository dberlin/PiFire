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

The Adafruit adapters share one parameterized public-module contract.
"""

import sys
import types
import importlib
import logging

import pytest
from common.i2c_bus_config import BasicBus, FT232HBus


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
        from common.i2c_bus_config import KernelBusNumber

        probe = self._load(monkeypatch, {0: 1500})
        fake_smbus2 = types.ModuleType("smbus2")
        captured = {}

        class FakeSMBus:
            def __init__(self, bus_num):
                captured["bus_num"] = bus_num

        fake_smbus2.SMBus = FakeSMBus
        monkeypatch.setitem(sys.modules, "smbus2", fake_smbus2)

        dev = probe.ADSDevice(i2c_bus_addr=0x49, bus=KernelBusNumber(bus_num=42))

        assert captured["bus_num"] == 42
        assert isinstance(dev.ads.i2c, FakeSMBus)

    def test_init_device_wires_address_and_bus(self, monkeypatch):
        from common.i2c_bus_config import KernelAdapterName

        probe = self._load(monkeypatch, {0: 1500})
        captured = {}

        class SpyADSDevice:
            def __init__(self, i2c_bus_addr, bus):
                captured["args"] = (i2c_bus_addr, bus)

        monkeypatch.setattr(probe, "ADSDevice", SpyADSDevice)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {"i2c_bus_addr": "0x49", "i2c_bus": {"kind": "kernel", "adapter": "CP2112"}}}
        obj._init_device()

        assert obj.device_info["ports"] == ["ADC0", "ADC1", "ADC2", "ADC3"]
        assert obj.time_delay == 0.008
        assert captured["args"] == (0x49, KernelAdapterName(adapter="CP2112"))

    def test_init_device_defaults(self, monkeypatch):
        from common.i2c_bus_config import BasicBus

        probe = self._load(monkeypatch, {0: 1500})
        captured = {}

        class SpyADSDevice:
            def __init__(self, i2c_bus_addr, bus):
                captured["args"] = (i2c_bus_addr, bus)

        monkeypatch.setattr(probe, "ADSDevice", SpyADSDevice)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {}}
        obj._init_device()

        assert captured["args"] == (0x48, BasicBus())

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

    def test_close_releases_an_extended_bus_handle(self, monkeypatch):
        """The extended bus kind opens its OWN smbus2 handle per device (the
        ADS1115 library hardcodes SMBus(1), so the module repoints it), which
        makes it this instance's to release on a probe-map rebuild."""
        probe = self._load(monkeypatch, {0: 1500})
        fake_smbus2 = types.ModuleType("smbus2")
        closed = []

        class FakeSMBus:
            def __init__(self, bus_num):
                self.bus_num = bus_num

            def close(self):
                closed.append(self.bus_num)

        fake_smbus2.SMBus = FakeSMBus
        monkeypatch.setitem(sys.modules, "smbus2", fake_smbus2)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {"i2c_bus": {"kind": "kernel", "bus_num": 42}}}
        obj._init_device()

        obj.close()

        assert closed == [42]

    def test_close_on_the_basic_bus_does_nothing(self, monkeypatch):
        """On the basic bus the ADS1115 library owns the smbus handle
        internally; this module opened nothing, so it must close nothing."""
        probe = self._load(monkeypatch, {0: 1500})
        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {}}
        obj._init_device()

        obj.close()  # must not raise


# ===========================================================================
# Shared fake for the two Adafruit-based modules
# (adafruit_ads1x15.ads1115 / adafruit_ads1x15.ads1015 + .analog_in.AnalogIn)
# ===========================================================================


def _install_fake_adafruit_ads1x15(
    monkeypatch,
    submodule_name,
    class_name,
    voltages_by_channel,
    fail_channels=(),
    *,
    channels=(0, 1, 2, 3),
    observed_channels=None,
):
    for module_name in (
        "adafruit_ads1x15.ads1015",
        "adafruit_ads1x15.ads1115",
        "adafruit_ads1x15.analog_in",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    pkg = types.ModuleType("adafruit_ads1x15")
    pkg.__path__ = []
    sub = types.ModuleType(f"adafruit_ads1x15.{submodule_name}")

    class FakeADS:
        def __init__(self, i2c, address=0x48):
            self.i2c = i2c
            self.address = address

    setattr(sub, class_name, FakeADS)
    for port_name, channel in zip(("P0", "P1", "P2", "P3"), channels, strict=True):
        setattr(sub, port_name, channel)

    analog_in_mod = types.ModuleType("adafruit_ads1x15.analog_in")

    class FakeAnalogIn:
        def __init__(self, ads, channel):
            self.ads = ads
            self.channel = channel
            if observed_channels is not None:
                observed_channels.append(channel)

        @property
        def voltage(self):
            if self.channel in fail_channels:
                raise OSError("simulated I2C read failure")
            return voltages_by_channel[self.channel]

    setattr(analog_in_mod, "AnalogIn", FakeAnalogIn)

    setattr(pkg, submodule_name, sub)
    monkeypatch.setitem(sys.modules, "adafruit_ads1x15", pkg)
    monkeypatch.setitem(sys.modules, f"adafruit_ads1x15.{submodule_name}", sub)
    monkeypatch.setitem(sys.modules, "adafruit_ads1x15.analog_in", analog_in_mod)
    return sub, analog_in_mod


# ===========================================================================
# probes/ads1115_adafruit.py and probes/ads1015_adafruit.py
#
# The two modules are the same driver over a different Adafruit chip class,
# and their tests were byte-identical apart from _load(). Parametrizing the
# class keeps both chips covered while there is one copy of each assertion --
# and a new chip variant now costs one tuple, not a cloned class.
# ===========================================================================


@pytest.mark.parametrize(
    ("module_name", "chip_module", "chip_class"),
    [
        ("probes.ads1115_adafruit", "ads1115", "ADS1115"),
        ("probes.ads1015_adafruit", "ads1015", "ADS1015"),
    ],
    ids=["ads1115", "ads1015"],
)
class TestAdafruitADS:
    def _load(
        self,
        monkeypatch,
        module_name,
        chip_module,
        chip_class,
        voltages,
        fail_channels=(),
        *,
        channels=(0, 1, 2, 3),
        observed_channels=None,
        open_bus=None,
    ):
        from common import i2c_bus as i2c_bus_module

        _install_fake_adafruit_ads1x15(
            monkeypatch,
            chip_module,
            chip_class,
            voltages,
            fail_channels,
            channels=channels,
            observed_channels=observed_channels,
        )
        monkeypatch.setattr(
            i2c_bus_module,
            "open_i2c_bus",
            open_bus or (lambda bus: "FAKE_I2C_BUS"),
        )
        for public_module in (
            "probes.ads1015_adafruit",
            "probes.ads1115_adafruit",
            "probes._ads1x15_adafruit",
        ):
            monkeypatch.delitem(sys.modules, public_module, raising=False)
        return importlib.import_module(module_name)

    def test_public_import_requires_only_selected_chip(self, monkeypatch, module_name, chip_module, chip_class):
        probe = self._load(monkeypatch, module_name, chip_module, chip_class, {})
        other_chip = "ads1015" if chip_module == "ads1115" else "ads1115"

        assert probe.__name__ == module_name
        assert f"adafruit_ads1x15.{chip_module}" in sys.modules
        assert f"adafruit_ads1x15.{other_chip}" not in sys.modules
        assert f"probes.{other_chip}_adafruit" not in sys.modules

    def test_public_open_i2c_bus_seam_is_live_after_import(self, monkeypatch, module_name, chip_module, chip_class):
        probe = self._load(monkeypatch, module_name, chip_module, chip_class, {})
        configured_bus = FT232HBus(url="ftdi://ftdi:232h/1")
        shared_bus = object()
        opened_buses = []

        def live_open_i2c_bus(bus):
            opened_buses.append(bus)
            return shared_bus

        monkeypatch.setattr(probe, "open_i2c_bus", live_open_i2c_bus)

        dev = probe.ADSDevice(i2c_bus_addr=0x49, bus=configured_bus)

        assert opened_buses == [configured_bus]
        assert dev.i2c is shared_bus
        assert dev.ads.address == 0x49

    def test_public_analog_in_seam_is_live_after_import(self, monkeypatch, module_name, chip_module, chip_class):
        channels = tuple(f"{chip_module}.P{index}" for index in range(4))
        probe = self._load(
            monkeypatch,
            module_name,
            chip_module,
            chip_class,
            {},
            channels=channels,
        )
        observed = []

        class LiveAnalogIn:
            def __init__(self, ads, channel):
                observed.append((ads, channel))

            @property
            def voltage(self):
                return 1.2349

        monkeypatch.setattr(probe, "AnalogIn", LiveAnalogIn)
        dev = probe.ADSDevice(i2c_bus_addr=0x48)

        voltage = dev.read_voltage("ADC3")

        assert voltage == 1234
        assert observed == [(dev.ads, channels[3])]

    def test_dynamic_module_and_public_class_names_remain_stable(
        self, monkeypatch, module_name, chip_module, chip_class
    ):
        module_key = module_name.removeprefix("probes.")
        record = {
            "module": module_key,
            "module_filename": module_key,
            "config": {},
        }
        self._load(monkeypatch, module_name, chip_module, chip_class, {0: 1.5})

        loaded = importlib.import_module(f"probes.{record['module_filename']}")
        obj = loaded.ReadProbes(
            _probe_info([("ADC0", "Probe1", "Primary")]),
            _device_info(record["config"]),
            "F",
        )

        assert loaded.__name__ == module_name
        assert loaded.ADSDevice.__name__ == "ADSDevice"
        assert loaded.ADSDevice.__module__ == module_name
        assert loaded.ReadProbes.__name__ == "ReadProbes"
        assert loaded.ReadProbes.__module__ == module_name
        assert type(obj) is loaded.ReadProbes

    def test_public_adsdevice_selects_chip_through_shared_base(self, monkeypatch, module_name, chip_module, chip_class):
        channels = tuple(f"{chip_module}.P{index}" for index in range(4))
        probe = self._load(
            monkeypatch,
            module_name,
            chip_module,
            chip_class,
            {},
            channels=channels,
        )
        shared_base = probe.ADSDevice.__mro__[1]
        chip_api = sys.modules[f"adafruit_ads1x15.{chip_module}"]

        assert shared_base.__module__ == "probes._ads1x15_adafruit"
        assert shared_base.__name__ == "AdafruitADSDevice"
        assert probe.ADSDevice.CHIP_FACTORY is getattr(chip_api, chip_class)
        assert probe.ADSDevice.CHANNELS == {
            "ADC0": channels[0],
            "ADC1": channels[1],
            "ADC2": channels[2],
            "ADC3": channels[3],
        }

    def test_read_voltage_maps_exact_chip_channels_and_floors_millivolts(
        self, monkeypatch, module_name, chip_module, chip_class
    ):
        channels = tuple(f"{chip_module}.P{index}" for index in range(4))
        observed_channels = []
        probe = self._load(
            monkeypatch,
            module_name,
            chip_module,
            chip_class,
            {
                channels[0]: 1.2349,
                channels[1]: 2.3459,
                channels[2]: 3.4569,
                channels[3]: 4.5679,
            },
            channels=channels,
            observed_channels=observed_channels,
        )
        dev = probe.ADSDevice(i2c_bus_addr=0x48)

        voltages = [
            dev.read_voltage("ADC0"),
            dev.read_voltage("ADC1"),
            dev.read_voltage("ADC2"),
            dev.read_voltage("ADC3"),
        ]

        assert voltages == [1234, 2345, 3456, 4567]
        assert all(isinstance(voltage, int) for voltage in voltages)
        assert observed_channels == list(channels)

    def test_read_all_ports_maps_known_adc_voltage_to_temperature(
        self, monkeypatch, module_name, chip_module, chip_class
    ):
        # 1.5V -> AnalogIn.voltage -> floor(1.5*1000) = 1500mV, matching
        # the reference case computed in test_base.py.
        probe = self._load(
            monkeypatch,
            module_name,
            chip_module,
            chip_class,
            {0: 1.5, 1: 1.5, 2: 1.5},
        )
        probe_info = _probe_info(
            [
                ("ADC0", "Probe1", "Primary"),
                ("ADC1", "Probe2", "Food"),
                ("ADC2", "Probe3", "Aux"),
            ]
        )
        obj = probe.ReadProbes(probe_info, _device_info(), "F")

        result = obj.read_all_ports(obj.output_data)

        assert result["primary"]["Probe1"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)
        assert result["tr"]["Probe1"] == EXPECTED_TR
        assert result["food"]["Probe2"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)
        assert result["aux"]["Probe3"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)

    def test_read_voltage_error_logs_port_and_returns_zero(
        self, monkeypatch, caplog, module_name, chip_module, chip_class
    ):
        probe = self._load(
            monkeypatch,
            module_name,
            chip_module,
            chip_class,
            {2: 1.5},
            fail_channels={2},
        )
        dev = probe.ADSDevice(i2c_bus_addr=0x48)

        with caplog.at_level(logging.ERROR, logger="control"):
            voltage = dev.read_voltage("ADC2")

        assert voltage == 0
        assert "Exception occurred while reading probe port ADC2" in caplog.text

    def test_init_device_defaults_and_uses_module_local_adsdevice(
        self, monkeypatch, module_name, chip_module, chip_class
    ):
        probe = self._load(monkeypatch, module_name, chip_module, chip_class, {})
        captured = {}

        class SpyADSDevice:
            def __init__(self, i2c_bus_addr, bus):
                captured["args"] = (i2c_bus_addr, bus)

        monkeypatch.setattr(probe, "ADSDevice", SpyADSDevice)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {}}
        obj._init_device()

        assert obj.device_info["ports"] == ["ADC0", "ADC1", "ADC2", "ADC3"]
        assert obj.time_delay == 0.008
        assert captured["args"] == (0x48, BasicBus())

    @pytest.mark.parametrize(
        ("stored_bus", "expected_bus"),
        [
            (None, BasicBus()),
            (
                {"kind": "ft232h", "url": "ftdi://ftdi:232h/1"},
                FT232HBus(url="ftdi://ftdi:232h/1"),
            ),
        ],
        ids=["default-basic", "explicit-ft232h"],
    )
    def test_init_device_opens_parsed_bus_once_with_address(
        self,
        monkeypatch,
        module_name,
        chip_module,
        chip_class,
        stored_bus,
        expected_bus,
    ):
        opened_buses = []
        shared_bus = object()

        def fake_open(bus):
            opened_buses.append(bus)
            return shared_bus

        probe = self._load(
            monkeypatch,
            module_name,
            chip_module,
            chip_class,
            {},
            open_bus=fake_open,
        )
        config = {"i2c_bus_addr": "0x49"}
        if stored_bus is not None:
            config["i2c_bus"] = stored_bus
        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": config}

        obj._init_device()

        chip_api = sys.modules[f"adafruit_ads1x15.{chip_module}"]
        assert opened_buses == [expected_bus]
        assert obj.device.i2c is shared_bus
        assert obj.device.ads.address == 0x49
        assert type(obj.device.ads) is getattr(chip_api, chip_class)

    def test_close_does_not_close_shared_bus(self, monkeypatch, module_name, chip_module, chip_class):
        class SharedBus:
            close_calls = 0

            def close(self):
                self.close_calls += 1

        shared_bus = SharedBus()
        probe = self._load(
            monkeypatch,
            module_name,
            chip_module,
            chip_class,
            {0: 1.5},
            open_bus=lambda bus: shared_bus,
        )
        obj = probe.ReadProbes(
            _probe_info([("ADC0", "Probe1", "Primary")]),
            _device_info(),
            "F",
        )

        obj.close()

        assert shared_bus.close_calls == 0

    def test_init_device_failure_logs_chip_and_reraises(
        self, monkeypatch, caplog, module_name, chip_module, chip_class
    ):
        probe = self._load(monkeypatch, module_name, chip_module, chip_class, {})

        def boom(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(probe, "ADSDevice", boom)

        obj = probe.ReadProbes.__new__(probe.ReadProbes)
        obj.logger = logging.getLogger("control")
        obj.device_info = {"config": {}}

        with caplog.at_level(logging.ERROR, logger="control"), pytest.raises(RuntimeError, match="boom"):
            obj._init_device()

        assert f"trying to initialize the {chip_class} device" in caplog.text
        assert "address=0x48" in caplog.text
