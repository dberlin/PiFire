# ThermoWorks Cloud probe (`thermoworks_cloud`) — Design

**Date:** 2026-07-03
**Status:** Approved (design); implementation pending
**Author:** PiFire

## Goal

Add a probe module that reads temperatures from ThermoWorks Cloud-connected
wireless thermometers (Signals, Smoke, Smoke X4, Node, RFX, BlueDOT, etc.) via
the `thermoworks-cloud` PyPI package (`a2hill/python-thermoworks-cloud`). Unlike
every existing probe module, this one talks to a remote cloud API over HTTPS
rather than local hardware or a paired Bluetooth device, so it needs a
credential-based setup step and a network-polling background thread instead of
a direct sensor read.

## Background

### The `thermoworks-cloud` package

Confirmed by reading the package source (`a2hill/python-thermoworks-cloud` on
GitHub, version 0.1.13) — it is entirely `asyncio`-based and requires an
externally-provided `aiohttp.ClientSession`:

- `AuthFactory(websession).build_auth(email, password) -> Auth` — logs in with
  the user's ThermoWorks account email/password (no separate API key needed;
  the Firebase web config is fetched automatically). The returned `Auth`
  object auto-refreshes its access token internally (60s buffer before
  expiry), and is meant to be reused across many calls, not rebuilt per call.
- `ThermoworksCloud(auth)` — the client:
  - `async get_user() -> User` — `user.account_id` is needed for `get_devices`.
  - `async get_devices(account_id) -> List[Device]` — `Device.serial`,
    `Device.label`, `Device.type`, `Device.device_display_units` describe each
    physical unit tied to the account.
  - `async get_device_channel(device_serial, channel: str) -> DeviceChannel` —
    one probe channel's current reading. `DeviceChannel.value` (float),
    `.units` (`"F"`/`"C"`/`"H"`), `.label`, `.last_telemetry_saved`
    (`datetime`), `.status`.
  - Channels are 1-indexed and not otherwise enumerable — the library's own
    example (`examples/get_devices_for_user.py`) discovers a device's channel
    count by calling `get_device_channel` for `channel in range(1, 10)` until
    `ResourceNotFoundError`.
  - `AuthenticationError` (with `.reason` — `INVALID_EMAIL`, `EMAIL_NOT_FOUND`,
    `INVALID_PASSWORD`, `USER_DISABLED`, `UNKNOWN`) is raised on bad
    credentials.

### PiFire probe architecture (relevant precedent)

- Every probe module implements `ProbeInterface` (`probes/base.py:174`):
  `_init_device()` builds `self.device`, `read_all_ports(output_data)` is
  called every main-loop tick and must return promptly.
- Network/wireless probes (`probes/bt_meater.py`, `probes/bt_ibbq.py`) already
  establish the pattern this module follows: a background thread owns the
  actual I/O and caches the latest values; `read_all_ports()` only reads the
  cache and never blocks. Both skip the Steinhart-Hart voltage-to-temperature
  conversion (`probes/base.py:267`) since their source already returns
  calibrated temperatures — this module does the same, writing `0` to the
  `tr` (resistance) output slot like `bt_meater.py:480` and
  `mcp9600_adafruit.py`.
- `device_specific.ports` in `wizard_manifest.json` is a **fixed list per
  manifest entry** — confirmed by `blueprints/probeconfig/routes.py:93`,
  where `add_device` copies `ports` verbatim from the manifest, with no
  per-instance customization. `bt_ibbq`/`bt_ibt6xs` work around device
  variability by shipping two manifest entries with different fixed port
  counts, plus a `num_probes` config field that tells the module how many of
  those ports are actually active for a given physical unit
  (`probes/bt_ibbq.py:18`). This module reuses that same `num_probes`
  approach — a discovered channel count, not a second manifest entry, since
  channel count varies continuously up to a reasonable max.
- `device_specific.type` is a descriptive label only — confirmed unused for
  branching anywhere in `blueprints/` or `probes/` (only `.config` and
  `.ports` are read programmatically). A new value `"network"` is safe to
  introduce.
- Device config fields with `"hidden": true` (e.g. `transient` in
  `bt_meater`/`bt_ibbq`) are already a supported, unremarkable pattern for
  values a script fills in rather than the user typing directly.
