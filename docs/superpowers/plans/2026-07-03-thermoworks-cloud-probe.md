# ThermoWorks Cloud Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `thermoworks_cloud` probe module so PiFire can read live temperatures from ThermoWorks Cloud-connected wireless thermometers (Signals, Smoke, Smoke X4, Node, RFX, BlueDOT, etc.) via the `thermoworks-cloud` PyPI package, with a wizard "Test Connection" discovery step so users don't have to type in a device serial by hand.

**Architecture:** A new `probes/thermoworks_cloud.py` implements PiFire's `ProbeInterface`. A background thread runs one persistent `asyncio` event loop for the process lifetime of the device, polling the cloud API every `poll_interval` seconds and caching Celsius-normalized readings in a lock-protected dict; the main-thread `read_all_ports()` only reads that cache, never blocking on network I/O (mirroring the existing `bt_meater.py`/`bt_ibbq.py` pattern). A fixed 8-port wizard manifest entry (`TWC0`..`TWC7`) plus a hidden `num_probes` config field (filled in by a "Test Connection" discovery step, mirroring the existing Bluetooth-scan UI) works around ports being statically defined per manifest entry.

**Tech Stack:** Python 3.14, `thermoworks-cloud` (PyPI, async, requires `aiohttp`), Flask/Jinja (existing wizard blueprint), pytest with `monkeypatch`/fake-module injection (existing test convention, no real network in tests).

## Global Constraints

- Full design rationale and precedent citations: `docs/superpowers/specs/2026-07-03-thermoworks-cloud-probe-design.md` — read it first if anything below is ambiguous.
- No Steinhart-Hart conversion — readings are already calibrated by the cloud API; the `tr` (resistance) output slot is always `0`.
- Credentials (`email`/`password`) are stored as plaintext config, matching the existing MQTT password field convention (`blueprints/settings/templates/settings/index.html:1770`) — do not add encryption/masking.
- `device_specific.ports` in the wizard manifest is a **fixed, static list per manifest entry** (confirmed: `blueprints/probeconfig/routes.py:93` copies it verbatim on `add_device`) — do not attempt to make it dynamic per-instance. Use the `num_probes`-style hidden-field workaround instead (precedent: `probes/bt_ibbq.py:18`).
- `device_specific.type` is a descriptive label only, never branched on in code — the new value `"network"` is safe.
- Background thread must never die on a caught exception (bad password, network error) — log, update a status dict, back off, retry.
- All new unit tests must run without the real `thermoworks-cloud`/`aiohttp` packages installed — inject fake modules via `monkeypatch.setitem(sys.modules, ...)`, mirroring `tests/test_max31856_probe.py`.
- Run tests with: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v` (repo already has a working `tests/conftest.py` that puts the repo root on `sys.path`).

---

### Task 1: Dependency declarations + `poll_once()` channel-polling helper

**Files:**
- Modify: `pyproject.toml`
- Modify: `auto-install/requirements.txt`
- Create: `probes/thermoworks_cloud.py`
- Test: `tests/test_thermoworks_cloud_probe.py`

**Interfaces:**
- Produces: `probes.thermoworks_cloud.poll_once(client, device_serial: str, num_probes: int) -> dict[int, Any | None]` — an `async` function. Keys are channel numbers `1..num_probes`; value is whatever `client.get_device_channel` returned, or `None` if it raised `ResourceNotFoundError`.
- Produces: `probes.thermoworks_cloud.ResourceNotFoundError` — re-exported from the `thermoworks_cloud` package import, used by later tasks' tests.

- [ ] **Step 1: Add the dependency to `pyproject.toml`**

Open `pyproject.toml` and add this line inside the `dependencies = [...]` list (alphabetical position, after `"scikit-learn>=1.9.0",` and before `"uuid>=1.30",` — or anywhere in the list, order doesn't matter functionally):

```toml
    "thermoworks-cloud>=0.1.13",
