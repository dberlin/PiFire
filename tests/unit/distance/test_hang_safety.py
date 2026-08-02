"""A hung distance sensor must not take the controller with it.

Three separate failures, all about a sensor that stops answering rather than
one that answers badly:

* the open runs on control.py's boot path, so an open that never returns used
  to mean the controller never finished starting;
* the stuck-sensor safeguard measured a cycle's duration only AFTER the read
  returned, so the one recovery mechanism the sampler had was exactly the one
  a hang disabled;
* the reads themselves polled the sensor's data-ready register with no
  deadline. That register lives on an I2C bus common/i2c_bus.py hands to every
  device naming the same hardware, so a ToF sensor that stopped answering took
  and released that bus's lock about a thousand times a second for as long as
  the controller ran, and the probes and the grill platform starved behind it.
  The read never returned and never raised, which is why bounding it had to
  come with somewhere for the failure to go: a guard around the cycle, a burst
  that stops on the first failure, and a widening wait between attempts.

There is no ToF sensor on this machine, so nothing here is validated against
hardware. Every hang is a fake transport parked on a `threading.Event` the
test controls: an event that is never set is a hang that cannot resolve early,
which is what makes "stuck" deterministic instead of a race against a real
deadline. The fakes shorten `open_deadline_seconds`, `stuck_cycle_seconds` and
`read_deadline_seconds` onto themselves so the bounded wait the code really
performs stays in the tens of milliseconds, and the backoff tests drive the
sampling loop's clock by hand so no test waits out a real one.
"""

import contextlib
import importlib
import sys
import threading
import types
from unittest import mock

import pytest

import time as _real_time

import distance._sampled_base as sampled_base
from distance._sampled_base import SampledHopperLevel, SensorOpenTimeout
from distance._tof_base import ToFReadTimeout

# Short enough to keep the suite quick, long enough that a thread that has
# merely not been scheduled yet cannot be mistaken for one that is stuck.
TEST_OPEN_DEADLINE = 0.05

# The shipped read deadline and poll interval are 0.5s and 0.01s. Shrunk by the
# same factor of ten so the ratio between them -- and therefore the number of
# data_ready reads a failing sensor gets -- is the shipped one.
TEST_READ_DEADLINE = 0.05
TEST_DATA_READY_POLL = 0.001

# A poll of at least TEST_DATA_READY_POLL cannot fit more than this many checks
# into TEST_READ_DEADLINE, whatever else the machine is doing: time.sleep()
# undershoots nothing, so load can only push the count DOWN. Slack of two for
# the check that opens the wait and the one that trips the deadline.
MAX_DATA_READY_READS = TEST_READ_DEADLINE / TEST_DATA_READY_POLL + 2


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


class _TickingClock(_FakeClock):
    """`_FakeClock` for tests that need the loop to keep cycling.

    Its `sleep()` advances instead of standing still, so the loop's own pacing
    wait carries it past SENSOR_SAMPLE_INTERVAL and on to the next cycle -- in
    no real time at all. Not the default: a clock that moves would also expire
    a backoff, and the backoff tests need one that cannot."""

    def sleep(self, seconds):
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


@pytest.fixture
def ticking_loop_clock():
    """Run the sampling loop's clock forward on its own pacing sleep, so a test
    can watch several cycles without spending several real SENSOR_SAMPLE_INTERVALs."""
    with mock.patch.object(sampled_base, "time", _TickingClock()):
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
# D. The ToF read itself is bounded. This is the defect the owner reported: not
#    a read that blocks, but one that spins on the shared bus forever.
# ---------------------------------------------------------------------------


class _NeverReadyToF:
    """A ToF part that never reports data ready.

    `release()` makes it answer, so a worker that a FAILING test abandoned
    finishes instead of spinning for the rest of the session. Counting reads of
    `data_ready` is the whole point: it is a register read on the shared bus,
    so the count IS the bus traffic a failing sensor generates."""

    def __init__(self):
        self.data_ready_reads = 0
        self.distance = 10.0
        self._released = threading.Event()

    @property
    def data_ready(self):
        self.data_ready_reads += 1
        return self._released.is_set()

    def clear_interrupt(self):
        pass

    def release(self):
        self._released.set()


