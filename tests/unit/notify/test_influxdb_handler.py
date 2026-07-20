"""Tests for notify.influxdb_handler.InfluxNotificationHandler.

Mocks the InfluxDB client boundary -- ``influxdb_client.InfluxDBClient``,
imported locally inside ``publishing_thread`` -- with FakeInfluxDBClient /
FakeWriteApi below. No real database/network is ever touched. The real
``influxdb_client.Point`` and ``influxdb_client.WriteOptions`` classes are
used as-is (pure in-memory value objects, no I/O), so field/tag/WriteOptions
assertions exercise the real library's shape, not a stand-in.

``publishing_thread`` runs an intentional ``while True`` polling loop, so
tests drive it directly (never via a real background thread) and escape the
loop by making a patched ``time.sleep`` raise a sentinel ``_StopLoop`` on a
specific call -- always one of the *outer*, un-guarded ``time.sleep(1)``
calls at the top of the loop, since anything raised inside the inner
``try/except`` is swallowed by the handler's bare ``except:``.

Covers: __init__'s daemon-thread wiring (target/args/daemon/start), the
happy-path write cycle (client construction args, WriteOptions literals,
queue drain), the empty-queue no-op write, and notify()'s point construction
(field/measurement mapping, grill_name default, pelletdb/mode defaults,
grill_platform output fields, the GRILL_STATE Event-field suppression) and
its 1-second debounce.

Three latent bugs were found here (coverage-round2) and have since been
FIXED; the pinning tests were flipped to assert the fixed behavior, and a
new test covers the previously-undemonstrated bug #3:

  1. **First point after startup is always dropped** (influxdb_handler.py:9,
     60-61): ``__init__`` used to set ``self.last_updated = time.time()``
     instead of 0/None, and ``notify()``'s debounce checks
     ``time.time() - self.last_updated < 1``. The handler is constructed and
     then immediately ``notify()``-ed from the same call site
     (notify/notifications.py:534-538), so the elapsed time was essentially
     always < 1s -- the very first metrics point after every startup/handler
     re-creation was silently swallowed by the debounce, not just throttled.
     Fixed by seeding ``last_updated = 0``. See
     ``test_notify_first_call_after_construction_is_not_dropped_fixed``.

  2. **Buffered points were lost on write failure, not retried**
     (influxdb_handler.py:50-53): ``buf = self.queue.copy(); self.queue.clear()``
     ran *before* ``write_api.write(...)``. If the write raised (auth
     failure, network blip, InfluxDB down -- exactly the case the bare
     ``except`` is there to handle), the just-cleared points were gone
     forever; there was no requeue. Fixed by only trimming the written
     prefix off ``self.queue`` *after* a successful ``write()`` call, so a
     failed write leaves the points in place to be retried on the next loop
     iteration. See
     ``test_publishing_thread_write_failure_retains_buffer_and_retries_fixed``.

  3. **Hardcoded assumption of >=2 "food" probes, unguarded**
     (influxdb_handler.py:75-76): ``Probe1Key``/``Probe2Key`` indexed
     ``list(...)[0]``/``[1]`` into ``in_data["probe_history"]["food"]``
     unconditionally. A grill configured with 0 or 1 food probes raised
     IndexError inside ``notify()``, which has no try/except of its own, and
     neither did its only caller
     (notify/notifications.py:_send_influxdb_notification). Fixed by
     guarding both indices and defaulting missing probes' temp/setpoint
     fields to 0.0, matching how mqtt_handler iterates its configured probe
     map dynamically instead of assuming a fixed count. See
     ``test_notify_zero_food_probes_does_not_crash_fixed`` and
     ``test_notify_one_food_probe_does_not_crash_fixed``.
"""

import time
from datetime import datetime

import pytest

import notify.influxdb_handler as IH


# ---------------------------------------------------------------------------
# Fake InfluxDB client -- the mocked network boundary
# ---------------------------------------------------------------------------


class _StopLoop(Exception):
    """Sentinel used to escape publishing_thread's intentional infinite loop."""


class FakeWriteApi:
    def __init__(self, write_options, raise_on_write=False):
        self.write_options = write_options
        self.raise_on_write = raise_on_write
        self.write_calls = []

    def write(self, bucket, org, record):
        if self.raise_on_write:
            raise RuntimeError("influx write failed")
        self.write_calls.append((bucket, org, record))


class FakeInfluxDBClient:
    """Stand-in for influxdb_client.InfluxDBClient. Never touches a socket."""

    instances = []
    # Consumed in order by write_api() to decide whether the *next* created
    # write_api raises on write(). Tests set this before calling
    # publishing_thread. Empty/exhausted -> False (no raise).
    raise_sequence = []

    def __init__(self, url=None, token=None, org=None):
        self.url = url
        self.token = token
        self.org = org
        self.write_apis = []
        FakeInfluxDBClient.instances.append(self)

    def write_api(self, write_options=None):
        raises = FakeInfluxDBClient.raise_sequence.pop(0) if FakeInfluxDBClient.raise_sequence else False
        api = FakeWriteApi(write_options, raise_on_write=raises)
        self.write_apis.append(api)
        return api


