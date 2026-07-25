from unittest import mock

import pytest

import time as _real_time

import distance._sampled_base as sampled_base


class _FakeClock:
    """Deterministic stand-in for the `time` module as seen by
    distance._sampled_base -- the sampling loop is shared by every transport
    and lives there, so that is the module the clock is patched into.
    `_sensing_loop` measures a read cycle's duration via time.time() to decide
    whether the sensor looks stuck and needs re-initializing; a slow fake read
    advances this clock via `advance()` instead of the real one, so that
    timing-dependent behavior can be exercised without the test process
    actually blocking on a real sleep.

    `sleep()` (the loop's once-per-iteration idle pacing wait) is a genuine
    no-op that deliberately does *not* advance the clock: if it did, the
    background thread -- no longer throttled by a real sleep -- would spin
    fast enough to blow past the read-interval threshold
    (intervals.SENSOR_SAMPLE_INTERVAL) many times over during a single test,
    re-triggering reads/re-inits well beyond what the test intends to
    exercise."""

    def __init__(self):
        self._now = 0.0

    def time(self):
        return self._now

    def sleep(self, seconds):
        pass

    def advance(self, seconds):
        """Test-only: simulate a read that took `seconds` to complete."""
        self._now += seconds


class FakeSensorMixin:
    """Mixed in ahead of ToFHopperLevel in test subclasses so the shared
    thread/percent-calc/bus-resolution logic can be exercised without a real
    sensor. `reading_mm` is the fixed distance every read returns; `read_delay`
    simulates a slow sensor to exercise the stuck-sensor re-init path by
    advancing the fake clock installed by the `tof_mod` fixture."""

    def __init__(self, *args, reading_mm=100, read_delay=0, **kwargs):
        self.open_calls = 0
        self.opened_with = None
        self._reading_mm = reading_mm
        self._read_delay = read_delay
        super().__init__(*args, **kwargs)

    def _open_sensor(self, i2c, address):
        self.open_calls += 1
        self.opened_with = (i2c, address)

    def _read_distance_mm(self):
        if self._read_delay:
            sampled_base.time.advance(self._read_delay)
        return self._reading_mm


@pytest.fixture
def tof_mod():
    import distance._tof_base as mod

    with (
        mock.patch.object(mod, "open_i2c_bus", return_value=mock.sentinel.bus),
        mock.patch.object(sampled_base, "time", _FakeClock()),
    ):
        yield mod


def _make_hopper(tof_mod, dev_pins=None, reading_mm=100, empty=22, full=4, read_delay=0):
    class TestHopperLevel(FakeSensorMixin, tof_mod.ToFHopperLevel):
        _tof_mod = tof_mod

    return TestHopperLevel(dev_pins or {}, empty=empty, full=full, reading_mm=reading_mm, read_delay=read_delay)


def test_open_i2c_bus_delegates_to_factory(tof_mod):
    hopper = _make_hopper(tof_mod, dev_pins={"distance": {"i2c_bus_kind": "ft232h", "i2c_bus_num": "1"}})
    try:
        assert hopper.opened_with[0] is mock.sentinel.bus
        tof_mod.open_i2c_bus.assert_called_with("ft232h", "1")
    finally:
        _stop(hopper)


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


def test_invalid_empty_full_forces_defaults(tof_mod):
    hopper = _make_hopper(tof_mod, empty=4, full=22)
    try:
        assert hopper.empty == 22
        assert hopper.full == 4
    finally:
        _stop(hopper)


def test_reading_at_or_below_full_is_100_percent(tof_mod):
    hopper = _make_hopper(tof_mod, reading_mm=40, empty=22, full=4)  # 4.0cm == full
    try:
        assert _await_sample(hopper) == 100
    finally:
        _stop(hopper)


def test_reading_at_empty_is_0_percent(tof_mod):
    hopper = _make_hopper(tof_mod, reading_mm=220, empty=22, full=4)  # 22.0cm == empty
    try:
        assert _await_sample(hopper) == 0
    finally:
        _stop(hopper)


def test_reading_above_empty_is_0_percent(tof_mod):
    hopper = _make_hopper(tof_mod, reading_mm=300, empty=22, full=4)  # 30.0cm > empty
    try:
        assert _await_sample(hopper) == 0
    finally:
        _stop(hopper)


