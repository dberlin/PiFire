"""Coverage for probes/base.py -- the shared ProbeInterface base class.

Exercises: construction / port classification (_discover_port_types,
_build_port_map, _build_output_data, _build_ports, set_profiles),
_init_device_with_retries' retry/exhaustion behavior, the Steinhart-Hart
voltage<->temperature<->resistance math (_voltage_to_temp / _temp_to_resistance)
including bounds-clamping and error paths, read_all_ports, apply_filters
(Kalman wiring, applies_kalman=False bypass, debug-log dedup, unmapped-port
skip), update_units, get_port_map/get_device_info, and the F/C helpers.

The reference thermistor coefficients (A/B/C) below are the real
"Thermoworks-Pro-Series-HeaterMeter" (TWPS00) profile from
wizard/wizard_manifest.json, so the "expected" numbers are independently
computed (see comments) rather than copy-pasted from probes/base.py.

resolve_spi_bus/_gp_index/resolve_mcp2210 (the SPI/MCP2210 hardware-bus
helpers at the top of base.py) are already covered by
tests/unit/mcp2210/test_mcp2210_probe_bus.py and
tests/unit/probes/test_max31856_probe.py; only the one branch they don't
hit (basic-bus unknown chip-select) is added here for completeness.
"""

import logging
import sys
from types import SimpleNamespace

import pytest

from probes import base
from probes.base import FakeDevice, ProbeInterface, resolve_spi_bus
from probes.kalman import TempKalman

# --- Reference thermistor profile (real values, not synthetic) ---
# wizard/wizard_manifest.json: probes.ADS1115 -> Probe-1/2 -> profile
# "Thermoworks-Pro-Series-HeaterMeter" (id TWPS00).
PROFILE_A = 0.00073431401
PROFILE_B = 0.0002157437
PROFILE_C = 9.515686e-8


def _profile():
    return {"A": PROFILE_A, "B": PROFILE_B, "C": PROFILE_C}


def _device_info(ports, config=None):
    return {"device": "TESTDEV", "ports": ports, "config": config or {}}


def _probe(port, label, ptype, device="TESTDEV", profile=None):
    return {"device": device, "port": port, "label": label, "type": ptype, "profile": profile or _profile()}


def _make_probe(probe_info, device_info, units="F"):
    return ProbeInterface(probe_info, device_info, units)


class FakeStatusDevice:
    """Scripted hardware device: fixed voltages per port, plus get_status()."""

    def __init__(self, voltages, status="ok"):
        self.voltages = voltages
        self.status = status

    def read_voltage(self, port):
        return self.voltages.get(port)

    def get_status(self):
        return self.status


# ---------------------------------------------------------------------------
# Construction: port classification, output-data/port-filter building, profiles
# ---------------------------------------------------------------------------


def test_init_classifies_ports_and_builds_structures():
    device_info = _device_info(["P1", "P2", "P3", "P4"], config={"P1_rd": "5000", "transient": "True"})
    probe_info = [
        _probe("P1", "Primary1", "Primary"),
        _probe("P2", "Food1", "Food"),
        _probe("P3", "Aux1", "Aux"),
        _probe("P4", "Weird1", "Unknown"),  # not Primary/Food/Aux
        _probe("OTHER", "Ignored", "Primary", device="OTHERDEV"),  # different device: ignored
    ]
    obj = _make_probe(probe_info, device_info, units="F")

    assert obj.transient is True
    assert obj.primary_port == "P1"
    assert obj.food_ports == ["P2"]
    assert obj.aux_ports == ["P3"]
    assert obj.port_map == {"P1": "Primary1", "P2": "Food1", "P3": "Aux1", "P4": "Weird1"}
    assert obj.output_data["primary"] == {"Primary1": 0}
    assert obj.output_data["food"] == {"Food1": 0}
    assert obj.output_data["aux"] == {"Aux1": 0}
    assert obj.output_data["tr"] == {"Primary1": 0, "Food1": 0, "Aux1": 0, "Weird1": 0}
    assert set(obj.port_filters.keys()) == {"P1", "P2", "P3", "P4"}
    assert all(isinstance(k, TempKalman) for k in obj.port_filters.values())
    assert isinstance(obj.device, FakeDevice)  # base's own default _init_device

    # set_profiles: per-port Rd override vs. default, and a shared Vs default
    assert obj.probe_profiles["P1"]["Rd"] == 5000  # overridden via "P1_rd"
    assert obj.probe_profiles["P2"]["Rd"] == 10000  # default (no "P2_rd" key)
    assert obj.probe_profiles["P1"]["Vs"] == pytest.approx(3.28)  # default voltage_ref


