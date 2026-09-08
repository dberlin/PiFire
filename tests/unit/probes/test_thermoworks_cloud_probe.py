import asyncio
import importlib
import sys
import time
import types
from datetime import UTC, datetime, timedelta

import pytest


def _install_fake_thermoworks_cloud(monkeypatch):
    """Install fake thermoworks_cloud/aiohttp modules so the probe imports
    without the real network libraries. Individual tests can further
    monkeypatch attributes on the reloaded `probe` module (e.g.
    `probe.AuthFactory`) to control behavior for a specific test."""
    fake_tc = types.ModuleType("thermoworks_cloud")

    class ResourceNotFoundError(Exception):
        pass

    class AuthenticationError(Exception):
        def __init__(self, message, reason=None):
            super().__init__(message)
            self.reason = reason

    class AuthFactory:
        def __init__(self, session, api_key=None, app_id=None, referer=None):
            pass

        async def build_auth(self, email, password):
            raise NotImplementedError

    class ThermoworksCloud:
        def __init__(self, auth):
            pass

    fake_tc.AuthFactory = AuthFactory
    fake_tc.ThermoworksCloud = ThermoworksCloud
    fake_tc.ResourceNotFoundError = ResourceNotFoundError
    fake_tc.AuthenticationError = AuthenticationError
    monkeypatch.setitem(sys.modules, "thermoworks_cloud", fake_tc)

    fake_aiohttp = types.ModuleType("aiohttp")

    class ClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class ClientError(Exception):
        pass

    fake_aiohttp.ClientSession = ClientSession
    fake_aiohttp.ClientError = ClientError
    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)


def _load_probe(monkeypatch):
    _install_fake_thermoworks_cloud(monkeypatch)
    import probes.thermoworks_cloud as probe

    importlib.reload(probe)
    return probe