def test_reading_between_full_and_empty_is_interpolated(tof_mod):
    hopper = _make_hopper(tof_mod, reading_mm=50, empty=22, full=4)  # 5.0cm
    try:
        assert _await_sample(hopper) == 94
    finally:
        _stop(hopper)


def test_slow_read_cycle_reinitializes_sensor(tof_mod):
    hopper = _make_hopper(tof_mod, reading_mm=100, read_delay=0.2)  # 3 * 0.2s > 0.5s threshold
    try:
        _await_sample(hopper)
        assert hopper.open_calls == 2
    finally:
        _stop(hopper)


def test_address_defaults_to_chip_default(tof_mod):
    hopper = _make_hopper(tof_mod, dev_pins={})
    try:
        assert hopper.opened_with[1] == 0x29
    finally:
        _stop(hopper)


def test_address_override_parses_hex_string(tof_mod):
    hopper = _make_hopper(tof_mod, dev_pins={"distance": {"address": "0x2a"}})
    try:
        assert hopper.opened_with[1] == 0x2A
    finally:
        _stop(hopper)


def test_sampling_interval_comes_from_the_shared_constant(tof_mod):
    # This used to be a hard-coded 60, which made the cached level -- the only
    # thing the control loop's automatic refresh can read for free -- up to a
    # minute stale. It is now the one tuned constant every transport shares.
    from distance.intervals import SENSOR_SAMPLE_INTERVAL

    hopper = _make_hopper(tof_mod)
    try:
        assert hopper.sensor_thread_read_interval == SENSOR_SAMPLE_INTERVAL
    finally:
        _stop(hopper)


def test_get_level_takes_no_arguments_and_cannot_block(tof_mod):
    """THE SAFETY PROPERTY, asserted structurally.

    `get_level` used to accept `override=True`, which set the request flag and
    then waited on a `threading.Event` for up to 3 seconds. That parameter is
    gone, and with it the only way for a caller to wait on a measurement. This
    pins the SIGNATURE, so the blocking path cannot be reintroduced under the
    same name by a future change that "just adds the argument back"."""
    import inspect

    params = inspect.signature(sampled_base.SampledHopperLevel.get_level).parameters
    assert list(params) == ["self"], f"get_level grew a parameter: {list(params)}"
    assert not hasattr(sampled_base.SampledHopperLevel, "event")

    hopper = _make_hopper(tof_mod)
    try:
        _await_sample(hopper)
        assert hopper.get_level() == hopper.distance_read
        # And the read is pure: it does not secretly queue a measurement either.
        hopper.sample_requested = False
        hopper.get_level()
        assert hopper.sample_requested is False
    finally:
        _stop(hopper)


def test_a_request_arriving_mid_cycle_is_not_swallowed(tof_mod):
    """The flag is cleared BEFORE the reading, not after.

    Clearing it afterwards loses any request that arrived while the cycle was
    in flight -- and since a cycle takes ~1s on the ultrasonic sensor, that is
    a perfectly ordinary thing for a `hopper_check` to do. The request would
    vanish with nothing to show for it."""
    hopper = _make_hopper(tof_mod)
    try:
        _await_sample(hopper)
        # Simulate the arrival landing inside a cycle: set the flag, then let
        # the loop run. It must produce a sample and leave the flag cleared,
        # never the other way round.
        before = hopper.sample_count
        hopper.request_sample()
        _await_sample(hopper, before + 1)
        assert hopper.sample_requested is False
    finally:
        _stop(hopper)


def test_request_sample_asks_but_does_not_wait(tof_mod):
    """The replacement for `override=True`: it sets the flag the sampling
    thread is already watching, and returns. The reading lands in the cache
    shortly afterwards, and reaches the datastore on the control loop's next
    timed refresh."""
    hopper = _make_hopper(tof_mod, reading_mm=50, empty=22, full=4)
    try:
        _await_sample(hopper)
        before = hopper.sample_count
        started = _real_time.monotonic()
        hopper.request_sample()
        assert _real_time.monotonic() - started < 0.1  # returned immediately
        assert hopper.sample_requested is True
        _await_sample(hopper, before + 1)  # ... and the thread does honour it
        assert hopper.get_level() == 94
    finally:
        _stop(hopper)
