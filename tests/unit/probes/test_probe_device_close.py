"""ProbesMain must CLOSE the previous probe device instances when it rebuilds
the device list, not merely drop the references and wait for garbage
collection.

Backlog item 9a gap 2: `_setup_probe_devices` rebound `self.probe_device_list`
to a fresh list, so a device holding an OS handle (a bluepy Peripheral and its
helper process, a spidev fd, an smbus2 fd) released it at GC -- or, when a
background thread still referenced the device object, never. The live re-setup
path (`update_probe_map`, driven by POST /api/probe_map) then tried to re-open
the same hardware while the old handle was still held.

These tests pin the two properties that fix needs:
  1. every previous device is closed BEFORE the replacement is constructed, and
  2. one device raising from close() neither stops the other devices from being
     closed nor aborts the rebuild -- the rebuild IS the recovery path.
"""

import sys
import types

from probes.main import ProbesMain

VIRT = {
    "config": {"probes_list": []},
    "device": "VirtDev",
    "module": "virtual_average",
    "module_filename": "virtual_average",
    "ports": ["VIRT0"],
}


def _map(devices, probes=()):
    return {"probe_devices": list(devices), "probe_info": list(probes)}


def _install_recorder_module(monkeypatch, name="_close_recorder", close_error_for=()):
    """Install a fake `probes.<name>` module whose ReadProbes records its
    construction and its close() into one shared, ordered event log. Devices
    whose name is in `close_error_for` raise from close(), to exercise the
    failure-isolation path."""
    events = []
    module = types.ModuleType(f"probes.{name}")

    class ReadProbes:
        def __init__(self, probe_info, device_info, units):
            self.device_info = device_info
            self.name = device_info["device"]
            self.closed = False
            events.append(("open", self.name))

        def close(self):
            if self.name in close_error_for:
                events.append(("close-boom", self.name))
                raise OSError(f"cannot release {self.name}")
            self.closed = True
            events.append(("close", self.name))

    module.ReadProbes = ReadProbes
    monkeypatch.setitem(sys.modules, f"probes.{name}", module)
    return events


def _recorder_device(name, module="_close_recorder"):
    return {
        "config": {},
        "device": name,
        "module": module,
        "module_filename": module,
        "ports": [f"{name}0"],
    }


def test_rebuild_closes_the_previous_devices_before_building_the_new_ones(monkeypatch):
    events = _install_recorder_module(monkeypatch)
    pm = ProbesMain(_map([_recorder_device("A")]), "F")
    first = pm.probe_device_list[0]
    assert first.closed is False

    pm.update_probe_map(_map([_recorder_device("A")]))

    assert first.closed is True
    assert pm.probe_device_list[0] is not first
    # Ordering matters: the old handle must be released before the replacement
    # tries to open the same hardware.
    assert events == [("open", "A"), ("close", "A"), ("open", "A")]


def test_rebuild_closes_every_previous_device(monkeypatch):
    events = _install_recorder_module(monkeypatch)
    pm = ProbesMain(_map([_recorder_device("A"), _recorder_device("B")]), "F")
    old = list(pm.probe_device_list)

    pm.update_probe_map(_map([]))

    assert [device.closed for device in old] == [True, True]
    assert pm.probe_device_list == []
    assert events == [("open", "A"), ("open", "B"), ("close", "A"), ("close", "B")]


def test_a_device_that_raises_on_close_does_not_stop_the_rebuild(monkeypatch):
    events = _install_recorder_module(monkeypatch, close_error_for=("A",))
    pm = ProbesMain(_map([_recorder_device("A"), _recorder_device("B")]), "F")
    old = list(pm.probe_device_list)

    errors = pm.update_probe_map(_map([_recorder_device("C")]))

    # A raised; B was still closed, and the new device was still built.
    assert old[1].closed is True
    assert errors == []
    assert [device.name for device in pm.probe_device_list] == ["C"]
    assert events == [
        ("open", "A"),
        ("open", "B"),
        ("close-boom", "A"),
        ("close", "B"),
        ("open", "C"),
    ]


def test_close_failure_is_logged(monkeypatch, caplog):
    _install_recorder_module(monkeypatch, close_error_for=("A",))
    pm = ProbesMain(_map([_recorder_device("A")]), "F")

    with caplog.at_level("ERROR", logger="control"):
        pm.update_probe_map(_map([]))

    assert any("cannot release A" in record.getMessage() for record in caplog.records)


def test_devices_without_a_teardown_of_their_own_rebuild_cleanly():
    """Most probe modules own nothing releasable (a virtual/derived probe, a
    process-shared I2C or SPI bus). ProbeInterface.close() is a no-op for them,
    so the rebuild must not need any special-casing."""
    pm = ProbesMain(_map([VIRT]), "F")
    first = pm.probe_device_list[0]

    pm.update_probe_map(_map([VIRT]))

    assert pm.probe_device_list[0] is not first
    assert len(pm.probe_device_list) == 1


def test_first_construction_has_nothing_to_close():
    """__init__ calls _setup_probe_devices before probe_device_list exists."""
    pm = ProbesMain(_map([VIRT]), "F")

    assert len(pm.probe_device_list) == 1