def test_init_transient_defaults_false_when_key_absent():
    device_info = _device_info(["P1"])
    probe_info = [_probe("P1", "Primary1", "Primary")]
    obj = _make_probe(probe_info, device_info)
    assert obj.transient is False


def test_init_transient_explicit_false_string():
    device_info = _device_info(["P1"], config={"transient": "False"})
    probe_info = [_probe("P1", "Primary1", "Primary")]
    obj = _make_probe(probe_info, device_info)
    assert obj.transient is False


def test_non_thermocouple_probe_has_no_junction_samples():
    obj = _make_probe(
        [_probe("P1", "Primary1", "Primary")],
        _device_info(["P1"]),
    )

    assert obj.get_thermocouple_samples() == {}


# ---------------------------------------------------------------------------
# _init_device_with_retries: retry-then-succeed, and exhaustion
# ---------------------------------------------------------------------------


class _RetryProbe(ProbeInterface):
    device_init_retry_delay = 0  # skip the real time.sleep() between attempts

    def _init_device(self):
        self.attempts = getattr(self, "attempts", 0) + 1
        if self.attempts < self.fail_until:
            raise RuntimeError(f"boom-{self.attempts}")
        self.device = FakeDevice(self.port_map, self.primary_port, self.units)
        self.time_delay = 0


def _bare_retry_probe(fail_until, retries):
    # Bypass the heavy ProbeInterface.__init__ (matches the convention used in
    # test_max31856_probe.py / test_mcp2210_probe_bus.py) so only
    # _init_device_with_retries itself is under test.
    obj = _RetryProbe.__new__(_RetryProbe)
    obj.logger = logging.getLogger("control")
    obj.device_info = {"device": "TESTDEV"}
    obj.port_map = {}
    obj.primary_port = None
    obj.units = "F"
    obj.fail_until = fail_until
    obj.device_init_retries = retries
    return obj


def test_init_device_with_retries_succeeds_after_failures(caplog):
    obj = _bare_retry_probe(fail_until=3, retries=5)
    with caplog.at_level(logging.INFO, logger="control"):
        obj._init_device_with_retries()
    assert obj.attempts == 3
    assert isinstance(obj.device, FakeDevice)
    assert "initialized on attempt 3" in caplog.text


def test_init_device_with_retries_exhausted_raises_last_exception():
    obj = _bare_retry_probe(fail_until=999, retries=3)
    with pytest.raises(RuntimeError, match="boom-3"):
        obj._init_device_with_retries()
    assert obj.attempts == 3  # gave up after exactly device_init_retries attempts


# ---------------------------------------------------------------------------
# _temp_to_resistance: Steinhart-Hart inverse (temp -> Tr)
# ---------------------------------------------------------------------------


def _obj_for_math(units="F"):
    device_info = _device_info(["P1"], config={"voltage_ref": "3.28", "P1_rd": "10000"})
    probe_info = [_probe("P1", "Probe1", "Primary")]
    return _make_probe(probe_info, device_info, units=units)


def test_temp_to_resistance_fahrenheit():
    obj = _obj_for_math("F")
    # Independently computed from the documented Steinhart-Hart inverse:
    # tempK = ((200-32)*5/9)+273.15; x=(1/2C)(A-1/tempK); y=sqrt((B/3C)^3+x^2);
    # Tr = exp((y-x)^(1/3) - (y+x)^(1/3)) -> 7554.267783149399
    tr = obj._temp_to_resistance(200, _profile())
    assert tr == pytest.approx(7554.267783149399, rel=1e-9)


def test_temp_to_resistance_celsius():
    obj = _obj_for_math("C")
    # Same formula with tempK = 100 + 273.15 -> 6155.053919208363
    tr = obj._temp_to_resistance(100, _profile())
    assert tr == pytest.approx(6155.053919208363, rel=1e-9)


def test_temp_to_resistance_math_error_returns_zero():
    obj = _obj_for_math("F")
    bad_profile = {"A": PROFILE_A, "B": PROFILE_B, "C": 0}  # C=0 -> ZeroDivisionError -> bare except -> Tr=0
    assert obj._temp_to_resistance(200, bad_profile) == 0


# ---------------------------------------------------------------------------
# _voltage_to_temp: Steinhart-Hart forward (voltage -> temp, Tr) + bounds
# ---------------------------------------------------------------------------


def test_voltage_to_temp_transient_probe_returns_none():
    obj = _obj_for_math()
    temp, tr = obj._voltage_to_temp(None, obj.probe_profiles["P1"])
    assert temp is None
    assert tr == 0


