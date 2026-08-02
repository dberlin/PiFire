"""A hung distance sensor must not take the controller with it.

Two separate failures, both about a sensor that stops answering rather than
one that answers badly:

* the open runs on control.py's boot path, so an open that never returns used
  to mean the controller never finished starting;
* the stuck-sensor safeguard measured a cycle's duration only AFTER the read
  returned, so the one recovery mechanism the sampler had was exactly the one
  a hang disabled.

There is no ToF sensor on this machine, so nothing here is validated against
hardware. Every hang is a fake transport parked on a `threading.Event` the
test controls: an event that is never set is a hang that cannot resolve early,
which is what makes "stuck" deterministic instead of a race against a real
deadline. The fakes shorten `open_deadline_seconds` and `stuck_cycle_seconds`
onto themselves so the bounded wait the code really performs stays in the
tens of milliseconds.
"""

import contextlib
import sys
import threading
import types
from unittest import mock

import pytest

import time as _real_time

import distance._sampled_base as sampled_base
from distance._sampled_base import SampledHopperLevel, SensorOpenTimeout

# Short enough to keep the suite quick, long enough that a thread that has
# merely not been scheduled yet cannot be mistaken for one that is stuck.
TEST_OPEN_DEADLINE = 0.05


class _FakeClock:
    """Stand-in for `time` as seen by the shared sampling loop. See
    tests/unit/distance/test_tof_base.py's _FakeClock for the full rationale.
    The watchdog does NOT read this: it binds `monotonic` and `sleep` directly
    so its own pacing survives tests that freeze the loop's clock."""

    def __init__(self):
        self._now = 0.0

    def time(self):
        return self._now

    def sleep(self, seconds):
        pass

    def advance(self, seconds):
        self._now += seconds


class _OpenFailed(RuntimeError):
    """What a transport raises when the device is absent. Distinct from
    SensorOpenTimeout so a test can tell "it failed" from "we gave up"."""


def _poll_until(predicate, what, timeout=5.0):
    """Wait for a background thread to reach `predicate`.

    Polls rather than waiting on a primitive the driver exposes, for the same
    reason tests/unit/distance/test_tof_base.py's _await_sample does: the
    driver deliberately offers nothing to block on. The timeout only bounds a
    failure -- when the code is right the predicate holds within milliseconds."""
    deadline = _real_time.monotonic() + timeout
    while not predicate():
        if _real_time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        _real_time.sleep(0.002)


def _stop(hopper):
    hopper.sensor_thread_active = False
    thread = getattr(hopper, "sensor_thread", None)
    if thread is not None:
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# One factory per transport that opens a sensor in __init__, so the bounded
# open is asserted on all three rather than on whichever one is convenient.
# `open_hook` is what runs in place of the real device handshake.
# ---------------------------------------------------------------------------


def _make_tof(stack, open_hook):
    import distance._tof_base as mod

    stack.enter_context(mock.patch.object(mod, "open_i2c_bus", return_value=mock.sentinel.bus))

    class _Hopper(mod.ToFHopperLevel):
        open_deadline_seconds = TEST_OPEN_DEADLINE

        def _open_sensor(self, i2c, address):
            open_hook()

        def _read_distance_mm(self):
            return 100

    return lambda: _Hopper({})


def _make_serial_tof(stack, open_hook):
    import distance._serial_tof_base as mod

    stack.enter_context(mock.patch.object(mod.serial, "Serial", return_value=mock.sentinel.ser))

    class _Hopper(mod.SerialToFHopperLevel):
        open_deadline_seconds = TEST_OPEN_DEADLINE

        def _open_sensor(self, ser):
            open_hook()

        def _read_distance_mm(self):
            return 100

    return lambda: _Hopper({})


def _make_hcsr04(stack, open_hook):
    # hcsr04sensor is a Pi-only GPIO dependency and is not installed here, so
    # the module is stubbed in before distance.hcsr04 is imported.
    fake_sensor = types.SimpleNamespace(Measurement=object)
    fake_pkg = types.ModuleType("hcsr04sensor")
    fake_pkg.sensor = fake_sensor
    stack.enter_context(mock.patch.dict(sys.modules, {"hcsr04sensor": fake_pkg, "hcsr04sensor.sensor": fake_sensor}))
    sys.modules.pop("distance.hcsr04", None)
    import distance.hcsr04 as mod

    class _Hopper(mod.HopperLevel):
        open_deadline_seconds = TEST_OPEN_DEADLINE

        def _restart_sensor(self):
            open_hook()

        def _read_distance_mm(self):
            return 100

    return lambda: _Hopper({"distance": {"trig": 1, "echo": 2}})