@pytest.fixture(autouse=True)
def _reset_fake_client():
    FakeInfluxDBClient.instances = []
    FakeInfluxDBClient.raise_sequence = []
    yield
    FakeInfluxDBClient.instances = []
    FakeInfluxDBClient.raise_sequence = []


def _make_sleep_spy(monkeypatch, raise_on_call):
    """Patch IH.time.sleep to record calls and raise _StopLoop on the Nth call
    (1-indexed) *instead of* sleeping. Callers must pick an N that lands on
    one of the loop's outer, try-unguarded time.sleep(1) calls, or the
    exception gets silently swallowed by the handler's bare except."""

    calls = []

    def fake_sleep(seconds):
        calls.append(seconds)
        if len(calls) == raise_on_call:
            raise _StopLoop()

    monkeypatch.setattr(IH.time, "sleep", fake_sleep)
    return calls


# ---------------------------------------------------------------------------
# __init__ -- daemon thread wiring
# ---------------------------------------------------------------------------


class FakeThread:
    instances = []

    def __init__(self, target=None, daemon=None, args=None):
        self.target = target
        self.daemon = daemon
        self.args = args
        self.started = False
        FakeThread.instances.append(self)

    def start(self):
        self.started = True


def _settings(**influx_overrides):
    settings = {
        "globals": {"grill_name": ""},
        "notify_services": {
            "influxdb": {
                "enabled": True,
                "url": "http://influx.local:8086",
                "token": "tok123",
                "org": "myorg",
                "bucket": "mybucket",
            }
        },
    }
    settings["notify_services"]["influxdb"].update(influx_overrides)
    return settings


def test_init_starts_daemon_thread_with_influxdb_connection_settings(monkeypatch):
    FakeThread.instances = []
    monkeypatch.setattr(IH.threading, "Thread", FakeThread)

    handler = IH.InfluxNotificationHandler(_settings())

    assert len(FakeThread.instances) == 1
    t = FakeThread.instances[0]
    assert t.daemon is True
    assert t.started is True
    assert t.target == handler.publishing_thread
    assert t.args == ("http://influx.local:8086", "tok123", "myorg", "mybucket")
    assert handler.queue == []


# ---------------------------------------------------------------------------
# publishing_thread -- client construction, WriteOptions, write cycle
# ---------------------------------------------------------------------------


def test_publishing_thread_writes_queued_points_with_expected_client_and_options(monkeypatch):
    monkeypatch.setattr("influxdb_client.InfluxDBClient", FakeInfluxDBClient)
    # call1: outer sleep(1) (iter1, write_api created) call2: try-block sleep(5)
    # call3: outer sleep(1) (iter2 -- write_api is *not* None, so the `if not
    # write_api:` creation branch is skipped/reused) call4: try-block sleep(5)
    # call5: outer sleep(1) (iter3) -- raise here, outside any try/except.
    sleep_calls = _make_sleep_spy(monkeypatch, raise_on_call=5)

    handler = IH.InfluxNotificationHandler.__new__(IH.InfluxNotificationHandler)
    handler.queue = ["point-1", "point-2"]

    with pytest.raises(_StopLoop):
        handler.publishing_thread("http://influx.local:8086", "tok123", "myorg", "mybucket")

    assert sleep_calls == [1, 5, 1, 5, 1]

    assert len(FakeInfluxDBClient.instances) == 1
    client = FakeInfluxDBClient.instances[0]
    assert (client.url, client.token, client.org) == ("http://influx.local:8086", "tok123", "myorg")

    # Exactly one write_api across two loop iterations: the second iteration's
    # `if not write_api:` is False (still set from iteration 1), so it is
    # reused rather than recreated.
    assert len(client.write_apis) == 1
    api = client.write_apis[0]
    opts = api.write_options
    assert opts.batch_size == 100
    assert opts.flush_interval == 5_000
    assert opts.jitter_interval == 2_000
    assert opts.retry_interval == 2_000
    assert opts.max_retries == 3
    assert opts.max_retry_delay == 30_000
    assert opts.exponential_base == 2

    # Only iteration 1 had queued points; iteration 2 found an empty queue
    # (already drained) and skipped the write, so exactly one write() call.
    assert api.write_calls == [("mybucket", "myorg", ["point-1", "point-2"])]
    assert handler.queue == []  # drained