```

- [ ] **Step 2: Add the dependency to `auto-install/requirements.txt`**

Open `auto-install/requirements.txt` and append this line at the end of the file:

```
thermoworks-cloud==0.1.13
```

- [ ] **Step 3: Write the test file with fake-module installer and the first failing test**

Create `tests/test_thermoworks_cloud_probe.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'probes.thermoworks_cloud'`

- [ ] **Step 4: Write the minimal implementation**

Create `probes/thermoworks_cloud.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml auto-install/requirements.txt probes/thermoworks_cloud.py tests/test_thermoworks_cloud_probe.py
git commit -m "feat(thermoworks-cloud): add dependency and poll_once() channel helper"
```

---

### Task 2: `ThermoworksCloudDevice` cache, unit conversion, and staleness

**Files:**
- Modify: `probes/thermoworks_cloud.py`
- Test: `tests/test_thermoworks_cloud_probe.py`

**Interfaces:**
- Consumes: nothing new from Task 1 beyond the module's existing imports.
- Produces: `probes.thermoworks_cloud.ThermoworksCloudDevice(email, password, device_serial, num_probes, poll_interval)` — constructing it does **not** start any thread (that's Task 3's `start()`). Exposes:
  - `get_channel_celsius(channel_number: int) -> float | None` — `None` if missing or the cached reading is older than `poll_interval * 3` seconds.
  - `get_status() -> dict` with keys `connected` (bool), `last_error` (str|None), `last_poll_time` (datetime|None).
  - Internal `_cache: dict[int, tuple[float, datetime]]` (channel -> `(celsius_value, fetched_at_utc)`), and `_lock: threading.Lock`.
- Produces: `probes.thermoworks_cloud._channel_to_celsius(data) -> float | None` — converts a `DeviceChannel`-shaped object's `.value`/`.units` to Celsius.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_thermoworks_cloud_probe.py` (add `from datetime import datetime, timedelta, timezone` to the top imports):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: FAIL — `AttributeError: module 'probes.thermoworks_cloud' has no attribute '_channel_to_celsius'` (and similar for `ThermoworksCloudDevice`)

- [ ] **Step 3: Write the minimal implementation**

In `probes/thermoworks_cloud.py`, add these imports at the top (alongside the existing ones):

```python
import logging
import threading
from datetime import datetime, timezone
```

Then add this constant and the two new pieces after `poll_once`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add probes/thermoworks_cloud.py tests/test_thermoworks_cloud_probe.py
git commit -m "feat(thermoworks-cloud): add ThermoworksCloudDevice cache with unit conversion and staleness"
```

---

### Task 3: Background polling thread with error backoff

**Files:**
- Modify: `probes/thermoworks_cloud.py`
- Test: `tests/test_thermoworks_cloud_probe.py`

**Interfaces:**
- Consumes: `poll_once` (Task 1), `ThermoworksCloudDevice.__init__`/`_cache`/`_lock`/`status` (Task 2).
- Produces: `ThermoworksCloudDevice.start()` — spawns the daemon thread. `ThermoworksCloudDevice._main()` — the `async` loop body (directly callable by tests via `asyncio.run`/`asyncio.wait_for`, without going through a real thread).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_thermoworks_cloud_probe.py` (add `import time` to the top imports):

