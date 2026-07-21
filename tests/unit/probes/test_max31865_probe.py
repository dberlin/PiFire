"""Coverage for probes/max31865.py -- the MAX31865 RTD probe module.

Fakes `spidev.SpiDev` (open/xfer2/close) with canned register bytes so the
module can be reloaded and exercised without real SPI hardware. Covers:
  - RTDDevice register decode -> a known real-world resistance/temperature
    (PT1000 @ 100C, IEC 60751 standard value).
  - The fault-bit branch (get_fault()'s per-bit debug logging).
  - A pinned latent bug: RTDDevice.resistance's except-handler references
    self.device_info, which RTDDevice never sets (only ReadProbes does), so
    an SPI read failure raises AttributeError instead of degrading to
    resistance=0 as the code intends.
  - ReadProbes._init_device's config parsing (defaults + overrides).
  - ReadProbes.read_all_ports (its own override, not the base one) across
    the primary/food/aux group-assignment branches, in both F and C units.
"""

import logging
import sys
import types
import importlib

import pytest


def _install_fake_spidev(monkeypatch, msb, lsb, fault_byte=0x00, xfer_error_on=None):
    """Install a fake spidev module whose SpiDev.xfer2 returns canned register
    bytes keyed by the command byte (0x01=RTD MSB, 0x02=RTD LSB, 0x07=fault,
    0x80=config write). xfer_error_on: a command byte that should raise
    instead, to exercise the SPI-failure path."""
    fake = types.ModuleType("spidev")

    class FakeSpiDev:
        def __init__(self):
            self.calls = []
            self.max_speed_hz = None
            self.mode = None

        def open(self, bus, device):
            self.calls.append(("open", bus, device))

        def xfer2(self, data):
            self.calls.append(("xfer2", list(data)))
            cmd = data[0]
            if xfer_error_on is not None and cmd == xfer_error_on:
                raise OSError("simulated SPI failure")
            if cmd == 0x01:
                return [0, msb]
            if cmd == 0x02:
                return [0, lsb]
            if cmd == 0x07:
                return [0, fault_byte]
            return [0, 0]  # config write (0x80) and anything else

        def close(self):
            self.calls.append(("close",))

    fake.SpiDev = FakeSpiDev
    monkeypatch.setitem(sys.modules, "spidev", fake)
    return fake


def _load_probe(monkeypatch, msb=0x52, lsb=0x76, fault_byte=0x00, xfer_error_on=None):
    _install_fake_spidev(monkeypatch, msb, lsb, fault_byte, xfer_error_on)
    import probes.max31865 as probe

    importlib.reload(probe)  # bind the fake spidev
    return probe


# ---------------------------------------------------------------------------
# RTDDevice register decode -> known temperature
# ---------------------------------------------------------------------------
# R(100C) = R0*(1 + A*T + B*T^2) with R0=1000 (rtd_nominal default),
# A=3.9083e-3, B=-5.775e-7 -> 1385.055 ohms. This is the standard IEC 60751
# PT1000 resistance-at-100C table value (PT100's 138.5055 ohms x10).
# adc_raw = round(R/ref_resistor*32768) with ref_resistor=4300 (default)
#         = round(1385.055/4300*32768) = 10555
# reg16 = adc_raw << 1 (fault bit clear) = 21110 -> msb=0x52, lsb=0x76.
# Decoding those bytes back gives R=1385.086 ohms -> T=100.008C (rounds to
# 100.0), confirming the fake bytes round-trip to the intended temperature.


def test_celsius_resistance_decodes_known_pt1000_temperature(monkeypatch):
    probe = _load_probe(monkeypatch, msb=0x52, lsb=0x76)
    device = probe.RTDDevice(cs=1)  # defaults: rtd_nominal=1000, ref_resistor=4300, wires=2

    assert device.resistance == pytest.approx(1385.086, abs=0.001)
    # celsius_resistance's T>=0 branch returns (temp, temp) -- the "resistance"
    # slot is NOT the ohms value in this branch (see the T<0 test below for the
    # branch where it is).
    celsius, second = device.celsius_resistance
    assert celsius == pytest.approx(100.0, abs=0.01)
    assert second == pytest.approx(100.0, abs=0.01)
    assert device.temperature == pytest.approx(100.0, abs=0.01)
    assert device.fahrenheit == pytest.approx((100.0 - 32) / 1.8, abs=0.01)
    fahrenheit, resistance = device.fahrenheit_resistance
    assert fahrenheit == pytest.approx((100.0 - 32) / 1.8, abs=0.01)
    assert resistance == pytest.approx(100.0, abs=0.01)


