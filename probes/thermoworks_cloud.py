"""
*****************************************
PiFire ThermoWorks Cloud Module
*****************************************

Description:
  Reads temperatures from ThermoWorks Cloud-connected wireless thermometers
  (Signals, Smoke, Smoke X4, Node, RFX, BlueDOT, etc.) via the
  `thermoworks-cloud` PyPI package. Polls the cloud API from a background
  thread and caches the latest reading per channel; read_all_ports() only
  reads the cache and never blocks on network I/O.

        Ex Device Definition:

        device_info = {
                        'device' : 'your_device_name',
                        'module' : 'thermoworks_cloud',
                        'ports' : ['TWC0', 'TWC1', 'TWC2', 'TWC3', 'TWC4', 'TWC5', 'TWC6', 'TWC7'],
                        'config' : {
                                        'email' : 'user@example.com',
                                        'password' : 'plaintext, like the MQTT password field',
                                        'device_serial' : '...',   # filled in by the wizard's discovery step
                                        'num_probes' : 4,          # filled in by the wizard's discovery step
                                        'poll_interval' : 30,      # seconds between cloud polls
                        }
                }

Requirements:
        thermoworks-cloud - https://github.com/a2hill/python-thermoworks-cloud
                pip install thermoworks-cloud
        A ThermoWorks Cloud account (email/password) with at least one connected device.
"""

from thermoworks_cloud import AuthFactory, ThermoworksCloud, ResourceNotFoundError, AuthenticationError
from aiohttp import ClientSession, ClientError

import asyncio
import logging
import threading
from datetime import datetime, timezone

from probes.base import ProbeInterface


async def poll_once(client, device_serial, num_probes):
    """Fetch channels 1..num_probes for one device. Pure — no thread, no
    sleep — this is the direct unit-test target for the polling logic."""
    results = {}
    for channel in range(1, num_probes + 1):
        try:
            results[channel] = await client.get_device_channel(device_serial, str(channel))
        except ResourceNotFoundError:
            results[channel] = None
    return results


async def discover_devices(client):
    """Enumerate this account's ThermoWorks devices and each one's channel
    count. Pure — takes an already-built client, so this is the direct
    unit-test target; discover() below wires in real auth/network."""
    user = await client.get_user()
    devices = await client.get_devices(user.account_id)
    results = []
    for device in devices:
        num_channels = 0
        for channel in range(1, 10):
            try:
                await client.get_device_channel(device.serial, str(channel))
                num_channels += 1
            except ResourceNotFoundError:
                break
        results.append(
            {"serial": device.serial, "label": device.label, "type": device.type, "num_channels": num_channels}
        )
    return results


async def discover(email, password):
    """Convenience wrapper used by the wizard's discovery route: builds a
    fresh session/auth/client and delegates to discover_devices()."""
    async with ClientSession() as session:
        auth = await AuthFactory(session).build_auth(email, password)
        client = ThermoworksCloud(auth)
        return await discover_devices(client)


_STALE_MULTIPLIER = 3  # a cached channel reading is considered stale (-> None)
# after this many missed poll intervals


def _channel_to_celsius(data):
    if data.value is None:
        return None
    if data.units == "F":
        return (data.value - 32) * 5 / 9
    return data.value


class ThermoworksCloudDevice:
    """Owns the cache of last-known channel readings. The background thread
    that populates the cache is started separately via start() (Task 3), so
    unit tests can construct this and poke _cache directly without spinning
    a real thread."""

    def __init__(self, email, password, device_serial, num_probes, poll_interval):
        self.email = email
        self.password = password
        self.device_serial = device_serial
        self.num_probes = num_probes
        self.poll_interval = poll_interval
        self.logger = logging.getLogger("control")

        self._cache = {}  # {channel_number: (celsius_value, fetched_at_utc)}
        self._lock = threading.Lock()
        self.status = {"connected": False, "last_error": None, "last_poll_time": None}

        self._thread = None
        self._stopped = False

    def get_channel_celsius(self, channel_number):
        with self._lock:
            entry = self._cache.get(channel_number)
        if entry is None:
            return None
        celsius, fetched_at = entry
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if age > self.poll_interval * _STALE_MULTIPLIER:
            return None
        return celsius

    def get_status(self):
        return self.status

    def start(self):
        self._stopped = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Signal the background poll loop to exit and join its thread.

        The loops in _main() check self._stopped, so the thread finishes its
        current poll interval and returns cleanly (no lingering event loop at
        interpreter shutdown, which otherwise delays process/test exit).
        """
        self._stopped = True
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)

    def _run_loop(self):
        asyncio.new_event_loop().run_until_complete(self._main())

    async def _main(self):
        while not self._stopped:
            try:
                async with ClientSession() as session:
                    auth = await AuthFactory(session).build_auth(self.email, self.password)
                    client = ThermoworksCloud(auth)
                    self.status["connected"] = True
                    self.status["last_error"] = None
                    while not self._stopped:
                        channels = await poll_once(client, self.device_serial, self.num_probes)
                        now = datetime.now(timezone.utc)
                        with self._lock:
                            for channel, data in channels.items():
                                if data is not None:
                                    self._cache[channel] = (_channel_to_celsius(data), now)
                        self.status["last_poll_time"] = now
                        await asyncio.sleep(self.poll_interval)
            except Exception as exc:  # AuthenticationError, ClientError, network errors, etc.
                self.status["connected"] = False
                self.status["last_error"] = str(exc)
                self.logger.error(f"thermoworks_cloud: {exc}")
                await asyncio.sleep(max(self.poll_interval, 60))  # backoff, then retry login


class ReadProbes(ProbeInterface):
    def __init__(self, probe_info, device_info, units):
        config = device_info["config"]
        self.email = config.get("email", "")
        self.password = config.get("password", "")
        self.device_serial = config.get("device_serial", "")
        self.num_probes = int(config.get("num_probes", 0))
        self.poll_interval = int(config.get("poll_interval", 30))
        super().__init__(probe_info, device_info, units)

    def _init_device(self):
        self.time_delay = 0
        self.device = ThermoworksCloudDevice(
            self.email, self.password, self.device_serial, self.num_probes, self.poll_interval
        )
        self.device.start()

    def read_all_ports(self, output_data):
        for port in self.port_map:
            channel_number = int(port.replace("TWC", "")) + 1
            if channel_number > self.num_probes:
                continue  # unused port beyond this device's discovered channel count

            celsius = self.device.get_channel_celsius(channel_number)
            output_value = celsius if self.units == "C" else self._to_fahrenheit(celsius)

            self.output_data["tr"][self.port_map[port]] = 0  # resistance NA

            if port == self.primary_port:
                self.output_data["primary"][self.port_map[port]] = output_value
            elif port in self.food_ports:
                self.output_data["food"][self.port_map[port]] = output_value
            elif port in self.aux_ports:
                self.output_data["aux"][self.port_map[port]] = output_value

        return self.output_data
