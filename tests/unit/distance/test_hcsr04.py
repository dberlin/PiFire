"""The ultrasonic hopper sensor, which used to measure on the caller's thread.

`hcsr04sensor` is not installed in this environment (it is a Pi-only GPIO
dependency, pinned in wizard_manifest.json), so the module is stubbed into
sys.modules before distance.hcsr04 is imported -- the same shape the real
library exposes: `sensor.Measurement(trig, echo).raw_distance()` answering in
centimetres.
"""

import sys
import types
from unittest import mock

import pytest

import time as _real_time

import distance._sampled_base as sampled_base


class _FakeClock:
    """Stand-in for `time` as seen by the shared sampling loop. See
    tests/unit/distance/test_tof_base.py's _FakeClock for the full rationale."""

    def __init__(self):
        self._now = 0.0

    def time(self):
        return self._now

    def sleep(self, seconds):
        pass

    def advance(self, seconds):
        self._now += seconds


class _FakeMeasurement:
    """Records construction and reads, and can simulate a slow ping burst by
    advancing the fake clock (rather than really sleeping)."""

    instances = []

    def __init__(self, trig, echo):
        self.trig = trig
        self.echo = echo
        self.reads = 0
        _FakeMeasurement.instances.append(self)

    def raw_distance(self):
        self.reads += 1
        if _FakeMeasurement.read_delay:
            sampled_base.time.advance(_FakeMeasurement.read_delay)
        return _FakeMeasurement.reading_cm

    read_delay = 0.0
    reading_cm = 10.0


@pytest.fixture
def hcsr04_mod():
    _FakeMeasurement.instances = []
    _FakeMeasurement.read_delay = 0.0
    _FakeMeasurement.reading_cm = 10.0

    fake_sensor = types.SimpleNamespace(Measurement=_FakeMeasurement)
    fake_pkg = types.ModuleType("hcsr04sensor")
    fake_pkg.sensor = fake_sensor
    with mock.patch.dict(sys.modules, {"hcsr04sensor": fake_pkg, "hcsr04sensor.sensor": fake_sensor}):
        sys.modules.pop("distance.hcsr04", None)
        import distance.hcsr04 as mod

        with mock.patch.object(sampled_base, "time", _FakeClock()):
            yield mod
    sys.modules.pop("distance.hcsr04", None)


DEV_PINS = {"distance": {"trig": 23, "echo": 27}}


def _make_hopper(mod, dev_pins=None, empty=22, full=4):
    return mod.HopperLevel(dev_pins or DEV_PINS, empty=empty, full=full)


def _stop(hopper):
    hopper.sensor_thread_active = False
    hopper.sensor_thread.join(timeout=2)


def _await_sample(hopper, count=1, timeout=2.0):
    """Wait until the sampling thread has completed `count` samples.

    Polls `sample_count` rather than waiting on the driver, because the driver
    deliberately offers NO way to wait for a measurement -- that blocking
    primitive was removed so the control loop cannot reacquire it. Tests are
    allowed to wait; the control loop is not."""
    deadline = _real_time.monotonic() + timeout
    while hopper.sample_count < count:
        if _real_time.monotonic() > deadline:
            raise AssertionError(f"sampling thread produced {hopper.sample_count} samples, wanted {count}")
        _real_time.sleep(0.005)
    return hopper.get_level()


def test_measures_on_a_background_thread_not_the_callers(hcsr04_mod):
    """THE POINT OF THIS FILE. get_level() used to call raw_distance()
    synchronously on whatever thread asked -- which, now that the control loop
    refreshes the hopper on a timer, would be the loop timing the auger."""
    hopper = _make_hopper(hcsr04_mod)
    try:
        _await_sample(hopper)  # let the thread take its first sample
        reads_before = _FakeMeasurement.instances[0].reads
        assert reads_before > 0  # the thread, not the caller, did the measuring
        for _ in range(5):
            hopper.get_level()
        assert _FakeMeasurement.instances[0].reads == reads_before  # cached; no new pings
    finally:
        _stop(hopper)


def test_reads_the_configured_trig_and_echo_pins(hcsr04_mod):
    hopper = _make_hopper(hcsr04_mod)
    try:
        assert (_FakeMeasurement.instances[0].trig, _FakeMeasurement.instances[0].echo) == (23, 27)
    finally:
        _stop(hopper)


def test_one_raw_distance_call_per_sample_cycle(hcsr04_mod):
    """raw_distance() already averages 11 pings internally, so the base's
    3-reading average would triple a ~1.1s call for nothing."""
    hopper = _make_hopper(hcsr04_mod)
    try:
        _await_sample(hopper)
        assert hopper.samples_per_cycle == 1
        assert _FakeMeasurement.instances[0].reads == 1
    finally:
        _stop(hopper)


def test_a_normal_slow_ultrasonic_read_does_not_trigger_reinit(hcsr04_mod):
    """A healthy raw_distance() is ~1.1s of the library's own inter-ping
    sleeps. Under the ToF sensors' 0.5s threshold that would re-initialize the
    sensor on every single reading."""
    _FakeMeasurement.read_delay = 1.1
    hopper = _make_hopper(hcsr04_mod)
    try:
        _await_sample(hopper)
        assert len(_FakeMeasurement.instances) == 1  # constructed once, never re-inited
    finally:
        _stop(hopper)


def test_a_genuinely_stuck_sensor_still_reinitializes(hcsr04_mod):
    _FakeMeasurement.read_delay = 3.0  # > slow_cycle_seconds
    hopper = _make_hopper(hcsr04_mod)
    try:
        _await_sample(hopper)
        assert len(_FakeMeasurement.instances) == 2
    finally:
        _stop(hopper)


def test_reading_at_or_below_full_is_100_percent(hcsr04_mod):
    _FakeMeasurement.reading_cm = 4.0  # == full
    hopper = _make_hopper(hcsr04_mod)
    try:
        assert _await_sample(hopper) == 100
    finally:
        _stop(hopper)


def test_reading_at_empty_is_0_percent(hcsr04_mod):
    _FakeMeasurement.reading_cm = 22.0  # == empty
    hopper = _make_hopper(hcsr04_mod)
    try:
        assert _await_sample(hopper) == 0
    finally:
        _stop(hopper)


def test_reading_between_full_and_empty_is_interpolated(hcsr04_mod):
    # Same arithmetic the ToF base is pinned against at 5.0cm.
    _FakeMeasurement.reading_cm = 5.0
    hopper = _make_hopper(hcsr04_mod)
    try:
        assert _await_sample(hopper) == 94
    finally:
        _stop(hopper)


def test_invalid_empty_full_forces_defaults(hcsr04_mod):
    hopper = _make_hopper(hcsr04_mod, empty=4, full=22)
    try:
        assert (hopper.empty, hopper.full) == (22, 4)
    finally:
        _stop(hopper)


def test_sampling_interval_is_the_shared_constant(hcsr04_mod):
    from distance.intervals import SENSOR_SAMPLE_INTERVAL

    hopper = _make_hopper(hcsr04_mod)
    try:
        assert hopper.sensor_thread_read_interval == SENSOR_SAMPLE_INTERVAL
    finally:
        _stop(hopper)