TRANSPORTS = [("i2c tof", _make_tof), ("serial tof", _make_serial_tof), ("hcsr04", _make_hcsr04)]
TRANSPORT_IDS = [name for name, _ in TRANSPORTS]


@pytest.fixture
def stack():
    with contextlib.ExitStack() as opened:
        yield opened


@pytest.fixture
def frozen_loop_clock():
    """Freeze the sampling loop's clock, so its 1s pacing sleep costs nothing
    and no test's runtime depends on SENSOR_SAMPLE_INTERVAL."""
    with mock.patch.object(sampled_base, "time", _FakeClock()):
        yield


# ---------------------------------------------------------------------------
# A. The open is bounded, and the failure semantics build_devices() relies on
#    are unchanged.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,factory", TRANSPORTS, ids=TRANSPORT_IDS)
def test_an_open_that_hangs_past_the_deadline_raises_promptly(name, factory, stack):
    """THE BOOT-PATH PROPERTY. build_devices() constructs the distance device
    while control.py is starting, so an unbounded open is a controller that
    never comes up."""
    hangs = threading.Event()
    stack.callback(hangs.set)
    construct = factory(stack, hangs.wait)

    started = _real_time.monotonic()
    with pytest.raises(SensorOpenTimeout):
        construct()
    elapsed = _real_time.monotonic() - started

    # Near the deadline, nowhere near the hang -- which never ends at all.
    assert elapsed < TEST_OPEN_DEADLINE + 1.0, f"{name} took {elapsed:.3f}s to give up on a {TEST_OPEN_DEADLINE}s open"
    assert elapsed >= TEST_OPEN_DEADLINE, f"{name} gave up in {elapsed:.3f}s, before its own deadline"


def test_the_timeout_message_names_the_sensor_and_says_it_was_abandoned(stack):
    hangs = threading.Event()
    stack.callback(hangs.set)
    construct = _make_tof(stack, hangs.wait)

    with pytest.raises(SensorOpenTimeout) as raised:
        construct()

    message = str(raised.value)
    assert "TOF sensor" in message
    assert "left behind" in message


def test_the_abandoned_open_worker_is_never_joined(stack):
    """A thread parked inside a driver call cannot be cancelled, so the
    constructor must walk away from it rather than wait on it."""
    hangs = threading.Event()
    stack.callback(hangs.set)
    running_on = []
    entered = threading.Event()

    def _hang():
        running_on.append(threading.current_thread())
        entered.set()
        hangs.wait()

    construct = _make_tof(stack, _hang)

    with pytest.raises(SensorOpenTimeout):
        construct()

    _poll_until(entered.is_set, "the open worker to start")
    worker = running_on[0]
    assert worker.is_alive(), "the constructor joined the stuck worker instead of abandoning it"
    assert worker.daemon, "the abandoned worker would hold the interpreter open at shutdown"
    assert worker is not threading.current_thread()


@pytest.mark.parametrize("name,factory", TRANSPORTS, ids=TRANSPORT_IDS)
def test_an_open_that_fails_reraises_the_original_exception(name, factory, stack):
    """build_devices() catches whatever the constructor raises, substitutes
    distance.none and records an operator banner. Bounding the open must not
    convert a real failure into a timeout and lose the original cause."""

    def _fail():
        raise _OpenFailed("simulated missing device")

    construct = factory(stack, _fail)

    with pytest.raises(_OpenFailed, match="simulated missing device"):
        construct()


@pytest.mark.parametrize("name,factory", TRANSPORTS, ids=TRANSPORT_IDS)
def test_a_normal_open_constructs_and_starts_sampling(name, factory, stack, frozen_loop_clock):
    opens = []
    hopper = factory(stack, lambda: opens.append(1))()
    try:
        assert opens == [1]
        assert hopper.sensor_thread.is_alive()
        assert hopper.sensor_healthy is True
        _poll_until(lambda: hopper.sample_count >= 1, f"{name} to take its first sample")
        assert hopper.get_level() == 66  # 10.0cm, interpolated between full=4 and empty=22
    finally:
        _stop(hopper)


# ---------------------------------------------------------------------------
# C. The watchdog, as policy. SampledHopperLevel's own __init__ starts no
#    threads, so the decision can be exercised with no concurrency at all.
# ---------------------------------------------------------------------------


def _idle_sampler(**attrs):
    hopper = SampledHopperLevel(empty=22, full=4)
    hopper.sensor_label = "TOF sensor"
    hopper.logger = mock.Mock()
    for key, value in attrs.items():
        setattr(hopper, key, value)
    return hopper