def test_publishing_thread_skips_write_when_queue_empty(monkeypatch):
    monkeypatch.setattr("influxdb_client.InfluxDBClient", FakeInfluxDBClient)
    sleep_calls = _make_sleep_spy(monkeypatch, raise_on_call=3)

    handler = IH.InfluxNotificationHandler.__new__(IH.InfluxNotificationHandler)
    handler.queue = []

    with pytest.raises(_StopLoop):
        handler.publishing_thread("url", "tok", "org", "bucket")

    assert sleep_calls == [1, 5, 1]
    client = FakeInfluxDBClient.instances[0]
    assert client.write_apis[0].write_calls == []


def test_publishing_thread_write_failure_retains_buffer_and_retries_fixed(monkeypatch):
    """FIXED LATENT BUG #2: the queue used to be cleared before write() was
    attempted, so a write failure lost the buffered points instead of
    retrying them. Now the queue is only trimmed after a successful write,
    so a failed write leaves the point(s) in place to be retried on the next
    loop iteration.
    Sequence: call1 outer sleep(1) [iter1] -> write_api #1 created -> write()
    raises -> except resets write_api=None, call2 sleep(10) -> call3 outer
    sleep(1) [iter2] -> write_api #2 created (queue still has "lost-point" --
    fix, not dropped) -> write() succeeds this time, queue drained -> call4
    try-block sleep(5) -> call5 outer sleep(1) [iter3], raise here."""
    monkeypatch.setattr("influxdb_client.InfluxDBClient", FakeInfluxDBClient)
    FakeInfluxDBClient.raise_sequence = [True]  # first write_api's write() raises
    sleep_calls = _make_sleep_spy(monkeypatch, raise_on_call=5)

    handler = IH.InfluxNotificationHandler.__new__(IH.InfluxNotificationHandler)
    handler.queue = ["lost-point"]

    with pytest.raises(_StopLoop):
        handler.publishing_thread("url", "tok", "org", "bucket")

    assert sleep_calls == [1, 10, 1, 5, 1]

    client = FakeInfluxDBClient.instances[0]
    assert len(client.write_apis) == 2  # write_api was reset to None and recreated
    first_api, second_api = client.write_apis
    assert first_api.write_calls == []  # the write that raised never recorded success
    # The point was NOT dropped -- it's still in the queue for the retry, and
    # the second (successful) write_api picks it up and writes it.
    assert second_api.write_calls == [("bucket", "org", ["lost-point"])]
    assert handler.queue == []  # drained only after the successful retry


# ---------------------------------------------------------------------------
# notify() -- point construction
# ---------------------------------------------------------------------------


class FakeGrillPlatform:
    def __init__(self, outputs):
        self._outputs = outputs

    def GetOutputStatus(self):
        return self._outputs


def _in_data(**overrides):
    data = {
        "probe_history": {
            "primary": {"Grill": 225.0},
            "food": {"Probe1": 150.0, "Probe2": 160.0},
        },
        "primary_setpoint": 225,
        "notify_targets": {"Grill": 225, "Probe1": 160, "Probe2": 170},
    }
    data.update(overrides)
    return data


def _handler():
    """A handler built via __new__ (bypasses __init__'s thread-spawning) with
    last_updated pushed into the past so the 1s debounce doesn't interfere
    unless a test wants it to."""
    handler = IH.InfluxNotificationHandler.__new__(IH.InfluxNotificationHandler)
    handler.queue = []
    handler.last_updated = time.time() - 10
    return handler


def test_notify_builds_point_with_expected_measurement_and_fields():
    handler = _handler()
    settings = _settings()
    settings["globals"]["grill_name"] = "Backyard Smoker"

    handler.notify(
        "SOME_EVENT",
        {"mode": "Hold"},
        settings,
        {"current": {"hopper_level": 42}},
        _in_data(),
        None,
    )

    assert len(handler.queue) == 1
    p = handler.queue[0]
    assert p._name == "Backyard Smoker"
    assert p._fields["GrillTemp"] == 225.0
    assert p._fields["GrillSetPoint"] == 225.0
    assert p._fields["GrillNotifyPoint"] == 225.0
    assert p._fields["Probe1Temp"] == 150.0
    assert p._fields["Probe1SetPoint"] == 160.0
    assert p._fields["Probe2Temp"] == 160.0
    assert p._fields["Probe2SetPoint"] == 170.0
    assert p._fields["Mode"] == "Hold"
    assert p._fields["PelletLevel"] == 42
    assert p._fields["Event"] == "SOME_EVENT"
    assert isinstance(p._time, datetime)


def test_notify_blank_grill_name_defaults_to_smoker():
    handler = _handler()
    handler.notify("EVT", {}, _settings(), {"current": {"hopper_level": 10}}, _in_data(), None)
    assert handler.queue[0]._name == "Smoker"