def test_voltage_to_temp_normal_range_fahrenheit():
    obj = _obj_for_math("F")
    # voltage=1500mV, Vs=3.28, Rd=10000: Vo=1.5 < Vs so Tr=(Vo*Rd)/(Vs-Vo)=8426.966...
    # -> round 8427; Steinhart-Hart -> tempF=193.74864976188468 (computed independently).
    temp, tr = obj._voltage_to_temp(1500, obj.probe_profiles["P1"], port="P1")
    assert tr == 8427
    assert temp == pytest.approx(193.74864976188468, abs=1e-6)


def test_voltage_to_temp_normal_range_celsius():
    obj = _obj_for_math("C")
    temp, tr = obj._voltage_to_temp(1500, obj.probe_profiles["P1"], port="P1")
    assert tr == 8427
    assert temp == pytest.approx(89.86036097882481, abs=1e-6)


def test_voltage_to_temp_clamps_extreme_high_temp_to_zero():
    # voltage=1mV -> Vo << Vs -> tiny Tr (~3.05 ohms) -> Steinhart-Hart yields
    # tempF~1386.5 (>600) and tempC~752.5 (>314); both bounds fail -> clamped to 0.
    obj_f = _obj_for_math("F")
    temp_f, _ = obj_f._voltage_to_temp(1, obj_f.probe_profiles["P1"], port="P1")
    assert temp_f == 0

    obj_c = _obj_for_math("C")
    temp_c, _ = obj_c._voltage_to_temp(1, obj_c.probe_profiles["P1"], port="P1")
    assert temp_c == 0


def test_voltage_to_temp_clamps_negative_temp_to_zero():
    # voltage=3300mV -> Vo(3.3) > Vs(3.28), still within the 1.01 guard band, so
    # it takes the "else" Tr=(Vo*Rd)/0.001 branch -> huge Tr -> negative temp,
    # which fails the >=0 bound and is clamped to 0.
    obj = _obj_for_math("F")
    temp, _ = obj._voltage_to_temp(3300, obj.probe_profiles["P1"], port="P1")
    assert temp == 0


def test_voltage_to_temp_out_of_range_voltage_logs_and_returns_zero(caplog):
    # voltage=5000mV > Vs*1000*1.01 (3312.8) -> out-of-range branch.
    obj = _obj_for_math("F")
    with caplog.at_level(logging.DEBUG, logger="control"):
        temp, tr = obj._voltage_to_temp(5000, obj.probe_profiles["P1"], port="P1")
    assert temp == 0.0
    assert tr == 0
    assert "outside the expected range" in caplog.text


def test_voltage_to_temp_zero_voltage_is_out_of_range():
    obj = _obj_for_math("F")
    temp, tr = obj._voltage_to_temp(0, obj.probe_profiles["P1"], port="P1")
    assert temp == 0.0
    assert tr == 0


# ---------------------------------------------------------------------------
# read_all_ports
# ---------------------------------------------------------------------------


def test_read_all_ports_populates_all_groups_and_tr():
    device_info = _device_info(["P1", "P2", "P3"], config={"voltage_ref": "3.28"})
    probe_info = [
        _probe("P1", "Primary1", "Primary"),
        _probe("P2", "Food1", "Food"),
        _probe("P3", "Aux1", "Aux"),
    ]
    obj = _make_probe(probe_info, device_info, units="F")
    obj.device = FakeStatusDevice({"P1": 1500, "P2": None, "P3": 5000})

    result = obj.read_all_ports(obj.output_data)

    assert result["primary"]["Primary1"] == pytest.approx(193.74864976188468, abs=1e-6)
    assert result["tr"]["Primary1"] == 8427
    assert result["food"]["Food1"] is None  # transient (voltage None)
    assert result["tr"]["Food1"] == 0
    assert result["aux"]["Aux1"] == 0.0  # out-of-range voltage
    assert result["tr"]["Aux1"] == 0


def test_read_all_ports_skips_unmapped_port_and_applies_time_delay(monkeypatch):
    device_info = _device_info(["P1", "P2"], config={"voltage_ref": "3.28"})
    probe_info = [
        _probe("P1", "Primary1", "Primary"),
        _probe("P2", "Weird1", "Unknown"),  # not Primary/Food/Aux -> no group assignment
    ]
    obj = _make_probe(probe_info, device_info, units="F")
    obj.device = FakeStatusDevice({"P1": 1500, "P2": 1500})
    obj.time_delay = 0.001

    sleep_calls = []
    monkeypatch.setattr("probes.base.time.sleep", lambda s: sleep_calls.append(s))

    result = obj.read_all_ports(obj.output_data)

    assert result["primary"]["Primary1"] == pytest.approx(193.74864976188468, abs=1e-6)
    assert "Weird1" not in result["primary"] and "Weird1" not in result["food"] and "Weird1" not in result["aux"]
    assert sleep_calls == [0.001, 0.001]  # once per port, real sleep mocked out