- Credentials-as-plaintext-config has direct precedent: the MQTT notification
  service's password field is a plain, unmasked `type="text"` input
  (`blueprints/settings/templates/settings/index.html:1770`), stored as
  plaintext in settings/config. This module's `email`/`password` fields follow
  the same convention — no new encrypted-storage mechanism is introduced.
- Bluetooth device scanning goes through a system-command queue to the
  control-loop process (`blueprints/wizard/routes.py:71`,
  `grillplat/raspberry_pi_all.py:338`) because it needs real radio hardware
  access. This module's "discovery" is a plain HTTPS call, so it does **not**
  need that queue — it runs directly, synchronously (via `asyncio.run()`), in
  the Flask web process handling the wizard/probeconfig request.
- `probes/base.py:391,397` (`_to_fahrenheit`/`_to_celsius`) are the existing
  unit-conversion helpers, used by `bt_meater.py:474` — this module uses the
  same helpers rather than inventing new conversion logic.

## Architecture

### New file `probes/thermoworks_cloud.py`

```python
"""
PiFire ThermoWorks Cloud Module

  device_info = {
      'device': 'your_device_name',
      'module': 'thermoworks_cloud',
      'ports': ['TWC0', ..., 'TWC7'],   # fixed manifest list; only the first
                                         # num_probes are actually read
      'config': {
          'email': 'user@example.com',
          'password': 'plaintext, like the MQTT password field',
          'device_serial': '...',       # filled in by the discovery step
          'num_probes': 4,              # filled in by the discovery step
          'poll_interval': 30,          # seconds between cloud polls
      }
  }
"""

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone

from thermoworks_cloud import AuthFactory, ThermoworksCloud, ResourceNotFoundError
from aiohttp import ClientSession, ClientError

from probes.base import ProbeInterface

_STALE_MULTIPLIER = 3  # a channel's cached reading is considered stale (-> None)
                        # after this many missed poll intervals


async def poll_once(client, device_serial, num_probes):
    """Fetch channels 1..num_probes for one device. Pure/no-thread — the unit
    under test. Returns {channel_number: DeviceChannel-or-None}."""
    results = {}
    for channel in range(1, num_probes + 1):
        try:
            results[channel] = await client.get_device_channel(device_serial, str(channel))
        except ResourceNotFoundError:
            results[channel] = None
    return results


class ThermoworksCloudDevice:
    """Owns the background thread, the persistent asyncio loop, and the
    cache of last-known channel readings."""

    def __init__(self, email, password, device_serial, num_probes, poll_interval):
        self.email = email
        self.password = password
        self.device_serial = device_serial
        self.num_probes = num_probes
        self.poll_interval = poll_interval
        self.logger = logging.getLogger("control")

        self._cache = {}   # {channel_number: (value_C, last_telemetry_saved)}
        self._lock = threading.Lock()
        self.status = {'connected': False, 'last_error': None, 'last_poll_time': None}

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
                                    self._cache[channel] = (data, now)
                        self.status['last_poll_time'] = now
                        await asyncio.sleep(self.poll_interval)
            except Exception as exc:  # AuthenticationError, ClientError, etc.
                self.status['connected'] = False
                self.status['last_error'] = str(exc)
                self.logger.error(f'thermoworks_cloud: {exc}')
                await asyncio.sleep(max(self.poll_interval, 60))  # backoff, then re-login

    def get_channel_value_and_units(self, channel_number, units):
        """Returns a temperature in the requested units, or None if missing/stale."""
        with self._lock:
            entry = self._cache.get(channel_number)
        if entry is None:
            return None
        data, fetched_at = entry
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if age > self.poll_interval * _STALE_MULTIPLIER:
            return None
        value = data.value
        if data.units == 'C' and units == 'F':
            value = value * 9 / 5 + 32
        elif data.units == 'F' and units == 'C':
            value = (value - 32) * 5 / 9
        return value

    def get_status(self):
        return self.status


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

    def read_all_ports(self, output_data):
        for index, port in enumerate(self.port_map):
            channel_number = index + 1
            if channel_number > self.num_probes:
                continue  # unused port beyond this device's discovered channel count
            output_value = self.device.get_channel_value_and_units(channel_number, self.units)

            self.output_data['tr'][self.port_map[port]] = 0  # resistance NA

            if port == self.primary_port:
                self.output_data['primary'][self.port_map[port]] = output_value
            elif port in self.food_ports:
                self.output_data['food'][self.port_map[port]] = output_value
            elif port in self.aux_ports:
                self.output_data['aux'][self.port_map[port]] = output_value

        return self.output_data

    def get_device_info(self):
        info = super().get_device_info()
        info.update(self.device.get_status())
        return info
```