```python
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
			return FakeReading(value=165.0, units='F')

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

	monkeypatch.setattr(probe, 'ClientSession', FakeClientSession)
	monkeypatch.setattr(probe, 'AuthFactory', FakeAuthFactory)
	monkeypatch.setattr(probe, 'ThermoworksCloud', FakeThermoworksCloud)

	device = probe.ThermoworksCloudDevice(
		email='a@b.com', password='pw', device_serial='SN1',
		num_probes=2, poll_interval=0.01,
	)

	async def run_briefly():
		try:
			await asyncio.wait_for(device._main(), timeout=0.2)
		except asyncio.TimeoutError:
			pass

	asyncio.run(run_briefly())

	assert device.status['connected'] is True
	assert device.status['last_error'] is None
	assert device.get_channel_celsius(1) == pytest.approx((165.0 - 32) * 5 / 9)


def test_main_sets_disconnected_status_on_auth_failure(monkeypatch):
	probe = _load_probe(monkeypatch)

	class FakeAuthFactory:
		def __init__(self, session):
			pass

		async def build_auth(self, email, password):
			raise probe.AuthenticationError('bad credentials')

	class FakeClientSession:
		async def __aenter__(self):
			return self

		async def __aexit__(self, *exc):
			return False

	monkeypatch.setattr(probe, 'ClientSession', FakeClientSession)
	monkeypatch.setattr(probe, 'AuthFactory', FakeAuthFactory)

	device = probe.ThermoworksCloudDevice(
		email='a@b.com', password='wrong', device_serial='SN1',
		num_probes=1, poll_interval=0.01,
	)

	async def run_briefly():
		try:
			await asyncio.wait_for(device._main(), timeout=0.05)
		except asyncio.TimeoutError:
			pass

	asyncio.run(run_briefly())

	assert device.status['connected'] is False
	assert 'bad credentials' in device.status['last_error']


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
			return FakeReading(value=100.0, units='C')

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

	monkeypatch.setattr(probe, 'ClientSession', FakeClientSession)
	monkeypatch.setattr(probe, 'AuthFactory', FakeAuthFactory)
	monkeypatch.setattr(probe, 'ThermoworksCloud', FakeThermoworksCloud)

	device = probe.ThermoworksCloudDevice(
		email='a@b.com', password='pw', device_serial='SN1',
		num_probes=1, poll_interval=0.01,
	)
	device.start()
	time.sleep(0.2)

	assert device.get_channel_celsius(1) == pytest.approx(100.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: FAIL — `AttributeError: 'ThermoworksCloudDevice' object has no attribute '_main'` (and `start`)

- [ ] **Step 3: Write the minimal implementation**

In `probes/thermoworks_cloud.py`, add `import asyncio` to the imports at the top. Then add these two methods to `ThermoworksCloudDevice` (after `get_status`):

```python
	def start(self):
		self._thread = threading.Thread(target=self._run_loop, daemon=True)
		self._thread.start()

	def _run_loop(self):
		asyncio.new_event_loop().run_until_complete(self._main())

	async def _main(self):
		while True:
			try:
				async with ClientSession() as session:
					auth = await AuthFactory(session).build_auth(self.email, self.password)
					client = ThermoworksCloud(auth)
					self.status['connected'] = True
					self.status['last_error'] = None
					while True:
						channels = await poll_once(client, self.device_serial, self.num_probes)
						now = datetime.now(timezone.utc)
						with self._lock:
							for channel, data in channels.items():
								if data is not None:
									self._cache[channel] = (_channel_to_celsius(data), now)
						self.status['last_poll_time'] = now
						await asyncio.sleep(self.poll_interval)
			except Exception as exc:  # AuthenticationError, ClientError, network errors, etc.
				self.status['connected'] = False
				self.status['last_error'] = str(exc)
				self.logger.error(f'thermoworks_cloud: {exc}')
				await asyncio.sleep(max(self.poll_interval, 60))  # backoff, then retry login
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add probes/thermoworks_cloud.py tests/test_thermoworks_cloud_probe.py
git commit -m "feat(thermoworks-cloud): add background polling thread with error backoff"
```

---

### Task 4: `discover_devices()` account enumeration helper

**Files:**
- Modify: `probes/thermoworks_cloud.py`
- Test: `tests/test_thermoworks_cloud_probe.py`

**Interfaces:**
- Produces: `probes.thermoworks_cloud.discover_devices(client) -> list[dict]` — pure `async` function; each dict has keys `serial`, `label`, `type`, `num_channels`. Used by Task 7's Flask route.
- Produces: `probes.thermoworks_cloud.discover(email, password) -> list[dict]` — `async` convenience wrapper that builds a fresh `ClientSession`/`Auth`/`ThermoworksCloud` and calls `discover_devices`. Not unit tested directly (network/auth wiring only) — Task 7's route calls `asyncio.run(discover(email, password))`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_thermoworks_cloud_probe.py`:

```python
def test_discover_devices_counts_channels_per_device(monkeypatch):
	probe = _load_probe(monkeypatch)

	class FakeDevice:
		def __init__(self, serial, label, dtype):
			self.serial = serial
			self.label = label
			self.type = dtype

	class FakeUser:
		account_id = 'ACC1'

	class FakeClient:
		async def get_user(self):
			return FakeUser()

		async def get_devices(self, account_id):
			assert account_id == 'ACC1'
			return [
				FakeDevice('SN1', 'Grill Signals', 'signals'),
				FakeDevice('SN2', 'Smoke', 'smoke'),
			]

		async def get_device_channel(self, serial, channel):
			channel_num = int(channel)
			limits = {'SN1': 4, 'SN2': 2}
			if channel_num > limits[serial]:
				raise probe.ResourceNotFoundError('not found')
			return object()

	result = asyncio.run(probe.discover_devices(FakeClient()))

	assert result == [
		{'serial': 'SN1', 'label': 'Grill Signals', 'type': 'signals', 'num_channels': 4},
		{'serial': 'SN2', 'label': 'Smoke', 'type': 'smoke', 'num_channels': 2},
	]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: FAIL — `AttributeError: module 'probes.thermoworks_cloud' has no attribute 'discover_devices'`

- [ ] **Step 3: Write the minimal implementation**

Add to `probes/thermoworks_cloud.py` (after `poll_once`, before `_STALE_MULTIPLIER`):

```python
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
		results.append({
			'serial': device.serial,
			'label': device.label,
			'type': device.type,
			'num_channels': num_channels,
		})
	return results


async def discover(email, password):
	"""Convenience wrapper used by the wizard's discovery route: builds a
	fresh session/auth/client and delegates to discover_devices()."""
	async with ClientSession() as session:
		auth = await AuthFactory(session).build_auth(email, password)
		client = ThermoworksCloud(auth)
		return await discover_devices(client)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add probes/thermoworks_cloud.py tests/test_thermoworks_cloud_probe.py