def _bounded_reader(vl_mod, tof):
    """The real driver's `_read_distance_mm`, wired to `tof`, with the deadline
    shrunk.

    No bus, no open and no sampling thread: the read is what is under test, and
    calling it directly keeps the loop's behaviour out of the result."""

    class _Hopper(vl_mod.HopperLevel):
        read_deadline_seconds = TEST_READ_DEADLINE
        data_ready_poll_seconds = TEST_DATA_READY_POLL

        def __init__(self):
            pass

    hopper = _Hopper()
    hopper.tof = tof
    return hopper


def _read_on_a_worker(hopper, timeout):
    """Take one reading on a daemon thread and report what became of it.

    A worker rather than an inline call because the defect under test is a read
    that never returns: inline, an unbounded poll would hang the whole suite
    instead of failing one assertion."""
    outcome = types.SimpleNamespace(finished=False, error=None, value=None, elapsed=None)
    done = threading.Event()

    def _run():
        started = _real_time.monotonic()
        try:
            outcome.value = hopper._read_distance_mm()
        except BaseException as error:
            outcome.error = error
        finally:
            outcome.elapsed = _real_time.monotonic() - started
            done.set()

    threading.Thread(target=_run, name="bounded read", daemon=True).start()
    outcome.finished = done.wait(timeout)
    return outcome


@pytest.mark.parametrize("module_name", ["distance.vl53l1x", "distance.vl53l4cd"])
def test_a_data_ready_that_never_goes_true_fails_the_read_instead_of_polling_forever(module_name, stack):
    """THE BUS-STARVATION PROPERTY, and the one the whole change is about.

    `data_ready` is an I2C register read. Unbounded, the poll took the shared
    bus's lock about a thousand times a second for the life of the process, so
    the number of reads -- not merely the fact that the call returns -- is what
    has to be bounded."""
    vl_mod = importlib.import_module(module_name)
    tof = _NeverReadyToF()
    stack.callback(tof.release)
    hopper = _bounded_reader(vl_mod, tof)

    outcome = _read_on_a_worker(hopper, timeout=2.0)

    assert tof.data_ready_reads <= MAX_DATA_READY_READS, (
        f"{module_name} read data_ready {tof.data_ready_reads} times against a "
        f"{TEST_READ_DEADLINE}s deadline polled every {TEST_DATA_READY_POLL}s"
    )
    assert outcome.finished, f"{module_name} never gave up on a sensor that never reports data ready"
    assert isinstance(outcome.error, ToFReadTimeout), f"the read ended with {outcome.error!r}"
    assert outcome.elapsed >= TEST_READ_DEADLINE, "gave up before its own deadline"
    assert outcome.elapsed < TEST_READ_DEADLINE + 1.0, "took far longer than the deadline to give up"


@pytest.mark.parametrize("module_name", ["distance.vl53l1x", "distance.vl53l4cd"])
def test_a_sensor_that_is_ready_is_read_without_extra_bus_traffic(module_name, stack):
    """The healthy read, unchanged: one data_ready check, one distance."""
    vl_mod = importlib.import_module(module_name)
    tof = _NeverReadyToF()
    tof.release()
    hopper = _bounded_reader(vl_mod, tof)

    assert hopper._read_distance_mm() == 100.0  # 10.0cm
    assert tof.data_ready_reads == 1


# ---------------------------------------------------------------------------
# E. A read that fails must not end the sampling thread, must not drag the rest
#    of its burst onto the bus, and must not be retried at the loop's tick.
# ---------------------------------------------------------------------------


class _ReadFailed(RuntimeError):
    """What a bounded read raises once the sensor stops answering."""


