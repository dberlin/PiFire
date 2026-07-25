"""Every distance driver must be safe to read from the control loop.

The control loop refreshes the hopper level on a timer now, so `get_level()`
is called from the thread that is timing the auger and the igniter. The rule
the repo owner set is absolute: that call must never wait for a measurement --
not 3 seconds, not 300ms, not "briefly on the first read".

The loop-side guard lives in
tests/characterization/test_controller_loop_golden.py
(`test_the_control_loop_never_waits_on_a_hopper_reading`). It catches the loop
calling anything that waits. What it CANNOT catch is a driver whose own
`get_level()` quietly becomes blocking again -- the loop has to call that one.
This file is that half of the guard, asserted against every driver at once.
"""

import inspect
import sys
import types
from unittest import mock

import pytest

import distance._sampled_base as sampled_base
import distance.none
import distance.prototype

# Every module in distance/ that defines a HopperLevel the control loop could
# be handed. The threaded ones inherit get_level from SampledHopperLevel; the
# two trivial ones define their own.
THREADED_DRIVERS = ["vl53l0x", "vl53l1x", "vl53l4cd", "sen0628", "hcsr04"]


def _driver_classes():
    """Import every driver, stubbing the Pi-only GPIO library that is not
    installed here. Returns [(name, HopperLevel class)]."""
    fake_sensor = types.SimpleNamespace(Measurement=object)
    fake_pkg = types.ModuleType("hcsr04sensor")
    fake_pkg.sensor = fake_sensor
    found = [
        ("prototype", distance.prototype.HopperLevel),
        ("none", distance.none.HopperLevel),
    ]
    with mock.patch.dict(sys.modules, {"hcsr04sensor": fake_pkg, "hcsr04sensor.sensor": fake_sensor}):
        for name in THREADED_DRIVERS:
            module = __import__(f"distance.{name}", fromlist=["HopperLevel"])
            found.append((name, module.HopperLevel))
    return found


DRIVERS = _driver_classes()


@pytest.mark.parametrize("name,cls", DRIVERS, ids=[n for n, _ in DRIVERS])
def test_get_level_takes_no_arguments(name, cls):
    """`get_level(override=True)` was the blocking path: it asked for a fresh
    reading and then waited on a threading.Event for up to 3 seconds. Removing
    the parameter removes the only way to express "wait" at this call site, on
    every driver, so it cannot come back one driver at a time."""
    params = list(inspect.signature(cls.get_level).parameters)
    assert params == ["self"], f"{name}.get_level grew a parameter: {params}"


@pytest.mark.parametrize("name,cls", DRIVERS, ids=[n for n, _ in DRIVERS])
def test_every_driver_offers_request_sample(name, cls):
    """The non-blocking replacement. It exists on every driver so the control
    loop never has to ask which module it happens to be holding."""
    assert callable(getattr(cls, "request_sample", None)), f"{name} has no request_sample"


@pytest.mark.parametrize("name,cls", DRIVERS, ids=[n for n, _ in DRIVERS])
def test_no_driver_exposes_a_wait_primitive(name, cls):
    """No `threading.Event` (or anything with a `wait`) hanging off a driver
    for a caller to block on."""
    for attr in dir(cls):
        if attr.startswith("__"):
            continue
        value = getattr(cls, attr, None)
        assert not hasattr(value, "wait"), f"{name}.{attr} exposes a wait primitive"


@pytest.mark.parametrize("name,cls", [(n, c) for n, c in DRIVERS if n in THREADED_DRIVERS], ids=THREADED_DRIVERS)
def test_threaded_drivers_share_the_one_cached_read(name, cls):
    """Each sampling driver inherits get_level/request_sample from the shared
    base rather than defining its own -- so the guarantee is proved once."""
    assert cls.get_level is sampled_base.SampledHopperLevel.get_level
    assert cls.request_sample is sampled_base.SampledHopperLevel.request_sample


def test_the_trivial_drivers_are_instant():
    """prototype and none have no sampler and no measurement to take. They are
    what the e2e suite and most dev work run against, so their reads staying
    free is what makes those runs representative."""
    for module in (distance.prototype, distance.none):
        hopper = module.HopperLevel({"distance": {}}, empty=22, full=4)
        assert hopper.get_level() == 100
        assert hopper.request_sample() == ()  # no-op, returns immediately