# ---------------------------------------------------------------------------
# apply_filters
# ---------------------------------------------------------------------------


class _FixedKalman:
    """Stub replacing TempKalman so apply_filters' output/logging is deterministic."""

    def __init__(self, output, x, v, outlier=False, none_streak=0):
        self.output = output
        self.x = x
        self.v = v
        self.outlier = outlier
        self.none_streak = none_streak

    def update(self, raw):
        return self.output


def test_apply_filters_disabled_returns_input_unchanged():
    device_info = _device_info(["P1"])
    probe_info = [_probe("P1", "Primary1", "Primary")]
    obj = _make_probe(probe_info, device_info)
    obj.applies_kalman = False  # virtual/derived-probe mode
    data = {"primary": {"Primary1": 123.4}, "food": {}, "aux": {}}

    result = obj.apply_filters(data)

    assert result is data  # returned as-is, untouched
    assert result["primary"]["Primary1"] == 123.4


def test_apply_filters_updates_output_and_dedups_debug_log(caplog, monkeypatch):
    monkeypatch.setattr(base, "KALMAN_DEBUG", True)
    device_info = _device_info(["P1"])
    probe_info = [_probe("P1", "Primary1", "Primary")]
    obj = _make_probe(probe_info, device_info)
    obj.port_filters["P1"] = _FixedKalman(output=200.5, x=200.5, v=0.1, outlier=False, none_streak=0)
    data = {"primary": {"Primary1": 199.9}, "food": {}, "aux": {}}

    with caplog.at_level(logging.DEBUG, logger="control"):
        obj.apply_filters(data)
        first_count = len(caplog.records)
        data["primary"]["Primary1"] = 199.9  # apply_filters mutates in place; reset raw to match
        obj.apply_filters(data)  # same raw+output -> same message -> should not re-log
        second_count = len(caplog.records)

    assert data["primary"]["Primary1"] == 200.5
    assert first_count == 1
    assert second_count == 1  # deduped: no new record on the second, identical call
    assert "Kalman[Primary1] raw=199.9 output=200.5" in caplog.text


def test_apply_filters_skips_port_with_no_group():
    # A port whose probe "type" is neither Primary/Food/Aux ends up in port_map
    # (via _build_port_map, which only checks device+port) but is never
    # classified by _discover_port_types -- apply_filters must skip it rather
    # than KeyError.
    device_info = _device_info(["P1"])
    probe_info = [_probe("P1", "Weird1", "Unknown")]
    obj = _make_probe(probe_info, device_info)
    data = {"primary": {}, "food": {}, "aux": {}}

    result = obj.apply_filters(data)

    assert result == {"primary": {}, "food": {}, "aux": {}}


def test_apply_filters_assigns_food_and_aux_groups_too():
    device_info = _device_info(["P1", "P2", "P3"], config={"voltage_ref": "3.28"})
    probe_info = [
        _probe("P1", "Primary1", "Primary"),
        _probe("P2", "Food1", "Food"),
        _probe("P3", "Aux1", "Aux"),
    ]
    obj = _make_probe(probe_info, device_info, units="F")
    for port, out in (("P1", 100.0), ("P2", 150.0), ("P3", 200.0)):
        obj.port_filters[port] = _FixedKalman(output=out, x=out, v=0.0)
    data = {"primary": {"Primary1": 99.0}, "food": {"Food1": 149.0}, "aux": {"Aux1": 199.0}}

    result = obj.apply_filters(data)

    assert result["primary"]["Primary1"] == 100.0
    assert result["food"]["Food1"] == 150.0
    assert result["aux"]["Aux1"] == 200.0


def test_apply_filters_skips_label_missing_from_output_data():
    # Defensive guard: a port classified into a group whose label isn't (yet)
    # a key in that group's output_data dict must be skipped, not KeyError.
    device_info = _device_info(["P1"])
    probe_info = [_probe("P1", "Primary1", "Primary")]
    obj = _make_probe(probe_info, device_info)
    data = {"primary": {}, "food": {}, "aux": {}}  # "Primary1" missing from "primary"

    result = obj.apply_filters(data)

    assert result == {"primary": {}, "food": {}, "aux": {}}