class _FailingReadHopper:
    """A ToF hopper whose reads raise until the test clears `fail`.

    Records reads and opens separately, because "no bus traffic during a
    backoff" is a claim about both."""

    def __init__(self, stack, backoff_base=None, backoff_cap=None, fail=True):
        import distance._tof_base as mod

        stack.enter_context(mock.patch.object(mod, "open_i2c_bus", return_value=mock.sentinel.bus))
        outer = self
        self.reads = 0
        self.opens = 0
        # Settled before construction: the loop starts inside it and takes its
        # first cycle at once, so a test that flipped this afterwards would be
        # racing a thread that has already read.
        self.fail = fail
        self.logger = mock.Mock()

        class _Hopper(mod.ToFHopperLevel):
            # The slow-cycle re-init is a separate mechanism with its own
            # tests. Held off here so the log counts below are about the
            # backoff alone.
            slow_cycle_seconds = 1e9

            def _open_sensor(self, i2c, address):
                outer.opens += 1

            def _read_distance_mm(self):
                outer.reads += 1
                if outer.fail:
                    raise _ReadFailed("simulated bus failure")
                return 50  # 5.0cm

            def _start_sampling(self):
                # The logger is swapped in HERE rather than after construction:
                # _start_sampling is the last thing __init__ does, so this is
                # the last moment before the loop can log anything.
                self.logger = outer.logger
                super()._start_sampling()

        if backoff_base is not None:
            _Hopper.backoff_base_seconds = backoff_base
        if backoff_cap is not None:
            _Hopper.backoff_cap_seconds = backoff_cap

        self.hopper = _Hopper({})


def test_the_loop_survives_a_read_that_raises_and_keeps_cycling(stack, ticking_loop_clock):
    """THE GUARD. Bounding the reads makes them raise, and _sensing_loop had no
    try/except: the thread simply ended. sensor_healthy stayed True, no banner
    was ever logged, and get_level() served the same stale number for the rest
    of the run -- a frozen hopper reading with nothing in the log."""
    # Backoff neutralised to zero, so this test is about the guard and only the
    # guard. The wait itself is asserted below.
    fake = _FailingReadHopper(stack, backoff_base=0, backoff_cap=0)
    hopper = fake.hopper
    try:
        _poll_until(
            lambda: hopper.sample_count >= 3,
            "cycles to keep coming after a read raised (the guard is missing, so the thread died)",
            timeout=2.0,
        )
        assert hopper.sensor_thread.is_alive()
        assert fake.reads >= 3
    finally:
        _stop(hopper)


def test_a_failed_read_abandons_the_rest_of_the_burst(stack, frozen_loop_clock):
    """Three reads run back to back. The second and third would be aimed at a
    device that has just failed, and each one costs the shared bus another read
    deadline."""
    fake = _FailingReadHopper(stack)
    hopper = fake.hopper
    try:
        _poll_until(lambda: hopper.sample_count >= 1, "the first, failing cycle")

        assert hopper.samples_per_cycle == 3
        # The loop's clock is frozen, so the backoff started by that cycle
        # never expires and this count cannot drift.
        assert fake.reads == 1, f"the burst issued {fake.reads} reads at a device that failed on the first"
    finally:
        _stop(hopper)


# Long enough that the sampling loop cannot tick its way to the end of one
# inside a test, which is what makes "during the backoff" a settled state
# rather than a race. The schedule's real values are asserted separately.
UNENDING_BACKOFF = 10**12


def test_a_backoff_sends_nothing_at_all_to_the_device(stack, ticking_loop_clock):
    """The loop's clock runs here, so cycle after cycle comes due and every one
    of them would go to the device. The backoff is the only thing stopping
    them."""
    fake = _FailingReadHopper(stack, backoff_base=UNENDING_BACKOFF, backoff_cap=UNENDING_BACKOFF)
    hopper = fake.hopper
    try:
        _poll_until(lambda: hopper.sample_count >= 1, "the first, failing cycle")
        reads, opens, cycles = fake.reads, fake.opens, hopper.sample_count

        # Many SENSOR_SAMPLE_INTERVALs of loop time: the pacing sleep advances
        # the clock, so this is thousands of cycles' worth of opportunity.
        _real_time.sleep(0.2)

        assert fake.reads == reads, f"the sampler took {fake.reads - reads} readings during its backoff"
        assert fake.opens == opens, f"the sampler re-opened the device {fake.opens - opens} times during its backoff"
        assert hopper.sample_count == cycles, "cycles kept being counted during the backoff"
    finally:
        _stop(hopper)