def test_celsius_resistance_negative_branch_uses_polynomial(monkeypatch):
    # R_back=849.948 ohms decodes (via the T>=0 quadratic) to a negative temp,
    # so the code switches to the documented rational-polynomial approximation.
    # Independently computed (see task-4-report.md derivation): celsius ~=
    # -38.170586991488044, normalized raw_reading ~= 84.99481201171875.
    probe = _load_probe(monkeypatch, msb=0x32, lsb=0x9A)
    device = probe.RTDDevice(cs=1)

    celsius, raw_reading = device.celsius_resistance

    assert celsius == pytest.approx(-38.170586991488044, abs=1e-6)
    assert raw_reading == pytest.approx(84.99481201171875, abs=1e-6)


def test_read_rtd_returns_adc_with_fault_bit_stripped(monkeypatch):
    probe = _load_probe(monkeypatch, msb=0x52, lsb=0x76)
    device = probe.RTDDevice(cs=1)

    adc = device.read_rtd()

    assert adc == 10555  # (0x52<<8 | 0x76) >> 1


def test_three_wire_config_byte(monkeypatch):
    probe = _load_probe(monkeypatch, msb=0x52, lsb=0x76)
    device = probe.RTDDevice(cs=1, wires=3)

    config_calls = [c for c in device.spi.calls if c[0] == "xfer2" and c[1][0] == 0x80]
    assert config_calls[0][1] == [0x80, 0b11010010]  # 3-wire config byte 0xD2


# ---------------------------------------------------------------------------
# Fault-bit branch: get_fault()'s per-bit debug logging
# ---------------------------------------------------------------------------


def test_read_rtd_fault_bit_triggers_all_fault_log_lines(monkeypatch, caplog):
    # lsb odd -> fault bit set; fault register 0xFF -> every bit branch fires.
    probe = _load_probe(monkeypatch, msb=0x52, lsb=0x77, fault_byte=0xFF)
    device = probe.RTDDevice(cs=1)

    with caplog.at_level(logging.DEBUG, logger="control"):
        adc = device.read_rtd()

    assert adc == 10555  # value itself is unaffected by the fault flag
    assert "RTD High Threshold" in caplog.text
    assert "RTD Low Threshold" in caplog.text
    assert "REFIN- > 0.85" in caplog.text
    assert "FORCE- Open" in caplog.text
    assert "Overvoltage/undervoltage fault" in caplog.text


def test_read_rtd_fault_bit_set_but_no_fault_flags_logs_nothing(monkeypatch, caplog):
    # lsb odd -> fault bit set (get_fault() is called), but the fault register
    # itself reads back 0x00 -> every per-bit branch's condition is False.
    probe = _load_probe(monkeypatch, msb=0x52, lsb=0x77, fault_byte=0x00)
    device = probe.RTDDevice(cs=1)

    with caplog.at_level(logging.DEBUG, logger="control"):
        device.read_rtd()

    assert "Fault SPI" not in caplog.text


# ---------------------------------------------------------------------------
# close() / get_status()
# ---------------------------------------------------------------------------


def test_close_closes_spi(monkeypatch):
    probe = _load_probe(monkeypatch, msb=0x52, lsb=0x76)
    device = probe.RTDDevice(cs=1)

    device.close()

    assert ("close",) in device.spi.calls


def test_get_status_returns_status_dict(monkeypatch):
    probe = _load_probe(monkeypatch, msb=0x52, lsb=0x76)
    device = probe.RTDDevice(cs=1)

    assert device.get_status() is device.status
    assert device.get_status() == {}


# ---------------------------------------------------------------------------
# Pinned latent bug: resistance's except-handler itself raises AttributeError
# ---------------------------------------------------------------------------


