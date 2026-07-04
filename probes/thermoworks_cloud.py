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


_STALE_MULTIPLIER = 3  # a cached channel reading is considered stale (-> None)
                        # after this many missed poll intervals


def _channel_to_celsius(data):
	if data.value is None:
		return None
	if data.units == 'F':
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
		self.logger = logging.getLogger('control')

		self._cache = {}  # {channel_number: (celsius_value, fetched_at_utc)}
		self._lock = threading.Lock()
		self.status = {'connected': False, 'last_error': None, 'last_poll_time': None}

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