def test_apply_filters_skips_logging_when_debug_disabled(caplog):
    device_info = _device_info(["P1"])
    probe_info = [_probe("P1", "Primary1", "Primary")]
    obj = _make_probe(probe_info, device_info)
    obj.port_filters["P1"] = _FixedKalman(output=200.5, x=200.5, v=0.1)
    data = {"primary": {"Primary1": 199.9}, "food": {}, "aux": {}}

    with caplog.at_level(logging.INFO, logger="control"):  # DEBUG disabled
        obj.apply_filters(data)

    assert data["primary"]["Primary1"] == 200.5  # filter still applied
    assert caplog.records == []  # but nothing logged


# ---------------------------------------------------------------------------
# update_units / get_port_map / get_device_info
# ---------------------------------------------------------------------------


def test_update_units_switches_to_celsius_and_reinitializes_device():
    device_info = _device_info(["P1"])
    probe_info = [_probe("P1", "Primary1", "Primary")]
    obj = _make_probe(probe_info, device_info, units="F")
    old_device = obj.device

    obj.update_units("C")

    assert obj.units == "C"
    assert obj.device is not old_device  # _init_device_with_retries ran again


def test_update_units_non_celsius_value_forces_fahrenheit():
    # update_units does `"C" if units == "C" else "F"` -- any non-"C" value
    # (even garbage) collapses to "F", it does not pass the value through.
    device_info = _device_info(["P1"])
    probe_info = [_probe("P1", "Primary1", "Primary")]
    obj = _make_probe(probe_info, device_info, units="F")

    obj.update_units("bogus")

    assert obj.units == "F"


def test_get_port_map_returns_port_map():
    device_info = _device_info(["P1"])
    probe_info = [_probe("P1", "Primary1", "Primary")]
    obj = _make_probe(probe_info, device_info)
    assert obj.get_port_map() == {"P1": "Primary1"}


def test_get_device_info_attaches_status_from_device():
    device_info = _device_info(["P1"])
    probe_info = [_probe("P1", "Primary1", "Primary")]
    obj = _make_probe(probe_info, device_info)
    obj.device = FakeStatusDevice({}, status="running")

    info = obj.get_device_info()

    assert info is obj.device_info
    assert info["status"] == "running"


# ---------------------------------------------------------------------------
# _to_celsius / _to_fahrenheit
# ---------------------------------------------------------------------------


def test_to_celsius_and_to_fahrenheit():
    device_info = _device_info(["P1"])
    probe_info = [_probe("P1", "Primary1", "Primary")]
    obj = _make_probe(probe_info, device_info)

    assert obj._to_celsius(212) == pytest.approx(100.0)
    assert obj._to_celsius(None) is None
    assert obj._to_fahrenheit(100) == pytest.approx(212.0)
    assert obj._to_fahrenheit(0) == pytest.approx(32.0)
    assert obj._to_fahrenheit(None) is None


# ---------------------------------------------------------------------------
# resolve_spi_bus: the one uncovered branch (basic bus, unknown chip-select)
# ---------------------------------------------------------------------------


def test_resolve_spi_bus_basic_unknown_cs_raises(monkeypatch):
    # This branch validates configuration before opening SPI hardware. Keep the
    # unit test independent of whether the host is a supported Blinka board.
    monkeypatch.setitem(sys.modules, "board", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "digitalio", SimpleNamespace())

    with pytest.raises(ValueError, match="Unknown SPI chip-select"):
        resolve_spi_bus({"spi_bus_kind": "basic", "cs": "NOPE"}, default_cs="D6")


# ---------------------------------------------------------------------------
# FakeDevice (base's own no-op default device)
# ---------------------------------------------------------------------------


def test_fake_device_is_a_noop_stub():
    dev = FakeDevice(port_map={}, primary_port=None, units="F")
    assert dev.read_voltage("anything") is None


def test_per_reading_kalman_tracing_stays_off_at_debug_unless_asked_for(caplog, monkeypatch):
    """A live probe changes every tick, so the dedup cannot suppress anything and
    this used to emit a record per probe per tick, burying every other message."""
    monkeypatch.setattr(base, "KALMAN_DEBUG", False)
    obj = _make_probe([_probe("P1", "Primary1", "Primary")], _device_info(["P1"]))
    obj.port_filters["P1"] = _FixedKalman(output=200.5, x=200.5, v=0.1, outlier=False, none_streak=0)
    data = {"primary": {"Primary1": 199.9}, "food": {}, "aux": {}}

    with caplog.at_level(logging.DEBUG, logger="control"):
        obj.apply_filters(data)

    assert data["primary"]["Primary1"] == 200.5  # filtering itself is unaffected
    assert "Kalman[" not in caplog.text
