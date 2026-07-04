import sys
import types
import importlib
import asyncio
from datetime import datetime, timedelta, timezone

import pytest


def _install_fake_thermoworks_cloud(monkeypatch):
	"""Install fake thermoworks_cloud/aiohttp modules so the probe imports
	without the real network libraries. Individual tests can further
	monkeypatch attributes on the reloaded `probe` module (e.g.
	`probe.AuthFactory`) to control behavior for a specific test."""
	fake_tc = types.ModuleType('thermoworks_cloud')

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
	monkeypatch.setitem(sys.modules, 'thermoworks_cloud', fake_tc)

	fake_aiohttp = types.ModuleType('aiohttp')

	class ClientSession:
		async def __aenter__(self):
			return self

		async def __aexit__(self, *exc):
			return False

	class ClientError(Exception):
		pass

	fake_aiohttp.ClientSession = ClientSession
	fake_aiohttp.ClientError = ClientError
	monkeypatch.setitem(sys.modules, 'aiohttp', fake_aiohttp)


def _load_probe(monkeypatch):
	_install_fake_thermoworks_cloud(monkeypatch)
	import probes.thermoworks_cloud as probe

	importlib.reload(probe)
	return probe


def test_poll_once_maps_channels_and_handles_missing(monkeypatch):
	probe = _load_probe(monkeypatch)

	class FakeReading:
		def __init__(self, value, units):
			self.value = value
			self.units = units

	class FakeClient:
		async def get_device_channel(self, serial, channel):
			assert serial == 'SN1'
			if channel == '2':
				raise probe.ResourceNotFoundError('missing')
			return FakeReading(value=100.0, units='F')

	result = asyncio.run(probe.poll_once(FakeClient(), 'SN1', 3))

	assert result[1].value == 100.0
	assert result[2] is None
	assert result[3].value == 100.0


def test_channel_to_celsius_converts_fahrenheit(monkeypatch):
	probe = _load_probe(monkeypatch)

	class FakeReading:
		def __init__(self, value, units):
			self.value = value
			self.units = units

	assert probe._channel_to_celsius(FakeReading(value=32.0, units='F')) == pytest.approx(0.0)
	assert probe._channel_to_celsius(FakeReading(value=100.0, units='C')) == pytest.approx(100.0)
	assert probe._channel_to_celsius(FakeReading(value=None, units='F')) is None


def test_get_channel_celsius_returns_fresh_value_and_none_when_stale(monkeypatch):
	probe = _load_probe(monkeypatch)

	device = probe.ThermoworksCloudDevice(
		email='a@b.com', password='pw', device_serial='SN1',
		num_probes=2, poll_interval=10,
	)

	fresh_time = datetime.now(timezone.utc)
	stale_time = fresh_time - timedelta(seconds=1000)
	device._cache[1] = (55.5, fresh_time)
	device._cache[2] = (60.0, stale_time)

	assert device.get_channel_celsius(1) == pytest.approx(55.5)
	assert device.get_channel_celsius(2) is None
	assert device.get_channel_celsius(3) is None  # never populated


def test_initial_status_is_disconnected(monkeypatch):
	probe = _load_probe(monkeypatch)

	device = probe.ThermoworksCloudDevice(
		email='a@b.com', password='pw', device_serial='SN1',
		num_probes=1, poll_interval=10,
	)

	status = device.get_status()
	assert status['connected'] is False
	assert status['last_error'] is None
	assert status['last_poll_time'] is None