git commit -m "feat(thermoworks-cloud): add discover_devices() account enumeration helper"
```

---

### Task 5: `ReadProbes` wiring — `_init_device` and `read_all_ports`

**Files:**
- Modify: `probes/thermoworks_cloud.py`
- Test: `tests/test_thermoworks_cloud_probe.py`

**Interfaces:**
- Consumes: `ThermoworksCloudDevice` (Task 2/3), and `ProbeInterface` (`probes/base.py:174`) — specifically `self.port_map` (dict, port-name -> probe label, only for ports the user actually assigned), `self.primary_port`, `self.food_ports`, `self.aux_ports`, `self.output_data` (`{'primary': {}, 'food': {}, 'aux': {}, 'tr': {}}`), `self.units`, `self._to_fahrenheit(celsius)`/`self._to_celsius(fahrenheit)` (`probes/base.py:391,397`).
- Produces: `probes.thermoworks_cloud.ReadProbes(probe_info, device_info, units)` — the module's public entry point, loaded by `probes/main.py` via `importlib.import_module('probes.thermoworks_cloud')`.

**Important correctness note:** ports are named `TWC0`..`TWC7`, and channel number = the trailing digit + 1 (`TWC0` -> channel 1, `TWC3` -> channel 4). Do **not** derive the channel from `enumerate(self.port_map)`'s index — `self.port_map` only contains ports the user actually assigned to a named probe, so if a user assigns `TWC0` and `TWC2` but skips `TWC1`, `enumerate` would give `TWC2` index `1` (wrong: channel 2 instead of the correct channel 3). Parsing the channel number out of the port name itself is correct regardless of which ports are assigned.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_thermoworks_cloud_probe.py`:

```python
def test_init_device_wires_config_into_thermoworks_cloud_device(monkeypatch):
	probe = _load_probe(monkeypatch)

	captured = {}

	class FakeDevice:
		def __init__(self, email, password, device_serial, num_probes, poll_interval):
			captured['args'] = (email, password, device_serial, num_probes, poll_interval)

		def start(self):
			captured['started'] = True

	monkeypatch.setattr(probe, 'ThermoworksCloudDevice', FakeDevice)

	read_probes = probe.ReadProbes.__new__(probe.ReadProbes)
	device_info = {
		'config': {
			'email': 'user@example.com',
			'password': 'hunter2',
			'device_serial': 'SN1',
			'num_probes': '4',
			'poll_interval': '45',
		}
	}
	read_probes.email = device_info['config']['email']
	read_probes.password = device_info['config']['password']
	read_probes.device_serial = device_info['config']['device_serial']
	read_probes.num_probes = int(device_info['config']['num_probes'])
	read_probes.poll_interval = int(device_info['config']['poll_interval'])

	read_probes._init_device()

	assert captured['args'] == ('user@example.com', 'hunter2', 'SN1', 4, 45)
	assert captured['started'] is True


def test_read_all_ports_maps_port_name_to_channel_and_respects_num_probes(monkeypatch):
	probe = _load_probe(monkeypatch)

	class FakeDevice:
		def __init__(self):
			self.readings = {1: 35.0, 3: 77.0}  # TWC0 -> channel 1, TWC2 -> channel 3

		def get_channel_celsius(self, channel_number):
			return self.readings.get(channel_number)

	read_probes = probe.ReadProbes.__new__(probe.ReadProbes)
	read_probes.units = 'F'
	read_probes.num_probes = 3
	read_probes.device = FakeDevice()
	# Deliberately skip TWC1 to prove channel# comes from the port name, not
	# from enumerate() position.
	read_probes.port_map = {'TWC0': 'Grill', 'TWC2': 'Food1', 'TWC7': 'Extra'}
	read_probes.primary_port = 'TWC0'
	read_probes.food_ports = ['TWC2', 'TWC7']
	read_probes.aux_ports = []
	read_probes.output_data = {
		'primary': {'Grill': -999},
		'food': {'Food1': -999, 'Extra': -999},
		'aux': {},
		'tr': {'Grill': -999, 'Food1': -999, 'Extra': -999},
	}

	result = read_probes.read_all_ports(read_probes.output_data)

	assert result['primary']['Grill'] == read_probes._to_fahrenheit(35.0)
	assert result['food']['Food1'] == read_probes._to_fahrenheit(77.0)
	assert result['tr']['Grill'] == 0
	assert result['tr']['Food1'] == 0
	# TWC7 -> channel 8, which is beyond num_probes=3, so it's skipped
	# entirely and its pre-existing sentinel value is untouched.
	assert result['food']['Extra'] == -999
	assert result['tr']['Extra'] == -999
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: FAIL — `AttributeError: module 'probes.thermoworks_cloud' has no attribute 'ReadProbes'`

- [ ] **Step 3: Write the minimal implementation**

Add to `probes/thermoworks_cloud.py` (at the end of the file):

```python
class ReadProbes(ProbeInterface):
	def __init__(self, probe_info, device_info, units):
		config = device_info['config']
		self.email = config.get('email', '')
		self.password = config.get('password', '')
		self.device_serial = config.get('device_serial', '')
		self.num_probes = int(config.get('num_probes', 0))
		self.poll_interval = int(config.get('poll_interval', 30))
		super().__init__(probe_info, device_info, units)

	def _init_device(self):
		self.time_delay = 0
		self.device = ThermoworksCloudDevice(
			self.email, self.password, self.device_serial,
			self.num_probes, self.poll_interval,
		)
		self.device.start()

	def read_all_ports(self, output_data):
		for port in self.port_map:
			channel_number = int(port.replace('TWC', '')) + 1
			if channel_number > self.num_probes:
				continue  # unused port beyond this device's discovered channel count

			celsius = self.device.get_channel_celsius(channel_number)
			output_value = celsius if self.units == 'C' else self._to_fahrenheit(celsius)

			self.output_data['tr'][self.port_map[port]] = 0  # resistance NA

			if port == self.primary_port:
				self.output_data['primary'][self.port_map[port]] = output_value
			elif port in self.food_ports:
				self.output_data['food'][self.port_map[port]] = output_value
			elif port in self.aux_ports:
				self.output_data['aux'][self.port_map[port]] = output_value

		return self.output_data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add probes/thermoworks_cloud.py tests/test_thermoworks_cloud_probe.py