Notes on the sketch above:
- `poll_once()` is deliberately free of threading/sleep/logging so it is the
  direct unit-test target (see Testing).
- The outer `while True` in `_main()` re-creates the `ClientSession`/`Auth`
  only after an exception (bad password, network drop, etc.) — the normal
  path reuses one session/auth for the process lifetime, per the package's
  intended usage.
- Ports beyond `num_probes` are simply skipped in `read_all_ports` — they
  never appear in `output_data`, the same effective behavior as `bt_ibbq`
  leaving unused ports alone when `num_probes` is less than the manifest max.

### Wizard manifest entry (`modules.probes.thermoworks_cloud`)

- `friendly_name`: "ThermoWorks Cloud"
- `filename`: `thermoworks_cloud`
- `type`: `"network"` (new descriptive value; confirmed unused for branching)
- `ports`: `["TWC0","TWC1","TWC2","TWC3","TWC4","TWC5","TWC6","TWC7"]` (8 — a
  generous max; the largest known consumer multi-channel devices, e.g.
  Signals/Smoke X4, have 4)
- `py_dependencies`: `["thermoworks-cloud>=0.1.13"]` (`aiohttp` is pulled in
  transitively)
- `device_specific.config`, in order:
  1. `email` — `type: "string"`, default `""`.
  2. `password` — `type: "string"`, default `""` (plaintext, matching the
     MQTT password field precedent — no masking/encryption).
  3. `device_serial` — `type: "string"`, default `""`, `"hidden": true` (set
     by the discovery step's JS, not typed by hand).
  4. `num_probes` — `type: "int"`, default `0`, `"hidden": true` (set by the
     discovery step's JS).
  5. `poll_interval` — `type: "int"`, default `30`, min `10`, max `300`
     (user-editable, per design decision).

### Discovery ("Test Connection") flow

1. New AJAX endpoint (e.g. `blueprints/probeconfig/routes.py`, action
   `thermoworks_discover`), which:
   - Reads `email`/`password` from the POST body.
   - Runs `asyncio.run(_discover(email, password))` where `_discover`:
     opens an `aiohttp.ClientSession`, `AuthFactory(session).build_auth(...)`,
     `client.get_user()` → `account_id`, `client.get_devices(account_id)`,
     then for each device, probes channels `1..9` (stopping at the first
     `ResourceNotFoundError`) to get a channel count.
   - Returns JSON: `[{"serial": ..., "label": ..., "type": ..., "num_channels": ...}, ...]`,
     or an error message (e.g. on `AuthenticationError`).
   - No system-command queue involved — this runs directly in the Flask
     process, since it is a plain HTTPS call, not hardware access.
2. New template block (in
   `blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html`,
   alongside the existing `render_bt_scan_table`) renders the JSON result as a
   picklist.
3. Selecting a device fills the hidden `device_serial`/`num_probes` fields via
   JS before the existing `add_config`/`add_device` form submit. No changes
   to `routes.py`'s `add_device`/`edit_device` handlers — `ports` still comes
   from the static manifest list, as it does for every other module today.

## Config shape (example stored device)

```json
{
  "device": "SignalsGrill",
  "module": "thermoworks_cloud",
  "module_filename": "thermoworks_cloud",
  "ports": ["TWC0", "TWC1", "TWC2", "TWC3", "TWC4", "TWC5", "TWC6", "TWC7"],
  "config": {
    "email": "user@example.com",
    "password": "hunter2",
    "device_serial": "TW-ABC123",
    "num_probes": 4,
    "poll_interval": 30
  }
}
```

## Error handling

- **Bad credentials at setup time**: the discovery endpoint surfaces
  `AuthenticationError` as a form alert (mirroring the existing
  `add_device` alert pattern in `routes.py:73-84`), no device is added.
- **Bad credentials discovered later** (e.g. the user changed their
  ThermoWorks password): `_main()`'s outer exception handler catches it,
  sets `status['connected'] = False` / `status['last_error']`, and retries
  login on a backoff instead of killing the thread. `get_device_info()`
  surfaces this so the UI can show a disconnected/error state, mirroring
  `Meater_Device.get_status()`.
- **Missing channel** (`ResourceNotFoundError`): that channel's cached value
  is simply never populated / ages out to `None` — not a fatal error.
- **Network errors / timeouts** (`ClientError`): caught by the same outer
  handler; last-known-good cached values are still served by
  `get_channel_value_and_units` until they exceed the staleness threshold
  (`poll_interval * 3`), at which point ports report `None` (handled
  gracefully by the existing framework, `probes/base.py:269`), rather than
  serving a frozen reading indefinitely.
- **Thread never dies on error** — every exception path inside `_main()`'s
  outer loop is caught, logged, and retried; only a `KeyError`/crash during
  process init (e.g. missing config keys) would surface at `_init_device()`
  time, consistent with other probe modules' misconfiguration-only failure
  paths.

## Testing (network-free)

`thermoworks_cloud`/`aiohttp` are not required to be installed for unit tests;
tests inject fakes, mirroring the existing hardware-fake approach
(`tests/test_max31856_probe.py`):

- **`poll_once` mapping** (new `tests/test_thermoworks_cloud_probe.py`): a
  fake `client` object with an async `get_device_channel(serial, channel)`
  that returns canned `DeviceChannel`-like objects for some channels and
  raises a fake `ResourceNotFoundError` for others; assert `poll_once` returns
  the right `{channel: data-or-None}` mapping, driven via `asyncio.run()`.
- **Unit conversion**: feed `get_channel_value_and_units` a cached `'C'`
  reading and request `'F'` (and vice versa); assert the converted value.
- **Staleness**: a cached entry older than `poll_interval * 3` returns `None`
  from `get_channel_value_and_units`; a fresh one returns the value.
- **`read_all_ports` port mapping**: build a bare `ReadProbes` (`__new__`),
  inject a fake `self.device` whose `get_channel_value_and_units` returns
  fixed per-channel values, set `num_probes` less than the full 8-port
  manifest list, and assert only the first `num_probes` ports appear in
  `output_data`, with the `tr` slot as `0`.
- **Manifest sanity**: assert the `thermoworks_cloud` entry exists with
  `type == 'network'`, 8 `ports`, config labels include
  `email`/`password`/`device_serial`/`num_probes`/`poll_interval`, and
  `py_dependencies` includes `thermoworks-cloud`.
- **Not covered by automated tests**: the discovery AJAX endpoint's live
  network call and the real ThermoWorks Cloud API's response shapes — this
  is an integration concern. Manual verification against a real ThermoWorks
  account is the only way to validate the discovery flow end-to-end; note
  this explicitly rather than claiming full coverage.

## Files changed

| File | Change |
|------|--------|
| `probes/thermoworks_cloud.py` (new) | The probe module above. |
| `wizard/wizard_manifest.json` | Add the `thermoworks_cloud` entry. |
| `blueprints/probeconfig/routes.py` | Add the discovery AJAX action. |
| `blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html` | Add the "Test Connection" button + discovered-device picklist block. |
| `pyproject.toml` | Add `thermoworks-cloud>=0.1.13` dependency. |
| `auto-install/requirements.txt` | Add `thermoworks-cloud==0.1.13`. |
| `tests/test_thermoworks_cloud_probe.py` (new) | Unit tests described above. |

## Out of scope

- **Historical archive data** (`list_device_archives`/`get_archive`) — this
  module only reads live/current channel values, not historical cook
  archives.
- **Multiple physical devices sharing one login without re-entering
  credentials** — each PiFire device entry stores its own copy of
  email/password, matching the existing one-entry-per-physical-unit pattern
  (Meater, iBBQ). Users with multiple ThermoWorks units add multiple PiFire
  device entries.
- **Masked/encrypted credential storage** — plaintext, matching the existing
  MQTT password convention; not introducing new security posture in this
  change.
- **Alarms/min-max/battery reporting** (`DeviceChannel.alarm_high/low`,
  `Device.battery`) — only the current temperature reading is surfaced;
  richer status fields are not wired into PiFire's UI in this first cut.
- **Configurable staleness multiplier** — hardcoded at `poll_interval * 3`;
  not exposed as a wizard config field.