def _wait_for(predicate, timeout=2.0, interval=0.005):
    """Poll `predicate()` until it returns truthy or `timeout` seconds have
    elapsed, then return its final value. Used instead of a single blind
    `time.sleep()` to synchronize with a background thread/poll loop: it
    returns as soon as the condition is met (usually far under `timeout`)
    rather than hardcoding a fixed wait that may be too short under load or
    needlessly long otherwise."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


def test_poll_once_maps_channels_and_handles_missing(monkeypatch):
    probe = _load_probe(monkeypatch)

    class FakeReading:
        def __init__(self, value, units):
            self.value = value
            self.units = units

    class FakeClient:
        async def get_device_channel(self, serial, channel):
            assert serial == "SN1"
            if channel == "2":
                raise probe.ResourceNotFoundError("missing")
            return FakeReading(value=100.0, units="F")

    result = asyncio.run(probe.poll_once(FakeClient(), "SN1", 3))

    assert result[1].value == 100.0
    assert result[2] is None
    assert result[3].value == 100.0


def test_channel_to_celsius_converts_fahrenheit(monkeypatch):
    probe = _load_probe(monkeypatch)

    class FakeReading:
        def __init__(self, value, units):
            self.value = value
            self.units = units

    assert probe._channel_to_celsius(FakeReading(value=32.0, units="F")) == pytest.approx(0.0)
    assert probe._channel_to_celsius(FakeReading(value=100.0, units="C")) == pytest.approx(100.0)
    assert probe._channel_to_celsius(FakeReading(value=None, units="F")) is None


@pytest.fixture
def cloud_poll(monkeypatch):
    probe = _load_probe(monkeypatch)

    class Clock:
        monotonic_s = 100.0
        wall = datetime(2026, 9, 8, tzinfo=UTC)

        def monotonic(self) -> float:
            return self.monotonic_s

    clock = Clock()

    class WallClock:
        @staticmethod
        def now(tz):
            return clock.wall

    class FakeClient:
        response = types.SimpleNamespace(value=100.0, units="C")
        before_response = None

        async def get_device_channel(self, serial, channel):
            if self.before_response is not None:
                self.before_response()
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

    client = FakeClient()

    class FakeAuthFactory:
        def __init__(self, session):
            pass

        async def build_auth(self, email, password):
            return object()

    async def finish_poll(delay):
        raise asyncio.CancelledError

    monkeypatch.setattr(probe, "datetime", WallClock)
    monkeypatch.setattr(probe, "AuthFactory", FakeAuthFactory)
    monkeypatch.setattr(probe, "ThermoworksCloud", lambda auth: client)
    monkeypatch.setattr(probe.asyncio, "sleep", finish_poll)
    device = probe.ThermoworksCloudDevice(
        email="a@b.com",
        password="pw",
        device_serial="SN1",
        num_probes=1,
        poll_interval=10,
        monotonic=clock.monotonic,
    )

    def poll():
        async def run():
            try:
                await device._main()
            except asyncio.CancelledError:
                pass

        asyncio.run(run())

    reader = probe.ReadProbes.__new__(probe.ReadProbes)
    reader.device = device
    reader.units = "C"
    reader.num_probes = 1
    reader.port_map = {"TWC0": "Grill"}
    reader.primary_port = "TWC0"
    reader.food_ports = []
    reader.aux_ports = []
    reader.output_data = {"primary": {"Grill": None}, "food": {}, "aux": {}, "tr": {}}
    return types.SimpleNamespace(
        probe=probe,
        device=device,
        clock=clock,
        client=client,
        poll=poll,
        reader=reader,
    )


def test_cloud_cache_uses_receipt_monotonic_boundary(cloud_poll):
    device, clock = cloud_poll.device, cloud_poll.clock
    assert device.get_channel_celsius(1) is None
    cloud_poll.poll()
    assert device.get_channel_celsius(1) == 100.0
    assert device.status["last_poll_time"] == "2026-09-08T00:00:00+00:00"
    clock.monotonic_s = 130.0
    assert device.get_channel_celsius(1) == 100.0
    clock.monotonic_s = 130.001
    assert device.get_channel_celsius(1) is None
    clock.monotonic_s = 99.999
    assert device.get_channel_celsius(1) is None


def test_cloud_outage_does_not_revive_cache_on_wall_rollback(cloud_poll):
    cloud_poll.poll()
    cloud_poll.clock.monotonic_s = 120.0
    cloud_poll.client.response = RuntimeError("network unavailable")
    cloud_poll.poll()
    assert cloud_poll.device.get_channel_celsius(1) == 100.0
    cloud_poll.clock.monotonic_s = 130.001
    cloud_poll.clock.wall -= timedelta(seconds=3600)
    assert cloud_poll.device.get_channel_celsius(1) is None
    assert cloud_poll.reader.read_all_ports({})["primary"]["Grill"] is None


def test_cloud_wall_forward_step_does_not_expire_fresh_receipt(cloud_poll):
    cloud_poll.poll()
    cloud_poll.clock.wall += timedelta(seconds=3600)
    cloud_poll.clock.monotonic_s = 130.0
    assert cloud_poll.device.get_channel_celsius(1) == 100.0
    assert cloud_poll.reader.read_all_ports({})["primary"]["Grill"] == 100.0
    cloud_poll.clock.monotonic_s = 130.001
    assert cloud_poll.device.get_channel_celsius(1) is None


def test_missing_channel_does_not_renew_receipt(cloud_poll):
    cloud_poll.poll()
    cloud_poll.clock.monotonic_s = 120.0
    cloud_poll.client.response = cloud_poll.probe.ResourceNotFoundError("missing")
    cloud_poll.poll()
    assert cloud_poll.device.get_channel_celsius(1) == 100.0
    cloud_poll.clock.monotonic_s = 130.001
    assert cloud_poll.device.get_channel_celsius(1) is None


def test_successful_none_temperature_replaces_prior_value(cloud_poll):
    cloud_poll.poll()
    cloud_poll.client.response = types.SimpleNamespace(value=None, units="C")
    cloud_poll.poll()
    assert cloud_poll.device.get_channel_celsius(1) is None


def test_stop_and_restart_require_new_cloud_receipt(cloud_poll, monkeypatch):
    cloud_poll.poll()
    cloud_poll.device.stop()
    assert cloud_poll.device.get_channel_celsius(1) is None
    monkeypatch.setattr(cloud_poll.device, "_run_loop", lambda stop_event: None)
    cloud_poll.device.start()
    assert cloud_poll.device.get_channel_celsius(1) is None
    cloud_poll.poll()
    assert cloud_poll.device.get_channel_celsius(1) == 100.0
    cloud_poll.device.start()
    assert cloud_poll.device.get_channel_celsius(1) is None
    cloud_poll.device.stop()


def test_clock_invalidation_discards_cached_and_inflight_receipts(cloud_poll):
    cloud_poll.poll()
    cloud_poll.reader.invalidate_clock_domain()
    assert cloud_poll.reader.read_all_ports({})["primary"]["Grill"] is None
    cloud_poll.client.before_response = cloud_poll.reader.invalidate_clock_domain
    cloud_poll.poll()
    assert cloud_poll.device.get_channel_celsius(1) is None
    cloud_poll.client.before_response = None
    cloud_poll.poll()
    assert cloud_poll.device.get_channel_celsius(1) == 100.0


@pytest.mark.parametrize("action", ["stop", "start"])
def test_lifecycle_change_rejects_inflight_response(cloud_poll, monkeypatch, action):
    cloud_poll.poll()
    monkeypatch.setattr(cloud_poll.device, "_run_loop", lambda stop_event: None)
    cloud_poll.client.before_response = getattr(cloud_poll.device, action)
    cloud_poll.poll()
    assert cloud_poll.device.get_channel_celsius(1) is None
    cloud_poll.device.stop()


def test_receipt_age_starts_when_response_arrives(cloud_poll):
    def delay_response():
        cloud_poll.clock.monotonic_s = 120.0

    cloud_poll.client.before_response = delay_response
    cloud_poll.poll()
    cloud_poll.clock.monotonic_s = 150.0
    assert cloud_poll.device.get_channel_celsius(1) == 100.0
    cloud_poll.clock.monotonic_s = 150.001
    assert cloud_poll.device.get_channel_celsius(1) is None


def test_initial_status_is_disconnected(monkeypatch):
    probe = _load_probe(monkeypatch)

    device = probe.ThermoworksCloudDevice(
        email="a@b.com", password="pw", device_serial="SN1", num_probes=1, poll_interval=10
    )

    status = device.get_status()
    assert status["connected"] is False
    assert status["last_error"] is None
    assert status["last_poll_time"] is None


def test_main_populates_cache_on_success(monkeypatch):
    probe = _load_probe(monkeypatch)

    class FakeReading:
        def __init__(self, value, units):
            self.value = value
            self.units = units

    class FakeThermoworksCloud:
        def __init__(self, auth):
            pass

        async def get_device_channel(self, serial, channel):
            return FakeReading(value=165.0, units="F")

    class FakeAuth:
        pass

    class FakeAuthFactory:
        def __init__(self, session):
            pass

        async def build_auth(self, email, password):
            return FakeAuth()

    class FakeClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(probe, "ClientSession", FakeClientSession)
    monkeypatch.setattr(probe, "AuthFactory", FakeAuthFactory)
    monkeypatch.setattr(probe, "ThermoworksCloud", FakeThermoworksCloud)

    device = probe.ThermoworksCloudDevice(
        email="a@b.com", password="pw", device_serial="SN1", num_probes=2, poll_interval=0.01
    )

    async def run_briefly():
        try:
            await asyncio.wait_for(device._main(), timeout=0.2)
        except TimeoutError:
            pass

    asyncio.run(run_briefly())

    assert device.status["connected"] is True
    assert device.status["last_error"] is None
    assert device.get_channel_celsius(1) == pytest.approx((165.0 - 32) * 5 / 9)

    # Regression: status must survive the write_generic_key() -> json.dumps()
    # round trip (last_poll_time used to be a raw datetime, which json.dumps
    # rejects).
    assert isinstance(device.status["last_poll_time"], str)
    json.dumps(device.status)


def test_main_sets_disconnected_status_on_auth_failure(monkeypatch):
    probe = _load_probe(monkeypatch)

    class FakeAuthFactory:
        def __init__(self, session):
            pass

        async def build_auth(self, email, password):
            raise probe.AuthenticationError("bad credentials")

    class FakeClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(probe, "ClientSession", FakeClientSession)
    monkeypatch.setattr(probe, "AuthFactory", FakeAuthFactory)

    device = probe.ThermoworksCloudDevice(
        email="a@b.com", password="wrong", device_serial="SN1", num_probes=1, poll_interval=0.01
    )

    async def run_briefly():
        try:
            await asyncio.wait_for(device._main(), timeout=0.05)
        except TimeoutError:
            pass

    asyncio.run(run_briefly())

    assert device.status["connected"] is False
    assert "bad credentials" in device.status["last_error"]


def test_start_spawns_thread_and_populates_cache(monkeypatch):
    probe = _load_probe(monkeypatch)

    class FakeReading:
        def __init__(self, value, units):
            self.value = value
            self.units = units

    class FakeThermoworksCloud:
        def __init__(self, auth):
            pass

        async def get_device_channel(self, serial, channel):
            return FakeReading(value=100.0, units="C")

    class FakeAuth:
        pass

    class FakeAuthFactory:
        def __init__(self, session):
            pass

        async def build_auth(self, email, password):
            return FakeAuth()

    class FakeClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(probe, "ClientSession", FakeClientSession)
    monkeypatch.setattr(probe, "AuthFactory", FakeAuthFactory)
    monkeypatch.setattr(probe, "ThermoworksCloud", FakeThermoworksCloud)

    device = probe.ThermoworksCloudDevice(
        email="a@b.com", password="pw", device_serial="SN1", num_probes=1, poll_interval=0.01
    )
    device.start()
    try:
        # The background thread populates the cache asynchronously; poll for it
        # instead of blindly sleeping a fixed duration.
        reading = _wait_for(lambda: device.get_channel_celsius(1))
        assert reading == pytest.approx(100.0)
    finally:
        device.stop()  # stop the background loop so it doesn't linger past the test


def test_discover_devices_counts_channels_per_device(monkeypatch):
    probe = _load_probe(monkeypatch)

    class FakeDevice:
        def __init__(self, serial, label, dtype):
            self.serial = serial
            self.label = label
            self.type = dtype

    class FakeUser:
        account_id = "ACC1"

    class FakeClient:
        async def get_user(self):
            return FakeUser()

        async def get_devices(self, account_id):
            assert account_id == "ACC1"
            return [FakeDevice("SN1", "Grill Signals", "signals"), FakeDevice("SN2", "Smoke", "smoke")]

        async def get_device_channel(self, serial, channel):
            channel_num = int(channel)
            limits = {"SN1": 4, "SN2": 2}
            if channel_num > limits[serial]:
                raise probe.ResourceNotFoundError("not found")
            return object()

    result = asyncio.run(probe.discover_devices(FakeClient()))

    assert result == [
        {"serial": "SN1", "label": "Grill Signals", "type": "signals", "num_channels": 4},
        {"serial": "SN2", "label": "Smoke", "type": "smoke", "num_channels": 2},
    ]


def test_init_device_wires_config_into_thermoworks_cloud_device(monkeypatch):
    probe = _load_probe(monkeypatch)

    captured = {}

    class FakeDevice:
        def __init__(self, email, password, device_serial, num_probes, poll_interval):
            captured["args"] = (email, password, device_serial, num_probes, poll_interval)

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(probe, "ThermoworksCloudDevice", FakeDevice)

    read_probes = probe.ReadProbes.__new__(probe.ReadProbes)
    device_info = {
        "config": {
            "email": "user@example.com",
            "password": "hunter2",
            "device_serial": "SN1",
            "num_probes": "4",
            "poll_interval": "45",
        }
    }
    read_probes.email = device_info["config"]["email"]
    read_probes.password = device_info["config"]["password"]
    read_probes.device_serial = device_info["config"]["device_serial"]
    read_probes.num_probes = int(device_info["config"]["num_probes"])
    read_probes.poll_interval = int(device_info["config"]["poll_interval"])

    read_probes._init_device()

    assert captured["args"] == ("user@example.com", "hunter2", "SN1", 4, 45)
    assert captured["started"] is True


def test_read_all_ports_maps_port_name_to_channel_and_respects_num_probes(monkeypatch):
    probe = _load_probe(monkeypatch)

    class FakeDevice:
        def __init__(self):
            self.readings = {1: 35.0, 3: 77.0}  # TWC0 -> channel 1, TWC2 -> channel 3

        def get_channel_celsius(self, channel_number):
            return self.readings.get(channel_number)

    read_probes = probe.ReadProbes.__new__(probe.ReadProbes)
    read_probes.units = "F"
    read_probes.num_probes = 3
    read_probes.device = FakeDevice()
    # Deliberately skip TWC1 to prove channel# comes from the port name, not
    # from enumerate() position.
    read_probes.port_map = {"TWC0": "Grill", "TWC2": "Food1", "TWC7": "Extra"}
    read_probes.primary_port = "TWC0"
    read_probes.food_ports = ["TWC2", "TWC7"]
    read_probes.aux_ports = []
    read_probes.output_data = {
        "primary": {"Grill": -999},
        "food": {"Food1": -999, "Extra": -999},
        "aux": {},
        "tr": {"Grill": -999, "Food1": -999, "Extra": -999},
    }

    result = read_probes.read_all_ports(read_probes.output_data)

    assert result["primary"]["Grill"] == read_probes._to_fahrenheit(35.0)
    assert result["food"]["Food1"] == read_probes._to_fahrenheit(77.0)
    assert result["tr"]["Grill"] == 0
    assert result["tr"]["Food1"] == 0
    # TWC7 -> channel 8, which is beyond num_probes=3, so it's skipped
    # entirely and its pre-existing sentinel value is untouched.
    assert result["food"]["Extra"] == -999
    assert result["tr"]["Extra"] == -999


import json
import os


def test_wizard_manifest_has_thermoworks_cloud_entry():
    manifest_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "wizard", "wizard_manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    entry = manifest["modules"]["probes"]["thermoworks_cloud"]

    assert entry["filename"] == "thermoworks_cloud"
    assert entry["device_specific"]["type"] == "network"
    assert entry["device_specific"]["ports"] == ["TWC0", "TWC1", "TWC2", "TWC3", "TWC4", "TWC5", "TWC6", "TWC7"]
    assert "thermoworks-cloud>=0.1.13" in entry["py_dependencies"]

    labels = [item["label"] for item in entry["device_specific"]["config"]]
    assert labels == ["email", "password", "device_serial", "num_probes", "poll_interval"]

    config_by_label = {item["label"]: item for item in entry["device_specific"]["config"]}
    assert config_by_label["device_serial"]["hidden"] is True
    assert config_by_label["num_probes"]["hidden"] is True
    assert config_by_label["poll_interval"]["default"] == 30


def test_close_stops_the_background_poll_thread(monkeypatch):
    """The poll thread outlives the ReadProbes instance that started it (it
    holds a reference back to the device), so a rebuild that only dropped the
    reference would leave it polling forever."""
    probe = _load_probe(monkeypatch)
    calls = []

    class FakeDevice:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            calls.append("start")

        def stop(self):
            calls.append("stop")

    monkeypatch.setattr(probe, "ThermoworksCloudDevice", FakeDevice)

    read_probes = probe.ReadProbes.__new__(probe.ReadProbes)
    read_probes.email = "user@example.com"
    read_probes.password = "hunter2"
    read_probes.device_serial = "SN1"
    read_probes.num_probes = 1
    read_probes.poll_interval = 30
    read_probes._init_device()

    read_probes.close()

    assert calls == ["start", "stop"]
