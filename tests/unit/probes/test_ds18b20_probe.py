"""Coverage for probes/ds18b20.py -- the DS18B20 1-wire probe module.

Fakes `w1thermsensor.W1ThermSensor`/`Sensor` so the module imports without
real 1-wire hardware. DS18B20_Device.__init__ unconditionally spawns a
background polling thread (_sensing_loop); to keep tests deterministic and
avoid leaking real threads, `threading.Thread` is replaced (within the probe
module's namespace only) with a no-op stand-in, and _sensing_loop is driven
directly/synchronously where its behavior is under test.

Covers: init_device success/failure, check_availability success/failure,
_sensing_loop's initialized/not-initialized/error paths, the `temperature`
property's update-flag side effect, get_status, and ReadProbes._init_device +
its own read_all_ports override (including the "no reading yet" None branch
and the primary/food/aux group-assignment branches).
"""

import sys
import types
import importlib
import logging

import pytest


def _install_fake_w1thermsensor(monkeypatch):
    fake = types.ModuleType("w1thermsensor")

    class Sensor:
        DS18B20 = "DS18B20"

    class FakeW1ThermSensor:
        def __init__(self):
            self._temperature = 63.5  # a known raw reading in Celsius

        def get_temperature(self):
            return self._temperature

        def get_available_sensors(self, sensor_types):
            return [self]

    fake.W1ThermSensor = FakeW1ThermSensor
    fake.Sensor = Sensor
    monkeypatch.setitem(sys.modules, "w1thermsensor", fake)
    return fake


def _load_probe(monkeypatch):
    _install_fake_w1thermsensor(monkeypatch)
    import probes.ds18b20 as probe

    importlib.reload(probe)  # bind the fake w1thermsensor
    return probe


class _NoOpThread:
    """Replaces threading.Thread (within the probe module's namespace only)
    so DS18B20_Device.__init__ doesn't spawn a real background polling
    thread; tests drive _sensing_loop/temperature_C directly instead."""

    def __init__(self, target=None, **kwargs):
        self._target = target

    def start(self):
        pass  # intentionally never runs the sensing loop


def _patch_no_thread(monkeypatch, probe):
    monkeypatch.setattr(probe, "threading", types.SimpleNamespace(Thread=_NoOpThread))


# ---------------------------------------------------------------------------
# DS18B20_Device.init_device
# ---------------------------------------------------------------------------


def test_init_device_success(monkeypatch):
    probe = _load_probe(monkeypatch)
    _patch_no_thread(monkeypatch, probe)

    device = probe.DS18B20_Device()

    assert device.available is True
    assert device.initialized is True
    assert device.get_status() == {"connected": True, "ready": True}


def test_init_device_failure_sets_unavailable(monkeypatch):
    probe = _load_probe(monkeypatch)
    _patch_no_thread(monkeypatch, probe)

    class BoomW1ThermSensor:
        def __init__(self):
            raise RuntimeError("no 1-wire sensor found")

    monkeypatch.setattr(probe, "W1ThermSensor", BoomW1ThermSensor)

    device = probe.DS18B20_Device()

    assert device.available is False
    assert device.initialized is False
    assert device.get_status() == {"connected": False, "ready": False}


# ---------------------------------------------------------------------------
# check_availability
# ---------------------------------------------------------------------------


def test_check_availability_true_when_sensor_present(monkeypatch):
    probe = _load_probe(monkeypatch)
    _patch_no_thread(monkeypatch, probe)
    device = probe.DS18B20_Device()

    assert device.check_availability() is True


def test_check_availability_false_on_exception(monkeypatch):
    probe = _load_probe(monkeypatch)
    _patch_no_thread(monkeypatch, probe)
    device = probe.DS18B20_Device()

    def boom(sensor_types):
        raise OSError("bus read failure")

    device.sensor.get_available_sensors = boom

    assert device.check_availability() is False


def test_check_availability_reinitializes_when_not_initialized(monkeypatch):
    probe = _load_probe(monkeypatch)
    _patch_no_thread(monkeypatch, probe)
    device = probe.DS18B20_Device()
    device.initialized = False  # force the re-init branch

    assert device.check_availability() is True
    assert device.initialized is True  # init_device() re-ran and succeeded


# ---------------------------------------------------------------------------
# _sensing_loop: driven synchronously (no real background thread)
# ---------------------------------------------------------------------------


def test_sensing_loop_reads_temperature_when_initialized(monkeypatch):
    probe = _load_probe(monkeypatch)
    _patch_no_thread(monkeypatch, probe)
    device = probe.DS18B20_Device()
    device.temperature_C = None
    device.sensor_thread_update = True

    # Stop the loop after exactly one iteration instead of really sleeping.
    monkeypatch.setattr(probe.time, "sleep", lambda s: setattr(device, "sensor_thread_active", False))
    device.sensor_thread_active = True

    device._sensing_loop()

    assert device.temperature_C == 63.5
    assert device.sensor_thread_update is False