def test_a_cycle_still_in_flight_past_the_threshold_marks_the_sensor_unhealthy():
    hopper = _idle_sampler()
    hopper._cycle_started_at = _real_time.monotonic() - (hopper.stuck_cycle_seconds + 1)

    hopper._check_stuck_cycle()

    assert hopper.sensor_healthy is False


def test_a_cycle_inside_the_threshold_leaves_the_sensor_healthy():
    hopper = _idle_sampler()
    hopper._cycle_started_at = _real_time.monotonic()

    hopper._check_stuck_cycle()

    assert hopper.sensor_healthy is True
    assert hopper.logger.error.call_count == 0


def test_an_idle_sampler_is_never_declared_stuck():
    """Between cycles there is no read to be stuck in, and a sampler that has
    not started yet must not be written off before it reads once."""
    hopper = _idle_sampler()
    assert hopper._cycle_started_at is None

    hopper._check_stuck_cycle()

    assert hopper.sensor_healthy is True


def test_the_stuck_banner_is_logged_once_per_episode_not_once_per_check():
    hopper = _idle_sampler()
    hopper._cycle_started_at = _real_time.monotonic() - (hopper.stuck_cycle_seconds + 1)

    for _check in range(5):
        hopper._check_stuck_cycle()

    assert hopper.logger.error.call_count == 1
    banner = hopper.logger.error.call_args[0][0]
    assert "TOF sensor" in banner


def test_get_level_keeps_serving_the_last_good_reading_while_stuck():
    """The deliberate choice: a stuck sensor serves its last reading, not a
    sentinel. get_level() answers the control loop with an int percentage that
    goes straight into pelletdb, where 0 reads as an empty hopper and 100 as a
    full one. Staleness is reported out of band, via sensor_healthy."""
    hopper = _idle_sampler()
    hopper.distance_read = 63
    hopper._cycle_started_at = _real_time.monotonic() - (hopper.stuck_cycle_seconds + 1)

    hopper._check_stuck_cycle()

    assert hopper.get_level() == 63
    assert hopper.sensor_healthy is False


def test_a_successful_reopen_clears_the_stuck_state_and_rearms_the_banner():
    """The recovery design: the sampling loop re-opens the sensor rather than
    re-reading it, and a re-open that returns puts the loop back to work."""
    reopens = []
    hopper = _idle_sampler()
    hopper._restart_sensor = lambda: reopens.append(1)
    hopper.sensor_healthy = False
    hopper._stuck_reported = True

    hopper._recover_from_stuck_read()

    assert hopper.sensor_healthy is True
    assert reopens == [1]
    # Re-armed: a second stuck episode gets its own banner.
    hopper._cycle_started_at = _real_time.monotonic() - (hopper.stuck_cycle_seconds + 1)
    hopper._check_stuck_cycle()
    assert hopper.logger.error.call_count == 1


def test_a_failing_reopen_leaves_the_sensor_unhealthy_for_the_next_attempt():
    """A sensor that is unplugged rather than wedged raises on re-open. That
    must not resurrect it, and must not kill the sampling thread."""

    def _fail():
        raise _OpenFailed("still gone")

    hopper = _idle_sampler()
    hopper._restart_sensor = _fail
    hopper.sensor_healthy = False
    hopper._stuck_reported = True

    hopper._recover_from_stuck_read()

    assert hopper.sensor_healthy is False
    assert hopper.logger.exception.call_count == 1


# ---------------------------------------------------------------------------
# C. The watchdog, running for real against a read that never returns.
# ---------------------------------------------------------------------------


class _StuckReadHopper:
    """Builds a ToF hopper whose reads park on an event the test owns."""

    def __init__(self, stack):
        import distance._tof_base as mod

        stack.enter_context(mock.patch.object(mod, "open_i2c_bus", return_value=mock.sentinel.bus))
        self.release = threading.Event()
        stack.callback(self.release.set)
        outer = self

        class _Hopper(mod.ToFHopperLevel):
            # The watchdog's own thresholds, shrunk so the test asserts the
            # mechanism rather than waiting out the shipped 30s.
            stuck_cycle_seconds = 0.02
            watchdog_poll_seconds = 0.002

            def _open_sensor(self, i2c, address):
                outer.open_calls += 1

            def _read_distance_mm(self):
                outer.read_calls += 1
                outer.release.wait()
                return 100

        self.open_calls = 0
        self.read_calls = 0
        self.hopper = _Hopper({})