def test_notify_grill_state_event_is_suppressed():
    # GRILL_STATE is the routine per-cycle event (notify/notifications.py
    # sends it every loop); the handler deliberately omits the Event field
    # for it to avoid a constant no-op tag on every point.
    handler = _handler()
    handler.notify("GRILL_STATE", {}, _settings(), {"current": {"hopper_level": 10}}, _in_data(), None)
    assert "Event" not in handler.queue[0]._fields


def test_notify_no_event_omits_event_field():
    handler = _handler()
    handler.notify(None, {}, _settings(), {"current": {"hopper_level": 10}}, _in_data(), None)
    assert "Event" not in handler.queue[0]._fields


def test_notify_missing_control_and_pelletdb_use_defaults():
    handler = _handler()
    handler.notify("EVT", None, _settings(), None, _in_data(), None)
    p = handler.queue[0]
    assert p._fields["Mode"] == "unknown"
    assert p._fields["PelletLevel"] == 100


def test_notify_grill_platform_outputs_added_as_int_fields():
    handler = _handler()
    platform = FakeGrillPlatform({"fan": True, "auger": False, "igniter": 1})
    handler.notify("EVT", {}, _settings(), {"current": {"hopper_level": 10}}, _in_data(), platform)
    p = handler.queue[0]
    assert p._fields["fan"] == 1
    assert p._fields["auger"] == 0
    assert p._fields["igniter"] == 1


def test_notify_no_grill_platform_adds_no_output_fields():
    handler = _handler()
    handler.notify("EVT", {}, _settings(), {"current": {"hopper_level": 10}}, _in_data(), None)
    p = handler.queue[0]
    for key in ("fan", "auger", "igniter"):
        assert key not in p._fields


def test_notify_zero_food_probes_does_not_crash_fixed():
    """FIXED LATENT BUG #3 (influxdb_handler.py:75-76): Probe1Key/Probe2Key
    used to index list(...)[0]/[1] into in_data["probe_history"]["food"]
    unconditionally, raising IndexError for any grill configured with fewer
    than 2 food probes. Now both indices are guarded and default to 0.0 when
    the corresponding probe is absent."""
    handler = _handler()
    handler.notify(
        "EVT",
        {},
        _settings(),
        {"current": {"hopper_level": 10}},
        _in_data(probe_history={"primary": {"Grill": 225.0}, "food": {}}),
        None,
    )
    assert len(handler.queue) == 1
    p = handler.queue[0]
    assert p._fields["Probe1Temp"] == 0.0
    assert p._fields["Probe1SetPoint"] == 0.0
    assert p._fields["Probe2Temp"] == 0.0
    assert p._fields["Probe2SetPoint"] == 0.0


def test_notify_one_food_probe_does_not_crash_fixed():
    """FIXED LATENT BUG #3: a single configured food probe no longer raises
    IndexError on Probe2Key -- Probe2's fields default to 0.0."""
    handler = _handler()
    handler.notify(
        "EVT",
        {},
        _settings(),
        {"current": {"hopper_level": 10}},
        _in_data(
            probe_history={"primary": {"Grill": 225.0}, "food": {"Probe1": 150.0}},
            notify_targets={"Grill": 225, "Probe1": 160},
        ),
        None,
    )
    assert len(handler.queue) == 1
    p = handler.queue[0]
    assert p._fields["Probe1Temp"] == 150.0
    assert p._fields["Probe1SetPoint"] == 160.0
    assert p._fields["Probe2Temp"] == 0.0
    assert p._fields["Probe2SetPoint"] == 0.0


def test_notify_updates_last_updated_after_queuing():
    handler = _handler()
    before = handler.last_updated
    handler.notify("EVT", {}, _settings(), {"current": {"hopper_level": 10}}, _in_data(), None)
    assert handler.last_updated > before


def test_notify_debounced_within_one_second():
    handler = _handler()
    handler.last_updated = time.time()  # "just updated" -- inside the 1s window
    handler.notify("EVT", {}, _settings(), {"current": {"hopper_level": 10}}, _in_data(), None)
    assert handler.queue == []  # debounced: nothing queued, no crash despite in_data being valid


def test_notify_first_call_after_construction_is_not_dropped_fixed(monkeypatch):
    """FIXED LATENT BUG #1: __init__ used to set last_updated=time.time()
    (not 0), so a notify() called immediately after construction -- exactly
    the sequence notify/notifications.py:_send_influxdb_notification uses
    (construct handler, then notify() in the same call) -- was swallowed by
    the debounce and never queued. Now last_updated is seeded to 0, so the
    first notify() after construction always clears the 1s debounce check."""
    monkeypatch.setattr(IH.threading, "Thread", FakeThread)
    handler = IH.InfluxNotificationHandler(_settings())

    handler.notify("EVT", {}, _settings(), {"current": {"hopper_level": 10}}, _in_data(), None)

    assert len(handler.queue) == 1  # the first point is no longer silently lost