def test_a_request_during_a_backoff_is_neither_serviced_early_nor_lost(stack):
    """`request_sample()` is called from controller.py and from modes/base.py,
    and it can otherwise pull the next attempt forward to the loop's own tick
    -- which would leave a failing sensor being read about once a second."""
    clock = _FakeClock()
    with mock.patch.object(sampled_base, "time", clock):
        fake = _FailingReadHopper(stack)
        hopper = fake.hopper
        try:
            _poll_until(lambda: hopper.sample_count >= 1, "the first, failing cycle")
            reads = fake.reads

            hopper.request_sample()
            _real_time.sleep(0.1)
            assert fake.reads == reads, "request_sample() pulled a reading forward into the backoff"
            assert hopper.sample_requested is True, "the request was dropped instead of held for later"

            # Nothing else is due -- SENSOR_SAMPLE_INTERVAL has not elapsed on
            # this clock -- so a reading now is the held request, and only that.
            fake.fail = False
            clock.advance(hopper.backoff_base_seconds)

            _poll_until(lambda: fake.reads > reads, "the held request once the backoff expired")
            _poll_until(lambda: hopper.get_level() == 94, "the level the held request produced")
        finally:
            _stop(hopper)


def test_the_wait_after_a_failure_doubles_and_stops_at_the_cap():
    hopper = _idle_sampler()

    delays = [hopper._backoff_delay(failures) for failures in range(1, 8)]

    assert delays == [1, 2, 4, 8, 10, 10, 10]


def test_consecutive_failures_climb_the_schedule_and_one_success_returns_to_the_base():
    hopper = _idle_sampler()
    clock = _FakeClock()
    with mock.patch.object(sampled_base, "time", clock):
        waits = []
        for _failure in range(6):
            hopper._note_cycle_failed("simulated")
            waits.append(hopper._backoff_until - clock.time())
        assert waits == [1, 2, 4, 8, 10, 10]

        hopper._note_cycle_succeeded()
        assert hopper._backoff_until is None
        assert hopper._consecutive_failures == 0

        hopper._note_cycle_failed("simulated")
        assert hopper._backoff_until - clock.time() == hopper.backoff_base_seconds


def test_a_run_of_failures_is_logged_once_and_so_is_the_recovery(stack):
    """A dead sensor produces a failed cycle forever. One record when the run
    starts and one when it ends, rather than one per cycle."""
    clock = _FakeClock()
    with mock.patch.object(sampled_base, "time", clock):
        fake = _FailingReadHopper(stack)
        hopper = fake.hopper
        try:
            _poll_until(lambda: fake.logger.error.call_count >= 1, "the failure banner")

            for _step in range(4):
                cycles = hopper.sample_count
                clock.advance(hopper.backoff_cap_seconds)
                _poll_until(lambda: hopper.sample_count > cycles, "another failed cycle")

            assert fake.logger.error.call_count == 1, "a sensor that keeps failing logged once per cycle"
            assert fake.logger.info.call_count == 0

            fake.fail = False
            clock.advance(hopper.backoff_cap_seconds)
            _poll_until(lambda: fake.logger.info.call_count >= 1, "the recovery record")

            assert fake.logger.info.call_count == 1
            assert fake.logger.error.call_count == 1
            assert "resumed" in fake.logger.info.call_args[0][0]
        finally:
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


def test_a_healthy_sampler_never_backs_off_and_never_logs(stack, ticking_loop_clock):
    """The other half of "unchanged": no wait accumulates, and an operator
    watching the log sees nothing at all from a sensor that is working."""
    fake = _FailingReadHopper(stack, fail=False)
    hopper = fake.hopper
    try:
        _poll_until(lambda: hopper.sample_count >= 3, "three healthy cycles")
        # Joined before the counts are read, so they describe whole cycles
        # rather than whichever one happened to be in flight.
        _stop(hopper)

        assert hopper._backoff_until is None
        assert hopper._consecutive_failures == 0
        assert fake.logger.error.call_count == 0
        assert fake.logger.info.call_count == 0
        assert fake.reads == hopper.sample_count * hopper.samples_per_cycle
    finally:
        _stop(hopper)
