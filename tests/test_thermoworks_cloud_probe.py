import sys
import types
import importlib
import asyncio

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