def test_a_read_stuck_past_the_threshold_marks_the_sensor_unhealthy_from_another_thread(stack, frozen_loop_clock):
    """THE PROPERTY THE OLD SAFEGUARD COULD NOT HAVE. The duration check used
    to run after _read_distance_mm() returned, so a read that never returned
    was never measured. This one runs on a thread the stuck read does not
    own."""
    fake = _StuckReadHopper(stack)
    hopper = fake.hopper
    try:
        _poll_until(lambda: not hopper.sensor_healthy, "the watchdog to declare the sensor stuck")

        # And the control loop's read is unaffected: still immediate, still the
        # last value the sampler published.
        assert hopper.get_level() == hopper.distance_read
        assert hopper.sample_count == 0, "no cycle completed, so nothing should have been published"
    finally:
        fake.release.set()
        _stop(hopper)


def test_a_stuck_sensor_logs_its_banner_once_while_the_read_stays_parked(stack, frozen_loop_clock):
    fake = _StuckReadHopper(stack)
    hopper = fake.hopper
    hopper.logger = mock.Mock()
    try:
        _poll_until(lambda: hopper.logger.error.call_count >= 1, "the stuck-sensor banner")
        # The watchdog polls every 2ms here; a per-check banner would be dozens
        # of lines by the time this returns.
        _real_time.sleep(0.05)
        assert hopper.logger.error.call_count == 1
    finally:
        fake.release.set()
        _stop(hopper)


def test_a_stuck_read_that_returns_abandons_its_burst_and_reopens_the_sensor(stack, frozen_loop_clock):
    """THE RECOVERY DESIGN, pinned. The late reading is discarded rather than
    published, the remaining reads of the burst are not issued, and the sensor
    is re-opened before it is trusted again."""
    fake = _StuckReadHopper(stack)
    hopper = fake.hopper
    try:
        _poll_until(lambda: not hopper.sensor_healthy, "the watchdog to declare the sensor stuck")
        assert hopper.samples_per_cycle == 3
        opens_before = fake.open_calls

        fake.release.set()

        _poll_until(lambda: hopper.sensor_healthy, "the sampler to recover once the read returned")
        assert fake.open_calls == opens_before + 1, "recovery re-opens the sensor"
        assert fake.read_calls == 1, "the rest of the burst was issued into a sensor known to be stuck"
        assert hopper.distance_read == 100, "a reading that arrived unplaceably late was published anyway"
    finally:
        fake.release.set()
        _stop(hopper)


def test_a_re_initialization_that_hangs_is_inside_the_watchdogs_view(stack, frozen_loop_clock):
    """The slow-cycle re-init opens the device too, so it is part of the cycle
    the watchdog times. A re-init that parks would otherwise leave the sampler
    wedged with the reads themselves looking perfectly healthy."""
    import distance._tof_base as mod

    stack.enter_context(mock.patch.object(mod, "open_i2c_bus", return_value=mock.sentinel.bus))
    hangs = threading.Event()
    stack.callback(hangs.set)
    opens = []

    class _Hopper(mod.ToFHopperLevel):
        stuck_cycle_seconds = 0.02
        watchdog_poll_seconds = 0.002
        slow_cycle_seconds = -1  # every cycle counts as slow, so the re-init always runs

        def _open_sensor(self, i2c, address):
            pass

        def _restart_sensor(self):
            opens.append(1)
            if len(opens) > 1:  # the construction-time open still has to succeed
                hangs.wait()

        def _read_distance_mm(self):
            return 100

    hopper = _Hopper({})
    try:
        _poll_until(lambda: not hopper.sensor_healthy, "the watchdog to notice a re-init that never returned")
    finally:
        hangs.set()
        _stop(hopper)


# ---------------------------------------------------------------------------
# The healthy path, unchanged.
# ---------------------------------------------------------------------------


def test_a_normal_sample_cycle_is_unaffected(stack, frozen_loop_clock):
    import distance._tof_base as mod

    stack.enter_context(mock.patch.object(mod, "open_i2c_bus", return_value=mock.sentinel.bus))
    reads = []

    class _Hopper(mod.ToFHopperLevel):
        def _open_sensor(self, i2c, address):
            pass

        def _read_distance_mm(self):
            reads.append(1)
            return 50  # 5.0cm

    hopper = _Hopper({}, empty=22, full=4)
    hopper.logger = mock.Mock()
    try:
        _poll_until(lambda: hopper.sample_count >= 1, "the first sample")

        assert hopper.get_level() == 94  # same interpolation as before
        assert hopper.sensor_healthy is True
        assert len(reads) == hopper.samples_per_cycle
        assert hopper._cycle_started_at is None
        assert hopper.logger.error.call_count == 0
    finally:
        _stop(hopper)