git commit -m "feat(thermoworks-cloud): wire ReadProbes._init_device and read_all_ports"
```

---

### Task 6: Wizard manifest entry

**Files:**
- Modify: `wizard/wizard_manifest.json`
- Test: `tests/test_thermoworks_cloud_probe.py`

**Interfaces:**
- Produces: `wizard_manifest.json`'s `modules.probes.thermoworks_cloud` entry, consumed by `probes/main.py`'s dynamic loader and by `blueprints/probeconfig/routes.py`'s `add_device` (copies `device_specific.ports` verbatim) and `render_probe_device_settings` (renders `device_specific.config`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_thermoworks_cloud_probe.py` (add `import json` and `import os` to the top imports):

```python
def test_wizard_manifest_has_thermoworks_cloud_entry():
	manifest_path = os.path.join(
		os.path.dirname(__file__), '..', 'wizard', 'wizard_manifest.json'
	)
	with open(manifest_path) as f:
		manifest = json.load(f)

	entry = manifest['modules']['probes']['thermoworks_cloud']

	assert entry['filename'] == 'thermoworks_cloud'
	assert entry['device_specific']['type'] == 'network'
	assert entry['device_specific']['ports'] == [
		'TWC0', 'TWC1', 'TWC2', 'TWC3', 'TWC4', 'TWC5', 'TWC6', 'TWC7',
	]
	assert 'thermoworks-cloud>=0.1.13' in entry['py_dependencies']

	labels = [item['label'] for item in entry['device_specific']['config']]
	assert labels == ['email', 'password', 'device_serial', 'num_probes', 'poll_interval']

	config_by_label = {item['label']: item for item in entry['device_specific']['config']}
	assert config_by_label['device_serial']['hidden'] is True
	assert config_by_label['num_probes']['hidden'] is True
	assert config_by_label['poll_interval']['default'] == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: FAIL — `KeyError: 'thermoworks_cloud'`

- [ ] **Step 3: Add the manifest entry**

In `wizard/wizard_manifest.json`, find this exact text (the end of the `bt_meater_exp` entry, immediately followed by the `prototype` entry — use this full block to anchor a unique match):

```json
              "default": "True",
              "hidden": true
            }
          ]
        }
      },
      "prototype": {
        "friendly_name": "Prototype",
```

Replace it with (inserting the new entry between the two):

```json
              "default": "True",
              "hidden": true
            }
          ]
        }
      },
      "thermoworks_cloud": {
        "friendly_name": "ThermoWorks Cloud",
        "filename": "thermoworks_cloud",
        "description": "This device module reads temperatures from a ThermoWorks Cloud-connected wireless thermometer (Signals, Smoke, Smoke X4, Node, RFX, BlueDOT, etc.) using your ThermoWorks account. Click Test Connection after entering your email/password to discover your devices.",
        "default": false,
        "image": "default.png",
        "reboot_required": false,
        "py_dependencies": ["thermoworks-cloud>=0.1.13"],
        "apt_dependencies": [],
        "command_list": [],
        "settings_dependencies": {
          "units": {
            "friendly_name": "Temp Units",
            "description": "Select the temperature units to use for PiFire globally.  (This can be modified in settings later)",
            "options": { "F": "Fahrenheit", "C": "Celsius" },
            "settings": ["globals", "units"]
          }
        },
        "device_specific": {
          "ports": ["TWC0", "TWC1", "TWC2", "TWC3", "TWC4", "TWC5", "TWC6", "TWC7"],
          "type": "network",
          "config": [
            {
              "label": "email",
              "friendly_name": "Email",
              "description": "The email address for your ThermoWorks Cloud account.",
              "type": "string",
              "default": "",
              "hidden": false
            },
            {
              "label": "password",
              "friendly_name": "Password",
              "description": "The password for your ThermoWorks Cloud account.",
              "type": "string",
              "default": "",
              "hidden": false
            },
            {
              "label": "device_serial",
              "friendly_name": "Device Serial",
              "description": "The serial number of the ThermoWorks device this entry reads from. This should be discovered by clicking Test Connection.",
              "type": "string",
              "default": "",
              "hidden": true
            },
            {
              "label": "num_probes",
              "friendly_name": "Number of Probes",
              "description": "The number of channels this ThermoWorks device has. This should be discovered by clicking Test Connection.",
              "type": "int",
              "default": 0,
              "hidden": true
            },
            {
              "label": "poll_interval",
              "friendly_name": "Poll Interval (seconds)",
              "description": "How often to check ThermoWorks Cloud for updated temperatures.",
              "type": "int",
              "default": 30,
              "min": 10,
              "max": 300,
              "step": 1,
              "hidden": false
            }
          ]
        }
      },
      "prototype": {
        "friendly_name": "Prototype",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Validate the manifest is still well-formed JSON**

Run: `python3 -c "import json; json.load(open('wizard/wizard_manifest.json'))" && echo OK`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add wizard/wizard_manifest.json tests/test_thermoworks_cloud_probe.py
git commit -m "feat(thermoworks-cloud): add wizard manifest entry"
```

---

### Task 7: Discovery AJAX route

**Files:**
- Modify: `blueprints/wizard/routes.py`

**Interfaces:**
- Consumes: `probes.thermoworks_cloud.discover(email, password)` (Task 4).
- Produces: `POST /wizard/thermoworks_discover` (form fields `email`, `password`, `serialID`, `numProbesID`) — returns a rendered HTML fragment (a `render_thermoworks_scan_table` macro, added in Task 8), matching the existing `bt_scan` action's response style (`blueprints/wizard/routes.py:71-99`).

No automated test for this task — per the design spec, the live network/auth call is an integration concern (same reasoning as the existing `bt_scan` action, which also has no test coverage). Verified manually in Task 9's end-to-end checklist.

- [ ] **Step 1: Add imports**

At the top of `blueprints/wizard/routes.py`, add:

```python
import asyncio
from probes.thermoworks_cloud import discover
from thermoworks_cloud import AuthenticationError
```

- [ ] **Step 2: Add the route action**

In `blueprints/wizard/routes.py`, immediately after the existing `bt_scan` block (ends at the line `return render_template_string(render_string, itemID=itemID, bt_data=bt_data, error=error)`, currently line 99) and before the `""" Create Temporary Probe Device/Port Structure..."""` comment, add:

```python
			if action == 'thermoworks_discover':
				email = r.get('email', '')
				password = r.get('password', '')
				serialID = r.get('serialID', '')
				numProbesID = r.get('numProbesID', '')
				tw_data = []
				error = None

				try:
					tw_data = asyncio.run(discover(email, password))
					if tw_data == []:
						error = 'No ThermoWorks Cloud devices found for this account.'
				except AuthenticationError as e:
					error = f'Could not log in to ThermoWorks Cloud: {e}'
				except Exception as e:
					error = f'Something bad happened: {e}'

				render_string = "{% from 'probeconfig/_macro_probes_config.html' import render_thermoworks_scan_table %}{{ render_thermoworks_scan_table(serialID, numProbesID, tw_data, error) }}"
				return render_template_string(
					render_string, serialID=serialID, numProbesID=numProbesID, tw_data=tw_data, error=error
				)
```

- [ ] **Step 3: Sanity-check the file still imports cleanly**

Run: `python3 -c "import ast; ast.parse(open('blueprints/wizard/routes.py').read())" && echo OK`
Expected: `OK`

(A full import check needs the Flask app context and isn't worth standing up here — Task 9's manual checklist exercises the real route.)

- [ ] **Step 4: Commit**

```bash
git add blueprints/wizard/routes.py
git commit -m "feat(thermoworks-cloud): add thermoworks_discover wizard AJAX route"
```

---

### Task 8: Wizard UI — "Test Connection" button, results table, JS

**Files:**
- Modify: `blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html`
- Modify: `blueprints/probeconfig/static/probeconfig/js/probeconfig.js`

**Interfaces:**
- Consumes: `POST /wizard/thermoworks_discover` (Task 7).
- Produces: config-field type `thermoworks_discover` special-cased by `label == 'device_serial'` in the per-setting render loop (mirrors the existing `probes_list` label-based special case at line 188); macro `render_thermoworks_scan_table(serialID, numProbesID, tw_data, error)`.

No automated test — Jinja macros and JS aren't unit-tested anywhere else in this codebase either. Verified manually in Task 9.

- [ ] **Step 1: Add the "Test Connection" input+button+modal macro**

In `blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html`, immediately after the existing `render_input_bt_address` macro (ends at line 529, right before `{% macro render_bt_scan_table(itemID, bt_data, error) %}` at line 531), add:

```html
{% macro render_input_thermoworks_discover(section, mode, label, default) %}

<div class="input-group mb-3">
    <input type="text" class="form-control deviceSpecific{{ mode }}"
    value="{{ default }}" aria-label="{{ label }}" readonly
    id="{{ section }}_devspec_{{ label }}"
    name="{{ section }}_devspec_{{ label }}"/>
    <div class="input-group-append">
        <button type="button" class="btn btn-success" id="tw_{{ section }}_devspec_{{ label }}_Discover" onclick="scanThermoworksDevices('{{ section }}_devspec_email', '{{ section }}_devspec_password', '{{ section }}_devspec_device_serial', '{{ section }}_devspec_num_probes')">Test Connection</button>
    </div>
</div>

<!-- Discover ThermoWorks Cloud Devices Modal -->
<div class="modal fade power-modal" id="tw_{{ section }}_devspec_{{ label }}_Modal" data-backdrop="false" tabindex="-1" aria-labelledby="tw_{{ section }}_devspec_{{ label }}_Label" aria-hidden="true" >
    <div class="modal-dialog modal-xl">
    <div class="modal-content">
        <div class="modal-header">
        <h5 class="modal-title" id="tw_{{ section }}_devspec_{{ label }}_Label">ThermoWorks Cloud Devices</h5>
        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
            <span aria-hidden="true">&times;</span>
        </button>
        </div>
        <div class="modal-body text-center">

            <div id="tw_{{ section }}_devspec_{{ label }}_Select">
                <br>
                <h4>Connecting...</h4>
                <br>
                <div class="fa-3x">
                    <i class="fa-solid fa-magnifying-glass fa-bounce"></i>
                </div>
                <br>
            </div>

        </div>
        <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-dismiss="modal">Close</button>
        <button type="button" class="btn btn-primary" value="" onclick="scanThermoworksDevices('{{ section }}_devspec_email', '{{ section }}_devspec_password', '{{ section }}_devspec_device_serial', '{{ section }}_devspec_num_probes')">Refresh</button>
        </div>
    </div>
    </div>
</div>

{% endmacro %}

{% macro render_thermoworks_scan_table(serialID, numProbesID, tw_data, error) %}
    {% if error %}
        <div class="alert alert-danger" role="alert">
            {{ error }}
        </div>
    {% else %}
        <table class="table">
            <thead class="thead-light">
                <tr>
                <th scope="col">Label</th>
                <th scope="col">Type</th>
                <th scope="col">Serial</th>
                <th scope="col">Channels</th>
                <th scope="col"></th>
                </tr>
            </thead>
            <tbody>
                {% for device in tw_data %}
                <tr>
                    <td class="align-middle">{{ device['label'] }}</td>
                    <td class="align-middle">{{ device['type'] }}</td>
                    <td class="align-middle">{{ device['serial'] }}</td>
                    <td class="align-middle">{{ device['num_channels'] }}</td>
                    <td class="align-middle">
                        <button type="button" class="btn btn-primary btn-sm" onclick="selectThermoworksDevice('{{ device['serial'] }}', {{ device['num_channels'] }}, '{{ serialID }}', '{{ numProbesID }}')">Select</button>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    {% endif %}
{% endmacro %}
```

- [ ] **Step 2: Wire the new config-field type into the render loop**

In the same file, in `render_probe_device_settings`, the per-setting loop currently reads (around line 194-216 — the `Edit` and `Add` branches each have an `{% if setting['type'] in ['float', 'int'] %} ... {% elif setting['type'] == 'bt_address' %} ... {% else %}` chain). Add a new branch for our special-cased label, mirroring how `probes_list` is special-cased by label at line 188. Find:

```html
                    {% if setting['label'] == 'probes_list' %} 
```

This is immediately preceded by `<td>` and the loop's opening `{% for setting in moduleData['device_specific']['config'] %}` block. Change it to also special-case `device_serial`:

```html
                    {% if setting['label'] == 'probes_list' %} 
```
to
```html
                    {% if setting['label'] == 'device_serial' %}
                        {% if mode == 'Edit' %}
                            {{ render_input_thermoworks_discover(moduleSection, mode, setting['label'], defaultConfig[setting['label']]) }}
                        {% else %}
                            {{ render_input_thermoworks_discover(moduleSection, mode, setting['label'], setting['default']) }}
                        {% endif %}
                    {% elif setting['label'] == 'probes_list' %} 
```

(Leave the rest of the `probes_list`/`float`/`int`/`list`/`bt_address`/`else` chain exactly as it is — this just adds one more branch ahead of it.)

- [ ] **Step 3: Add the JS functions**

In `blueprints/probeconfig/static/probeconfig/js/probeconfig.js`, immediately after the existing `selectBluetoothDevice` function (ends at line 33), add:

```js
//
// ThermoWorks Cloud Discovery Functions
//
function scanThermoworksDevices(emailID, passwordID, serialID, numProbesID) {
	const modal = '#tw_' + serialID + '_Modal';
	const modalContent = '#tw_' + serialID + '_Select';
	const email = $('#' + emailID).val();
	const password = $('#' + passwordID).val();
	$(modal).modal('show');
	// Show connecting text while discovering
	$(modalContent).html('<br> \
                <h4>Connecting...</h4> \
                <br> \
                <div class="fa-3x"> \
                    <i class="fa-solid fa-magnifying-glass fa-bounce"></i> \
                </div> \
                <br></br>');
	// Load the discovery results
	$(modalContent).load("/wizard/thermoworks_discover", {
		"email": email,
		"password": password,
		"serialID": serialID,
		"numProbesID": numProbesID,
	});
}

function selectThermoworksDevice(serial, numChannels, serialID, numProbesID) {
	const modal = '#tw_' + serialID + '_Modal';
	$('#' + serialID).val(serial);
	$('#' + numProbesID).val(numChannels);
	// Hide the modal
	$(modal).modal('hide');
}
```

- [ ] **Step 4: Sanity-check the JS file has no syntax errors**

Run: `node --check blueprints/probeconfig/static/probeconfig/js/probeconfig.js && echo OK`
Expected: `OK` (if `node` isn't available in this environment, skip this step and rely on Task 9's manual browser check instead)

- [ ] **Step 5: Commit**

```bash
git add blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html blueprints/probeconfig/static/probeconfig/js/probeconfig.js
git commit -m "feat(thermoworks-cloud): add Test Connection UI to the probe config wizard"
```

---

### Task 9: Full test suite + manual end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full new test file**

Run: `python3 -m pytest tests/test_thermoworks_cloud_probe.py -v`
Expected: all 11 tests PASS

- [ ] **Step 2: Run the pre-existing probe test files to confirm no regression**

Run: `python3 -m pytest tests/test_max31856_probe.py tests/test_mcp2210_probe_bus.py -v`
Expected: all previously-passing tests still PASS (this plan does not modify either of those files)

- [ ] **Step 3: Install the real dependency and confirm the module imports for real**

```bash
pip install thermoworks-cloud
python3 -c "import probes.thermoworks_cloud" && echo OK
```

Expected: `OK` — this catches any mismatch between the fake test module's shape and the real package's actual public API (e.g. if `AuthenticationError` were not actually exported from the top-level `thermoworks_cloud` package, this import would fail even though the mocked tests pass).

- [ ] **Step 4: Manual end-to-end check (requires a real ThermoWorks Cloud account)**

This step cannot be automated — it's the integration path the design spec explicitly calls out as manual-only.

1. Start the PiFire web app (`python3 app.py` or the project's normal dev-run command) and open the probe configuration page (Settings -> Probe Configuration, or the first-time wizard's probe step).
2. Click "Add Device", select "ThermoWorks Cloud" from the module dropdown.
3. Enter a real ThermoWorks Cloud account's email and password, then click "Test Connection".
4. Confirm the modal shows a table of your ThermoWorks devices with correct labels, types, and channel counts.
5. Click "Select" on one device; confirm the modal closes and the hidden `device_serial`/`num_probes` fields are populated (inspect via browser dev tools if not visually obvious).
6. Give the device a unique name and save it.
7. Go to the probe ports page, assign a couple of the new device's ports (e.g. `TWC0`, `TWC1`) to named probes (Primary/Food).
8. Confirm temperatures start appearing within `poll_interval` seconds of the physical ThermoWorks device having food/ambient probes plugged in, and that they roughly match what the ThermoWorks Cloud web app / mobile app shows.
9. Temporarily change the stored password to something wrong (edit the device), confirm the probe reports no data (`None`) rather than crashing, and that PiFire's logs show a `thermoworks_cloud: ...` error message on a ~60s cadence, not spamming every second.
10. Restore the correct password; confirm readings resume without restarting PiFire.

- [ ] **Step 5: Record the manual check result**

If Step 4 was performed, note in the PR description (or a commit trailer) which sub-steps were verified and which were skipped (e.g. "no real ThermoWorks device available; verified 1-6 with a live account, could not verify 7-10"). Do not claim full end-to-end verification unless all 10 sub-steps were actually run.