def test_resistance_error_path_raises_attributeerror_latent_bug(monkeypatch):
    """PINNED BEHAVIOR (latent bug, not fixed here): RTDDevice.resistance's
    except-handler logs via self.device_info['ports'][0], but RTDDevice never
    sets self.device_info (only ReadProbes does -- RTDDevice is a plain
    hardware-facing class). So when read_rtd() raises, the except block
    itself raises AttributeError, masking the original SPI exception and
    propagating out unhandled instead of degrading to resistance=0 as the
    surrounding try/except clearly intends. This fires as early as
    RTDDevice.__init__ -> config() -> the priming `self.temperature` read.
    """
    probe = _load_probe(monkeypatch, msb=0x52, lsb=0x76, xfer_error_on=0x01)

    with pytest.raises(AttributeError):
        probe.RTDDevice(cs=1)


# ---------------------------------------------------------------------------
# ReadProbes._init_device: config parsing (defaults + overrides)
# ---------------------------------------------------------------------------


def test_init_device_wires_config_defaults(monkeypatch):
    probe = _load_probe(monkeypatch)
    captured = {}

    class SpyRTDDevice:
        def __init__(self, cs, rtd_nominal, ref_resistor, wires):
            captured["args"] = (cs, rtd_nominal, ref_resistor, wires)

    monkeypatch.setattr(probe, "RTDDevice", SpyRTDDevice)

    obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
    obj.device_info = {"config": {}}
    obj._init_device()

    assert obj.device_info["ports"] == ["RTD0"]
    assert obj.time_delay == 0
    assert captured["args"] == (1, 1000, 4300, 2)  # documented defaults


def test_init_device_wires_config_overrides(monkeypatch):
    probe = _load_probe(monkeypatch)
    captured = {}

    class SpyRTDDevice:
        def __init__(self, cs, rtd_nominal, ref_resistor, wires):
            captured["args"] = (cs, rtd_nominal, ref_resistor, wires)

    monkeypatch.setattr(probe, "RTDDevice", SpyRTDDevice)

    obj = probe.ReadProbes.__new__(probe.ReadProbes)
    obj.device_info = {"config": {"cs": "2", "rtd_nominal": "100", "ref_resistor": "430", "wires": "3"}}
    obj._init_device()

    assert captured["args"] == (2, 100, 430, 3)


# ---------------------------------------------------------------------------
# ReadProbes.read_all_ports (module-specific override) end-to-end
# ---------------------------------------------------------------------------


def _bare_readprobes(probe, port_label, primary_port=None, food_ports=None, aux_ports=None, units="F", device=None):
    obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
    obj.device_info = {"ports": ["RTD0"]}
    obj.device = device
    obj.port_map = {"RTD0": port_label}
    obj.primary_port = primary_port
    obj.food_ports = food_ports or []
    obj.aux_ports = aux_ports or []
    obj.units = units
    obj.output_data = {"primary": {}, "food": {}, "aux": {}, "tr": {port_label: 0}}
    if obj.primary_port == "RTD0":
        obj.output_data["primary"][port_label] = 0
    elif "RTD0" in obj.food_ports:
        obj.output_data["food"][port_label] = 0
    elif "RTD0" in obj.aux_ports:
        obj.output_data["aux"][port_label] = 0
    return obj


def test_read_all_ports_primary_fahrenheit(monkeypatch):
    probe = _load_probe(monkeypatch, msb=0x52, lsb=0x76)
    device = probe.RTDDevice(cs=1)
    obj = _bare_readprobes(probe, "Probe1", primary_port="RTD0", units="F", device=device)

    result = obj.read_all_ports(obj.output_data)

    assert result["primary"]["Probe1"] == pytest.approx(212.0, abs=0.1)  # 100C -> F
    assert result["tr"]["Probe1"] == pytest.approx(1385.086, abs=0.01)


def test_read_all_ports_food_celsius(monkeypatch):
    probe = _load_probe(monkeypatch, msb=0x52, lsb=0x76)
    device = probe.RTDDevice(cs=1)
    obj = _bare_readprobes(probe, "Probe2", food_ports=["RTD0"], units="C", device=device)

    result = obj.read_all_ports(obj.output_data)

    assert result["food"]["Probe2"] == pytest.approx(100.0, abs=0.1)


def test_read_all_ports_aux_celsius(monkeypatch):
    probe = _load_probe(monkeypatch, msb=0x52, lsb=0x76)
    device = probe.RTDDevice(cs=1)
    obj = _bare_readprobes(probe, "Probe3", aux_ports=["RTD0"], units="C", device=device)

    result = obj.read_all_ports(obj.output_data)

    assert result["aux"]["Probe3"] == pytest.approx(100.0, abs=0.1)