def test_sensing_loop_reinitializes_when_not_initialized(monkeypatch):
    probe = _load_probe(monkeypatch)
    _patch_no_thread(monkeypatch, probe)
    device = probe.DS18B20_Device()
    device.initialized = False  # forces the init_device() branch inside the loop
    device.temperature_C = None
    device.sensor_thread_update = True

    monkeypatch.setattr(probe.time, "sleep", lambda s: setattr(device, "sensor_thread_active", False))
    device.sensor_thread_active = True

    device._sensing_loop()

    # The not-initialized branch only reconnects (init_device()); it does not
    # also read a temperature in that same iteration (that's the `else` arm).
    assert device.initialized is True
    assert device.temperature_C is None


def test_sensing_loop_error_clears_temperature_and_availability(monkeypatch):
    probe = _load_probe(monkeypatch)
    _patch_no_thread(monkeypatch, probe)
    device = probe.DS18B20_Device()
    device.temperature_C = 63.5
    device.sensor_thread_update = True

    def boom():
        raise OSError("read failure")

    device.sensor.get_temperature = boom

    monkeypatch.setattr(probe.time, "sleep", lambda s: setattr(device, "sensor_thread_active", False))
    device.sensor_thread_active = True

    device._sensing_loop()

    assert device.temperature_C is None
    assert device.available is False


def test_sensing_loop_skips_read_when_update_flag_clear(monkeypatch):
    probe = _load_probe(monkeypatch)
    _patch_no_thread(monkeypatch, probe)
    device = probe.DS18B20_Device()
    device.temperature_C = "unchanged"
    device.sensor_thread_update = False  # no read requested

    monkeypatch.setattr(probe.time, "sleep", lambda s: setattr(device, "sensor_thread_active", False))
    device.sensor_thread_active = True

    device._sensing_loop()

    assert device.temperature_C == "unchanged"  # untouched: read was skipped


# ---------------------------------------------------------------------------
# temperature property
# ---------------------------------------------------------------------------


def test_temperature_property_returns_value_and_requests_update(monkeypatch):
    probe = _load_probe(monkeypatch)
    _patch_no_thread(monkeypatch, probe)
    device = probe.DS18B20_Device()
    device.temperature_C = 63.5
    device.sensor_thread_update = False

    assert device.temperature == 63.5
    assert device.sensor_thread_update is True  # side effect: flags a refresh


# ---------------------------------------------------------------------------
# ReadProbes._init_device
# ---------------------------------------------------------------------------


def test_readprobes_init_device_wires_ports_and_device(monkeypatch):
    probe = _load_probe(monkeypatch)
    _patch_no_thread(monkeypatch, probe)

    obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
    obj.device_info = {"config": {}}
    obj._init_device()

    assert obj.time_delay == 0
    assert obj.device_info["ports"] == ["DS0"]
    assert isinstance(obj.device, probe.DS18B20_Device)


# ---------------------------------------------------------------------------
# ReadProbes.read_all_ports (module-specific override)
# ---------------------------------------------------------------------------


def _bare_readprobes(probe, port_label, primary_port=None, food_ports=None, aux_ports=None, units="F", tempC=63.5):
    obj = probe.ReadProbes.__new__(probe.ReadProbes)
    obj.logger = logging.getLogger("control")
    obj.device_info = {"ports": ["DS0"]}
    obj.device = types.SimpleNamespace(temperature=tempC)
    obj.port_map = {"DS0": port_label}
    obj.primary_port = primary_port
    obj.food_ports = food_ports or []
    obj.aux_ports = aux_ports or []
    obj.units = units
    obj.output_data = {"primary": {}, "food": {}, "aux": {}, "tr": {port_label: 0}}
    return obj


def test_read_all_ports_primary_fahrenheit(monkeypatch):
    probe = _load_probe(monkeypatch)
    obj = _bare_readprobes(probe, "Probe1", primary_port="DS0", units="F", tempC=63.5)

    result = obj.read_all_ports(obj.output_data)

    assert result["primary"]["Probe1"] == pytest.approx(146.3, abs=0.01)  # 63.5C -> F
    assert result["tr"]["Probe1"] == 0  # resistance not applicable


def test_read_all_ports_food_celsius(monkeypatch):
    probe = _load_probe(monkeypatch)
    obj = _bare_readprobes(probe, "Probe2", food_ports=["DS0"], units="C", tempC=63.5)

    result = obj.read_all_ports(obj.output_data)

    assert result["food"]["Probe2"] == pytest.approx(63.5, abs=0.01)


def test_read_all_ports_aux_celsius(monkeypatch):
    probe = _load_probe(monkeypatch)
    obj = _bare_readprobes(probe, "Probe3", aux_ports=["DS0"], units="C", tempC=63.5)

    result = obj.read_all_ports(obj.output_data)

    assert result["aux"]["Probe3"] == pytest.approx(63.5, abs=0.01)


def test_read_all_ports_none_reading_propagates_as_none(monkeypatch):
    # DS18B20_Device.temperature is None until the sensing thread completes
    # its first read (or if the device is unavailable) -- read_all_ports must
    # pass that through as None rather than crashing on round(None, 1).
    probe = _load_probe(monkeypatch)
    obj = _bare_readprobes(probe, "Probe1", primary_port="DS0", units="F", tempC=None)

    result = obj.read_all_ports(obj.output_data)

    assert result["primary"]["Probe1"] is None
