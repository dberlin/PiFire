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
