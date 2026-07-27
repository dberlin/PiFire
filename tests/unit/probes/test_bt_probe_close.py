"""Teardown coverage for the three Bluetooth probe modules (bt_ibbq,
bt_meater, bt_meater_exp).

These are the modules backlog item 9a gap 2 is really about. Each one owns a
live BLE connection AND two non-daemon threads whose loops hold a reference
back to the device object, so dropping the device on a probe-map rebuild
released nothing at all -- garbage collection could never run on an object a
running thread still references, and the setup thread would happily reconnect
to the same probe while the replacement device was trying to connect to it.

Constructing these devices for real would scan for and connect to Bluetooth
hardware, so the tests build a bare instance (__new__) with fake threads and a
fake peripheral, exactly as the other probe-module tests bypass the heavy
__init__.

Two properties per module:
  1. close() stops both threads and drops the BLE connection, and
  2. both loops actually honor the closing flag, so close() is not just
     setting a value nothing reads.
"""

import importlib
import logging
import sys
import threading
import types

import pytest


class _FakeThread:
    """Stands in for the module's background threads: records join timeouts
    and reports itself as finished."""

    def __init__(self):
        self.joins = []

    def join(self, timeout=None):
        self.joins.append(timeout)

    def is_alive(self):
        return False


class _FakeThreadStillRunning(_FakeThread):
    def is_alive(self):
        return True


class _FakePeripheral:
    def __init__(self, error=None):
        self.disconnects = 0
        self.error = error

    def disconnect(self):
        self.disconnects += 1
        if self.error is not None:
            raise self.error


def _bare(cls):
    """Build a device instance without running __init__ (which would scan for
    real Bluetooth hardware), wired with the attributes close() touches."""
    device = cls.__new__(cls)
    device.logger = logging.getLogger("control")
    device._closing = threading.Event()
    device.sensor_thread_active = True
    device.device_setup = True
    device.sensor_thread = _FakeThread()
    device.device_thread = _FakeThread()
    return device


# ===========================================================================
# probes/bt_ibbq.py
# ===========================================================================


@pytest.fixture
def ibbq():
    import probes.bt_ibbq as probe

    return probe


def test_ibbq_close_stops_the_threads_and_disconnects(ibbq):
    device = _bare(ibbq.iBBQ_Device)
    peripheral = _FakePeripheral()
    device.ibbq_device = peripheral

    device.close()

    assert device._closing.is_set()
    assert device.sensor_thread_active is False
    assert device.sensor_thread.joins and device.device_thread.joins
    assert peripheral.disconnects == 1
    assert device.ibbq_device is None
    assert device.device_setup is False


def test_ibbq_close_without_a_connection_is_harmless(ibbq):
    device = _bare(ibbq.iBBQ_Device)

    device.close()  # never connected: no ibbq_device attribute at all

    assert device._closing.is_set()


def test_ibbq_close_warns_when_a_thread_outlives_the_join(ibbq, caplog):
    device = _bare(ibbq.iBBQ_Device)
    device.device_thread = _FakeThreadStillRunning()

    with caplog.at_level(logging.WARNING, logger="control"):
        device.close()

    assert "device_thread" in caplog.text


def test_ibbq_loops_exit_when_closing(ibbq):
    """Both loops must return promptly once the flag is set -- with it already
    set they must not touch Bluetooth at all, which is what lets these run in
    a test at all."""
    device = _bare(ibbq.iBBQ_Device)
    device._closing.set()

    device._setup_device()
    device._sensing_loop()


# ===========================================================================
# probes/bt_meater.py
# ===========================================================================


@pytest.fixture
def meater():
    import probes.bt_meater as probe

    return probe


def test_meater_close_stops_the_threads_and_disconnects(meater):
    device = _bare(meater.Meater_Device)
    peripheral = _FakePeripheral()
    device.device = peripheral
    device.meater = object()

    device.close()

    assert device._closing.is_set()
    assert device.sensor_thread_active is False
    assert peripheral.disconnects == 1
    assert device.device is None
    assert device.meater is None
    assert device.device_setup is False


def test_meater_loops_exit_when_closing(meater):
    device = _bare(meater.Meater_Device)
    device._closing.set()
    device.hardware_id = None

    device._setup_device()
    device._sensing_loop()


# ===========================================================================
# probes/bt_meater_exp.py  (simplepyble is not installed; fake it)
# ===========================================================================


@pytest.fixture
def meater_exp(monkeypatch):
    fake = types.ModuleType("simplepyble")

    class Adapter:
        @staticmethod
        def get_adapters():
            return []

    fake.Adapter = Adapter
    monkeypatch.setitem(sys.modules, "simplepyble", fake)
    import probes.bt_meater_exp as probe

    importlib.reload(probe)  # bind the fake simplepyble
    return probe


def test_meater_exp_close_stops_the_threads_and_disconnects(meater_exp):
    device = _bare(meater_exp.Meater_Device)
    peripheral = _FakePeripheral()
    device.probeHandler = types.SimpleNamespace(peripheral=peripheral)
    device.probe = object()

    device.close()

    assert device._closing.is_set()
    assert device.sensor_thread_active is False
    assert peripheral.disconnects == 1
    assert device.probe is None
    assert device.device_setup is False


def test_meater_exp_close_before_the_setup_thread_ran_is_harmless(meater_exp):
    """probeHandler is created inside _setup_device, not __init__, so a device
    closed before that thread got going has no handler at all."""
    device = _bare(meater_exp.Meater_Device)

    device.close()

    assert device._closing.is_set()


def test_meater_exp_loops_exit_when_closing(meater_exp):
    device = _bare(meater_exp.Meater_Device)
    device._closing.set()
    device.address = None

    device._setup_device()
    device._sensing_loop()
