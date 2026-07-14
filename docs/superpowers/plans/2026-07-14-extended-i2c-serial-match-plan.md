# Extended I2C Bus Matching by USB Serial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the `extended` I2C bus kind select an adapter by its USB iSerial (`i2c_bus_num = "serial:<ISERIAL>"`), and fix the `i2c_bus_num` field everywhere it appears (probe devices, distance sensor, fan controller) from a fixed-option dropdown into free text with a discovery-backed "Discover" button, for all three USB-capable kinds (`extended`, `ft232h`, `mcp2221`).

**Architecture:** `common/i2c_bus.py` gains a sysfs USB-ancestor walk (`_read_usb_serial`), a shared adapter-enumeration helper, `find_i2c_bus_by_serial`, a `serial:` selector prefix in `resolve_i2c_bus`, and three best-effort discovery helpers. A new `/wizard` route action (`i2c_bus_scan`) mirrors the existing `bt_scan`/`thermoworks_discover` pattern: it calls the matching discovery helper and returns an HTML fragment. Two wizard-rendering templates (`_macro_probes_config.html` for probe devices, `_macro_wizard_card.html` for distance sensor / fan controller) get a new shared input+Discover-button macro wired to that route via a new JS pair (`scanI2CBus`/`selectI2CBus`), replacing the strict `<select>` both templates render for `i2c_bus_num` today.

**Tech Stack:** Python (Flask, Jinja2), `hid` (MCP2221 HID enumeration), `pyftdi` (FT232H enumeration), pytest, jQuery (existing wizard JS).

## Global Constraints

- Every existing stored config value (plain bus numbers, `CP2112`/`MCP2221` name matches, existing ft232h URLs, existing mcp2221 serials) must keep working unchanged — no settings migration.
- Not every `extended` adapter is USB-backed (e.g. a Pi's onboard I2C). `serial:` matching only applies to adapters where a USB ancestor is found; others are unaffected and keep working via numeric/name matching.
- All new discovery helpers are best-effort: a missing optional dependency, a permission error, or zero devices present must resolve to `[]`, never raise, so the wizard page never breaks because of a Discover click.
- Serial matching is **exact**, not substring (unlike the existing adapter-name match).
- Follow this repo's existing test conventions: `tests/test_i2c_bus.py` for `common/i2c_bus.py` unit tests (fake backends injected via `mock.patch.dict('sys.modules', ...)`, sysfs behavior via `tmp_path`), `tests/test_webapp_sqlite.py` for Flask route-level tests (it already boots a real `app.test_client()` against a seeded SQLite DB).
- Run tests via `uv run pytest tests/ -q` (per this repo's project convention: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/`). Run `uvx ruff format <changed files>` before every commit.

---

### Task 1: Sysfs USB-ancestor serial resolution

**Files:**
- Modify: `common/i2c_bus.py`
- Test: `tests/test_i2c_bus.py`

**Interfaces:**
- Produces: `_read_usb_serial(bus_dir, max_hops=15) -> str | None` — used by Task 2's `_enumerate_i2c_adapters`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_i2c_bus.py` (near the existing `test_find_i2c_bus_debug_logs_match_and_result` test):

```python
def test_read_usb_serial_resolves_via_sysfs_walk(tmp_path):
	usb_device = tmp_path / 'devices' / 'usb1' / '1-1'
	usb_device.mkdir(parents=True)
	(usb_device / 'serial').write_text('AB12\n')
	(usb_device / 'idVendor').write_text('04d8\n')
	iface = usb_device / '1-1:1.0'
	iface.mkdir()
	bus_dir = iface / 'i2c-7'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text('MCP2221 usb-i2c bridge\n')

	assert i2c_bus._read_usb_serial(str(bus_dir)) == 'AB12'


def test_read_usb_serial_returns_none_without_usb_ancestor(tmp_path):
	bus_dir = tmp_path / 'i2c-1'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text('bcm2835 I2C adapter\n')

	assert i2c_bus._read_usb_serial(str(bus_dir)) is None


def test_read_usb_serial_ignores_serial_file_without_idvendor(tmp_path):
	# A directory with a 'serial' file but no 'idVendor' isn't a USB device
	# level (e.g. a power_supply sysfs node) -- must not be mistaken for one.
	not_usb = tmp_path / 'not_a_usb_device'
	not_usb.mkdir()
	(not_usb / 'serial').write_text('DECOY\n')
	bus_dir = not_usb / 'i2c-2'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text('some adapter\n')

	assert i2c_bus._read_usb_serial(str(bus_dir)) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_i2c_bus.py -k read_usb_serial -v`
Expected: FAIL with `AttributeError: module 'common.i2c_bus' has no attribute '_read_usb_serial'`

- [ ] **Step 3: Implement `_read_usb_serial`**

Add to `common/i2c_bus.py`, directly above `def find_i2c_bus(...)`:

```python
def _read_usb_serial(bus_dir, max_hops=15):
	"""Return the USB iSerial of `bus_dir`'s (an i2c-N sysfs directory) USB
	ancestor, or None if it has none within `max_hops` parent directories (a
	non-USB adapter, e.g. a Pi's onboard I2C). Requires the ancestor to have
	both a 'serial' and an 'idVendor' file -- the USB *device* level in sysfs,
	as opposed to an interface level or an unrelated subsystem node that might
	also expose a 'serial' file (e.g. power_supply)."""
	current = os.path.realpath(bus_dir)
	for _ in range(max_hops):
		parent = os.path.dirname(current)
		if parent == current:
			return None
		current = parent
		serial_path = os.path.join(current, 'serial')
		vendor_path = os.path.join(current, 'idVendor')
		if os.path.isfile(serial_path) and os.path.isfile(vendor_path):
			try:
				with open(serial_path) as handle:
					return handle.read().strip()
			except OSError:
				return None
	return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_i2c_bus.py -k read_usb_serial -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format common/i2c_bus.py tests/test_i2c_bus.py
git add common/i2c_bus.py tests/test_i2c_bus.py
git commit -m "$(cat <<'EOF'
feat(i2c): add sysfs USB-ancestor serial resolution

_read_usb_serial walks up from an i2c-N sysfs directory to find its USB
device (identified by a co-located serial + idVendor file), the primitive
needed to match an extended I2C bus by iSerial instead of adapter name.
EOF
)"
```

---

### Task 2: Shared adapter enumeration, `find_i2c_bus` regression-safe refactor

**Files:**
- Modify: `common/i2c_bus.py:62-94` (existing `find_i2c_bus`)
- Test: `tests/test_i2c_bus.py`

**Interfaces:**
- Consumes: `_read_usb_serial(bus_dir)` from Task 1.
- Produces: `_enumerate_i2c_adapters(devices_path='/sys/bus/i2c/devices') -> list[{'bus_num': int, 'name': str, 'serial': str | None}]` — used by Task 3's `find_i2c_bus_by_serial` and Task 4's `discover_extended_i2c_buses`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_i2c_bus.py`:

```python
def test_enumerate_i2c_adapters_includes_serial(tmp_path):
	usb_device = tmp_path / 'devices' / 'usb1' / '1-1'
	usb_device.mkdir(parents=True)
	(usb_device / 'serial').write_text('AB12')
	(usb_device / 'idVendor').write_text('04d8')
	devices_dir = usb_device / '1-1:1.0'
	devices_dir.mkdir()
	bus_dir = devices_dir / 'i2c-7'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text('MCP2221 usb-i2c bridge')

	adapters = i2c_bus._enumerate_i2c_adapters(devices_path=str(devices_dir))
	assert adapters == [{'bus_num': 7, 'name': 'MCP2221 usb-i2c bridge', 'serial': 'AB12'}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_i2c_bus.py -k enumerate_i2c_adapters -v`
Expected: FAIL with `AttributeError: module 'common.i2c_bus' has no attribute '_enumerate_i2c_adapters'`

- [ ] **Step 3: Refactor**

Replace the body of `find_i2c_bus` in `common/i2c_bus.py` (currently lines 62-94) with:

```python
def _enumerate_i2c_adapters(devices_path='/sys/bus/i2c/devices'):
	"""Return [{'bus_num': int, 'name': str, 'serial': str | None}, ...] for
	every i2c-dev adapter under devices_path. 'serial' is the USB iSerial of
	the adapter's USB ancestor (via _read_usb_serial), or None if it has none
	(e.g. an onboard/non-USB adapter)."""
	adapters = []
	for bus_dir in glob.glob(os.path.join(devices_path, 'i2c-*')):
		try:
			with open(os.path.join(bus_dir, 'name')) as handle:
				name = handle.read().strip()
		except OSError:
			continue
		try:
			bus_num = int(os.path.basename(bus_dir).split('-')[-1])
		except ValueError:
			continue
		adapters.append({'bus_num': bus_num, 'name': name, 'serial': _read_usb_serial(bus_dir)})
	return adapters


def find_i2c_bus(match, devices_path='/sys/bus/i2c/devices'):
	"""
	Return the integer i2c bus number whose adapter name contains `match`
	(case-insensitive), e.g. 'CP2112' for a USB-to-I2C bridge. Scans
	`<devices_path>/i2c-*/name`. Raises RuntimeError if zero or more than one
	adapter matches, so the caller fails clearly rather than guessing.
	"""
	match_lower = str(match).lower()
	adapters = _enumerate_i2c_adapters(devices_path)

	found = [a['bus_num'] for a in adapters if match_lower in a['name'].lower()]
	available = (
		', '.join(f'i2c-{a["bus_num"]} ({a["name"]!r})' for a in sorted(adapters, key=lambda a: a['bus_num']))
		or '(none)'
	)
	logger.debug('find_i2c_bus: matching %r among adapters: %s', match, available)
	if len(found) == 1:
		logger.debug('find_i2c_bus: %r matched i2c-%d', match, found[0])
		return found[0]
	if not found:
		raise RuntimeError(
			f'No i2c adapter found matching {match!r} under {devices_path}. Available adapters: {available}'
		)
	raise RuntimeError(f'Multiple i2c adapters match {match!r}: {sorted(found)}. Available adapters: {available}')
```

- [ ] **Step 4: Run tests to verify they pass, including existing regression tests**

Run: `uv run pytest tests/test_i2c_bus.py tests/test_i2c_bridge_match_manifest.py -v`
Expected: PASS, all tests including `test_find_i2c_bus_debug_logs_match_and_result` and `test_find_i2c_bus_matches_mcp2221_adapter` (both pre-existing, must still pass unchanged).

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format common/i2c_bus.py tests/test_i2c_bus.py
git add common/i2c_bus.py tests/test_i2c_bus.py
git commit -m "$(cat <<'EOF'
refactor(i2c): factor adapter enumeration out of find_i2c_bus

_enumerate_i2c_adapters is the single place that globs /sys/bus/i2c/devices
and reads each adapter's name + USB serial, so find_i2c_bus and the
upcoming serial-based matcher don't duplicate the sysfs walk.
EOF
)"
```

---

### Task 3: `find_i2c_bus_by_serial` + `resolve_i2c_bus` serial: dispatch

**Files:**
- Modify: `common/i2c_bus.py:97-109` (existing `resolve_i2c_bus`)
- Test: `tests/test_i2c_bus.py`

**Interfaces:**
- Consumes: `_enumerate_i2c_adapters` from Task 2.
- Produces: `find_i2c_bus_by_serial(serial, devices_path='/sys/bus/i2c/devices') -> int`; `resolve_i2c_bus` now accepts `"serial:<ISERIAL>"` in addition to its existing numeric/name-match forms.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_i2c_bus.py`:

```python
def _make_usb_i2c_adapter(root, usb_name, serial, bus_num, adapter_name, devices_dir):
	usb_dev = root / usb_name
	usb_dev.mkdir(parents=True)
	(usb_dev / 'serial').write_text(serial)
	(usb_dev / 'idVendor').write_text('04d8')
	iface = usb_dev / f'{usb_name}:1.0'
	iface.mkdir()
	bus_dir = iface / f'i2c-{bus_num}'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text(adapter_name)
	(devices_dir / f'i2c-{bus_num}').symlink_to(bus_dir)


def test_find_i2c_bus_by_serial_matches(tmp_path):
	devices_dir = tmp_path / 'devices_path'
	devices_dir.mkdir()
	_make_usb_i2c_adapter(tmp_path, 'usb1', 'AB12', 7, 'MCP2221 usb-i2c bridge', devices_dir)

	assert i2c_bus.find_i2c_bus_by_serial('AB12', devices_path=str(devices_dir)) == 7


def test_find_i2c_bus_by_serial_no_match_raises(tmp_path):
	devices_dir = tmp_path / 'devices_path'
	devices_dir.mkdir()
	_make_usb_i2c_adapter(tmp_path, 'usb1', 'AB12', 7, 'MCP2221 usb-i2c bridge', devices_dir)

	with pytest.raises(RuntimeError, match='No i2c adapter found with serial'):
		i2c_bus.find_i2c_bus_by_serial('DEADBEEF', devices_path=str(devices_dir))


def test_find_i2c_bus_by_serial_ambiguous_raises(tmp_path):
	devices_dir = tmp_path / 'devices_path'
	devices_dir.mkdir()
	_make_usb_i2c_adapter(tmp_path, 'usb1', 'AB12', 1, 'MCP2221 usb-i2c bridge', devices_dir)
	_make_usb_i2c_adapter(tmp_path, 'usb2', 'AB12', 2, 'MCP2221 usb-i2c bridge', devices_dir)

	with pytest.raises(RuntimeError, match='Multiple i2c adapters have serial'):
		i2c_bus.find_i2c_bus_by_serial('AB12', devices_path=str(devices_dir))


def test_find_i2c_bus_by_serial_is_exact_not_substring(tmp_path):
	devices_dir = tmp_path / 'devices_path'
	devices_dir.mkdir()
	_make_usb_i2c_adapter(tmp_path, 'usb1', 'AB1234', 7, 'MCP2221 usb-i2c bridge', devices_dir)

	with pytest.raises(RuntimeError, match='No i2c adapter found with serial'):
		i2c_bus.find_i2c_bus_by_serial('AB12', devices_path=str(devices_dir))


def test_resolve_i2c_bus_serial_prefix_dispatches(monkeypatch):
	monkeypatch.setattr(i2c_bus, 'find_i2c_bus_by_serial', lambda serial: 42 if serial == 'AB12' else None)
	assert resolve_i2c_bus('serial:AB12') == 42
	assert resolve_i2c_bus('SERIAL:AB12') == 42  # prefix keyword is case-insensitive
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_i2c_bus.py -k "find_i2c_bus_by_serial or serial_prefix" -v`
Expected: FAIL with `AttributeError: module 'common.i2c_bus' has no attribute 'find_i2c_bus_by_serial'`

- [ ] **Step 3: Implement**

Add to `common/i2c_bus.py`, directly below `find_i2c_bus`:

```python
def find_i2c_bus_by_serial(serial, devices_path='/sys/bus/i2c/devices'):
	"""
	Return the integer i2c bus number whose adapter's USB iSerial exactly
	equals `serial` (case-sensitive, no substring matching -- a serial is
	meant to be unambiguous). Raises RuntimeError if zero or more than one
	adapter matches, listing every available adapter (with its serial, if
	any) so the error is actionable without a second lookup.
	"""
	target = str(serial)
	adapters = _enumerate_i2c_adapters(devices_path)

	found = [a['bus_num'] for a in adapters if a['serial'] == target]
	available = (
		', '.join(
			f'i2c-{a["bus_num"]} (serial={a["serial"]!r})' for a in sorted(adapters, key=lambda a: a['bus_num'])
		)
		or '(none)'
	)
	logger.debug('find_i2c_bus_by_serial: matching %r among adapters: %s', serial, available)
	if len(found) == 1:
		logger.debug('find_i2c_bus_by_serial: %r matched i2c-%d', serial, found[0])
		return found[0]
	if not found:
		raise RuntimeError(
			f'No i2c adapter found with serial {serial!r} under {devices_path}. Available adapters: {available}'
		)
	raise RuntimeError(f'Multiple i2c adapters have serial {serial!r}: {sorted(found)}. Available adapters: {available}')
```

Replace `resolve_i2c_bus` (currently lines 97-109) with:

```python
def resolve_i2c_bus(bus):
	"""
	Resolve an extended-i2c-bus spec to a bus number. Accepts an int or numeric
	string (e.g. 3 / '3' -> /dev/i2c-3, used directly), a 'serial:<ISERIAL>'
	USB-serial match (e.g. 'serial:0012AB34' -> discovered via
	find_i2c_bus_by_serial, the only way to distinguish two identical USB-to-I2C
	bridges), or an adapter-name match string (e.g. 'CP2112' -> discovered via
	find_i2c_bus, robust against the dynamic bus numbers USB-to-I2C bridges get).
	"""
	spec = str(bus).strip()
	if spec.lower().startswith('serial:'):
		serial = spec.split(':', 1)[1].strip()
		logger.debug('resolve_i2c_bus: %r is a USB-serial match, discovering the bus number', bus)
		return find_i2c_bus_by_serial(serial)
	if spec.isdigit():
		logger.debug('resolve_i2c_bus: %r is a numeric bus -> /dev/i2c-%s', bus, spec)
		return int(spec)
	logger.debug('resolve_i2c_bus: %r is an adapter-name match, discovering the bus number', bus)
	return find_i2c_bus(spec)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_i2c_bus.py -v`
Expected: PASS (all tests in the file, including pre-existing ones)

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format common/i2c_bus.py tests/test_i2c_bus.py
git add common/i2c_bus.py tests/test_i2c_bus.py
git commit -m "$(cat <<'EOF'
feat(i2c): match the extended bus by USB serial via serial:<ISERIAL>

Two identical USB-to-I2C bridges (e.g. two MCP2221A units bound to the
kernel hid-mcp2221 driver) report the same adapter name, so the existing
name-match selector can't tell them apart. resolve_i2c_bus now also accepts
serial:<ISERIAL>, matched exactly against each adapter's USB iSerial.
EOF
)"
```

---

### Task 4: Discovery helpers (extended / mcp2221 / ft232h)

**Files:**
- Modify: `common/i2c_bus.py`
- Test: `tests/test_i2c_bus.py`

**Interfaces:**
- Consumes: `_enumerate_i2c_adapters` from Task 2.
- Produces: `discover_extended_i2c_buses(devices_path='/sys/bus/i2c/devices') -> list[{'bus_num', 'name', 'serial'}]`; `discover_mcp2221_devices() -> list[{'serial', 'path'}]`; `discover_ft232h_devices() -> list[{'url', 'serial', 'description'}]`. All used by Task 5's wizard route action.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_i2c_bus.py`:

```python
def test_discover_extended_i2c_buses_wraps_enumeration(tmp_path):
	usb_device = tmp_path / 'devices' / 'usb1' / '1-1'
	usb_device.mkdir(parents=True)
	(usb_device / 'serial').write_text('AB12')
	(usb_device / 'idVendor').write_text('04d8')
	iface = usb_device / '1-1:1.0'
	iface.mkdir()
	bus_dir = iface / 'i2c-7'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text('MCP2221 usb-i2c bridge')

	assert i2c_bus.discover_extended_i2c_buses(devices_path=str(iface)) == [
		{'bus_num': 7, 'name': 'MCP2221 usb-i2c bridge', 'serial': 'AB12'}
	]


def test_discover_extended_i2c_buses_empty_when_missing_path():
	assert i2c_bus.discover_extended_i2c_buses(devices_path='/no/such/path') == []


def test_discover_mcp2221_devices_lists_serials():
	modules, handle, ctor = _fake_mcp2221_modules(
		enumerate_result=[
			{'serial_number': 'AAAA', 'path': b'/dev/hidraw0'},
			{'serial_number': 'BBBB', 'path': b'/dev/hidraw1'},
		]
	)
	with mock.patch.dict('sys.modules', modules):
		devices = i2c_bus.discover_mcp2221_devices()
	assert devices == [
		{'serial': 'AAAA', 'path': b'/dev/hidraw0'},
		{'serial': 'BBBB', 'path': b'/dev/hidraw1'},
	]


def test_discover_mcp2221_devices_empty_without_hid_module():
	with mock.patch.dict('sys.modules', {'hid': None}):
		assert i2c_bus.discover_mcp2221_devices() == []


def test_discover_ft232h_devices_lists_urls():
	descriptor = types_module_with(sn='FT9', description='Single RS232-HS')

	class FakeFtdi:
		@staticmethod
		def list_devices(url):
			return [(descriptor, 1)]

	fake_mod = types_module_with(Ftdi=FakeFtdi)
	with mock.patch.dict('sys.modules', {'pyftdi.ftdi': fake_mod}):
		devices = i2c_bus.discover_ft232h_devices()
	assert devices == [{'url': 'ftdi://ftdi:232h:FT9/1', 'serial': 'FT9', 'description': 'Single RS232-HS'}]


def test_discover_ft232h_devices_empty_without_pyftdi():
	with mock.patch.dict('sys.modules', {'pyftdi.ftdi': None}):
		assert i2c_bus.discover_ft232h_devices() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_i2c_bus.py -k discover -v`
Expected: FAIL with `AttributeError: module 'common.i2c_bus' has no attribute 'discover_extended_i2c_buses'`

- [ ] **Step 3: Implement**

Add to `common/i2c_bus.py`, directly below `find_i2c_bus_by_serial`:

```python
def discover_extended_i2c_buses(devices_path='/sys/bus/i2c/devices'):
	"""Best-effort list of every extended-kind (kernel i2c-dev) adapter
	present, for the wizard's Discover button. Returns [] if devices_path
	doesn't exist or has no adapters; never raises."""
	return _enumerate_i2c_adapters(devices_path)


def discover_mcp2221_devices():
	"""Best-effort list of connected MCP2221 USB devices ({'serial', 'path'}),
	for the wizard's Discover button. Returns [] if the `hid` module or the
	Blinka MCP2221 backend aren't importable, or no devices are present --
	never raises."""
	try:
		import hid
		from adafruit_blinka.microcontroller.mcp2221 import mcp2221 as _mcp_mod
	except ImportError:
		return []
	try:
		return [
			{'serial': info.get('serial_number'), 'path': info.get('path')}
			for info in hid.enumerate(_mcp_mod.MCP2221.VID, _mcp_mod.MCP2221.PID)
			if info.get('serial_number')
		]
	except Exception:
		logger.debug('discover_mcp2221_devices: hid.enumerate failed', exc_info=True)
		return []


def discover_ft232h_devices():
	"""Best-effort list of connected FT232H USB devices ({'url', 'serial',
	'description'}), for the wizard's Discover button. Returns [] if pyftdi
	isn't importable or no devices are present -- never raises."""
	try:
		from pyftdi.ftdi import Ftdi
	except ImportError:
		return []
	try:
		devices = []
		for descriptor, _interface_count in Ftdi.list_devices('ftdi://ftdi:232h/'):
			url = f'ftdi://ftdi:232h:{descriptor.sn}/1' if descriptor.sn else 'ftdi://ftdi:232h/1'
			devices.append({'url': url, 'serial': descriptor.sn, 'description': descriptor.description})
		return devices
	except Exception:
		logger.debug('discover_ft232h_devices: Ftdi.list_devices failed', exc_info=True)
		return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_i2c_bus.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format common/i2c_bus.py tests/test_i2c_bus.py
git add common/i2c_bus.py tests/test_i2c_bus.py
git commit -m "$(cat <<'EOF'
feat(i2c): add best-effort device discovery for the wizard Discover button

discover_extended_i2c_buses/discover_mcp2221_devices/discover_ft232h_devices
enumerate connected devices per bus kind. All are best-effort: a missing
optional dependency or zero devices present resolve to [], never raise, so
a Discover click can never break the wizard page.
EOF
)"
```

---

### Task 5: Wizard route action `i2c_bus_scan` + results table macro + JS

**Files:**
- Modify: `blueprints/wizard/routes.py`
- Modify: `blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html` (add macro near `render_bt_scan_table`)
- Modify: `blueprints/probeconfig/static/probeconfig/js/probeconfig.js`
- Test: `tests/test_webapp_sqlite.py`

**Interfaces:**
- Consumes: `discover_extended_i2c_buses`, `discover_mcp2221_devices`, `discover_ft232h_devices` from Task 4.
- Produces: `POST /wizard/i2c_bus_scan` (form fields `itemID`, `kind`) returning an HTML fragment; `render_i2c_scan_table(itemID, candidates, error)` Jinja macro; `scanI2CBus(itemID, kindItemID)` / `selectI2CBus(value, itemID)` JS functions — used by Task 6's input macro.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_webapp_sqlite.py` (near `test_probeconfig_add_usb_hid_probe_not_blocked_by_stale_platform_bus`):

```python
@pytest.mark.skipif(flask_app is None, reason=f'app import failed (unrelated to datastore): {_APP_IMPORT_ERROR}')
def test_i2c_bus_scan_extended_lists_discovered_adapters(monkeypatch):
	flask_app.config.update(TESTING=True)
	client = flask_app.test_client()

	import blueprints.wizard.routes as wizard_routes

	monkeypatch.setattr(
		wizard_routes,
		'discover_extended_i2c_buses',
		lambda: [{'bus_num': 7, 'name': 'MCP2221 usb-i2c bridge', 'serial': 'AB12'}],
	)

	resp = client.post('/wizard/i2c_bus_scan', data={'itemID': 'distance_devspec_i2c_bus_num', 'kind': 'extended'})
	assert resp.status_code == 200
	body = resp.get_data(as_text=True)
	assert 'i2c-7' in body
	assert 'serial:AB12' in body


@pytest.mark.skipif(flask_app is None, reason=f'app import failed (unrelated to datastore): {_APP_IMPORT_ERROR}')
def test_i2c_bus_scan_no_devices_shows_error(monkeypatch):
	flask_app.config.update(TESTING=True)
	client = flask_app.test_client()

	import blueprints.wizard.routes as wizard_routes

	monkeypatch.setattr(wizard_routes, 'discover_mcp2221_devices', lambda: [])

	resp = client.post('/wizard/i2c_bus_scan', data={'itemID': 'distance_devspec_i2c_bus_num', 'kind': 'mcp2221'})
	assert resp.status_code == 200
	assert 'No mcp2221 I2C buses discovered.' in resp.get_data(as_text=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_webapp_sqlite.py -k i2c_bus_scan -v`
Expected: FAIL with 404 (no `/wizard/i2c_bus_scan` route yet) -- `assert resp.status_code == 200` fails.

- [ ] **Step 3: Implement the route action**

In `blueprints/wizard/routes.py`, change the import line:

```python
from common.i2c_bus import I2CBusConfigError, validate_bus_kinds
```

to:

```python
from common.i2c_bus import (
	I2CBusConfigError,
	discover_extended_i2c_buses,
	discover_ft232h_devices,
	discover_mcp2221_devices,
	validate_bus_kinds,
)
```

Then add a new action branch directly after the `if action == 'thermoworks_discover':` block (after its closing `return render_template_string(...)`, before the `""" Create Temporary Probe Device/Port Structure..."""` comment):

```python
		if action == 'i2c_bus_scan':
			itemID = r['itemID']
			kind = r.get('kind', '')
			candidates = []
			error = None

			try:
				if kind == 'extended':
					for adapter in discover_extended_i2c_buses():
						candidates.append(
							{'value': str(adapter['bus_num']), 'label': f"i2c-{adapter['bus_num']} ({adapter['name']})"}
						)
						if adapter['serial']:
							candidates.append(
								{
									'value': f'serial:{adapter["serial"]}',
									'label': f'{adapter["name"]} — serial {adapter["serial"]}',
								}
							)
				elif kind == 'mcp2221':
					for device in discover_mcp2221_devices():
						candidates.append({'value': device['serial'], 'label': f'MCP2221 serial {device["serial"]}'})
				elif kind == 'ft232h':
					for device in discover_ft232h_devices():
						candidates.append(
							{'value': device['url'], 'label': f'{device["description"] or "FT232H"} ({device["url"]})'}
						)
				else:
					error = f"Unknown I2C bus kind {kind!r}. Select Extended, FT232H, or MCP2221 first."

				if not candidates and error is None:
					error = f'No {kind} I2C buses discovered.'
			except Exception as e:
				error = f'Something bad happened: {e}'

			render_string = "{% from 'probeconfig/_macro_probes_config.html' import render_i2c_scan_table %}{{ render_i2c_scan_table(itemID, candidates, error) }}"
			return render_template_string(render_string, itemID=itemID, candidates=candidates, error=error)
```

- [ ] **Step 4: Add the results-table macro**

In `blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html`, add directly after the `render_bt_scan_table` macro's closing `{% endmacro %}`:

```jinja
{% macro render_i2c_scan_table(itemID, candidates, error) %}
    {% if error %}
        <div class="alert alert-danger" role="alert">
            {{ error }}
        </div>
    {% else %}
        <table class="table">
            <thead class="thead-light">
                <tr>
                <th scope="col">Discovered Bus</th>
                <th scope="col"></th>
                </tr>
            </thead>
            <tbody>
                {% for candidate in candidates %}
                <tr>
                    <td class="align-middle">{{ candidate['label'] }}</td>
                    <td class="align-middle">
                        <button type="button" class="btn btn-primary btn-sm" onclick="selectI2CBus('{{ candidate['value'] }}', '{{ itemID }}')">Select</button>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    {% endif %}
{% endmacro %}
```

- [ ] **Step 5: Add the JS functions**

In `blueprints/probeconfig/static/probeconfig/js/probeconfig.js`, add directly after `selectThermoworksDevice` (before the `// Device Functions` comment section):

```js
//
// I2C Bus Discovery Functions
//
function scanI2CBus(itemID, kindItemID) {
	const modal = '#i2c_' + itemID + '_Modal';
	const modalContent = '#i2c_' + itemID + '_Select';
	const kind = $('#' + kindItemID).val();
	$(modal).modal('show');
	// Show scanning text while scanning
	$(modalContent).html('<br> \
                <h4>Scanning...</h4> \
                <br> \
                <div class="fa-3x"> \
                    <i class="fa-solid fa-magnifying-glass fa-bounce"></i> \
                </div> \
                <br></br>');
	// Load the I2C bus scan results
	$(modalContent).load("/wizard/i2c_bus_scan", {"itemID" : itemID, "kind" : kind});
}

function selectI2CBus(value, itemID) {
	const modal = '#i2c_' + itemID + '_Modal';
	$('#' + itemID).val(value);
	// Hide the modal
	$(modal).modal('hide');
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_webapp_sqlite.py -v`
Expected: PASS (all tests in the file, including the two new ones and all pre-existing ones)

- [ ] **Step 7: Format and commit**

```bash
uvx ruff format blueprints/wizard/routes.py tests/test_webapp_sqlite.py
git add blueprints/wizard/routes.py blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html blueprints/probeconfig/static/probeconfig/js/probeconfig.js tests/test_webapp_sqlite.py
git commit -m "$(cat <<'EOF'
feat(wizard): add /wizard/i2c_bus_scan Discover action

Mirrors the existing bt_scan/thermoworks_discover pattern: calls the
matching discovery helper for the selected i2c_bus_kind and renders a
results table fragment with per-row Select buttons. Not yet wired to any
input field -- that's the next task.
EOF
)"
```

---

### Task 6: Shared `render_input_i2c_bus_num` macro + probe device_specific dispatch

**Files:**
- Modify: `blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html:201-223` (dispatch), add macro near `render_input_bt_address` (~line 490-536)

**Interfaces:**
- Consumes: `scanI2CBus`/`selectI2CBus` JS from Task 5.
- Produces: `render_input_i2c_bus_num(dom_id, css_class, default, kind_dom_id)` Jinja macro -- reused by Task 8 from `_macro_wizard_card.html`.

- [ ] **Step 1: Add the input macro**

In `blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html`, add directly after `render_input_bt_address`'s closing `{% endmacro %}` (before `render_input_thermoworks_discover`):

```jinja
{% macro render_input_i2c_bus_num(dom_id, css_class, default, kind_dom_id) %}

<div class="input-group mb-3">
    <input type="text" class="form-control {{ css_class }}"
    value="{{ default }}" aria-label="i2c_bus_num"
    id="{{ dom_id }}"
    name="{{ dom_id }}"/>
    <div class="input-group-append">
        <button type="button" class="btn btn-success" id="i2c_{{ dom_id }}_Scan" onclick="scanI2CBus('{{ dom_id }}', '{{ kind_dom_id }}')">Discover</button>
    </div>
</div>

<!-- Discover I2C Bus Modal -->
<div class="modal fade power-modal" id="i2c_{{ dom_id }}_Modal" data-backdrop="false" tabindex="-1" aria-labelledby="i2c_{{ dom_id }}_Label" aria-hidden="true" >
    <div class="modal-dialog modal-xl">
    <div class="modal-content">
        <div class="modal-header">
        <h5 class="modal-title" id="i2c_{{ dom_id }}_Label">Discovered I2C Buses</h5>
        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
            <span aria-hidden="true">&times;</span>
        </button>
        </div>
        <div class="modal-body text-center">

            <div id="i2c_{{ dom_id }}_Select">
                <br>
                <h4>Scanning...</h4>
                <br>
                <div class="fa-3x">
                    <i class="fa-solid fa-magnifying-glass fa-bounce"></i>
                </div>
                <br>
            </div>

        </div>
        <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-dismiss="modal">Close</button>
        <button type="button" class="btn btn-primary" value="" onclick="scanI2CBus('{{ dom_id }}', '{{ kind_dom_id }}')">Refresh</button>
        </div>
    </div>
    </div>
</div>

{% endmacro %}
```

- [ ] **Step 2: Wire the dispatch for probe device_specific fields**

In the same file, in `render_probe_device_settings`, the Edit-mode branch currently reads (around line 202-211):

```jinja
                        {% if mode == 'Edit' %}
                            {% if setting['type'] in ['float', 'int'] %}
                                {{ render_input_float_int(moduleSection, mode, setting['label'], defaultConfig[setting['label']], setting['min'], setting['max'], setting['step']) }} 
                            {% elif setting['type'] == 'list' %}
                                {{ render_input_list(moduleSection, mode, setting['label'], defaultConfig[setting['label']], setting['list_values'], setting['list_labels']) }}
                            {% elif setting['type'] == 'bt_address' %}
                                {{ render_input_bt_address(moduleSection, mode, setting['label'], defaultConfig[setting['label']]) }}
                            {% else %}
                                {{ render_input_string(moduleSection, mode, setting['label'], defaultConfig[setting['label']]) }}
                            {% endif %}
                        {% else %}
                            {% if setting['type'] in ['float', 'int'] %}
                                {{ render_input_float_int(moduleSection, mode, setting['label'], setting['default'], setting['min'], setting['max'], setting['step']) }} 
                            {% elif setting['type'] == 'list' %}
                                {{ render_input_list(moduleSection, mode, setting['label'], setting['default'], setting['list_values'], setting['list_labels']) }}
                            {% elif setting['type'] == 'bt_address' %}
                                {{ render_input_bt_address(moduleSection, mode, setting['label'], defaultConfig[setting['label']]) }}
                            {% else %}
                                {{ render_input_string(moduleSection, mode, setting['label'], setting['default']) }}
                            {% endif %}
                        {% endif %}
```

Replace it with (adds one `{% elif %}` branch to each of the two `{% if mode == 'Edit' %}`/`{% else %}` halves):

```jinja
                        {% if mode == 'Edit' %}
                            {% if setting['type'] in ['float', 'int'] %}
                                {{ render_input_float_int(moduleSection, mode, setting['label'], defaultConfig[setting['label']], setting['min'], setting['max'], setting['step']) }} 
                            {% elif setting['type'] == 'list' %}
                                {{ render_input_list(moduleSection, mode, setting['label'], defaultConfig[setting['label']], setting['list_values'], setting['list_labels']) }}
                            {% elif setting['type'] == 'bt_address' %}
                                {{ render_input_bt_address(moduleSection, mode, setting['label'], defaultConfig[setting['label']]) }}
                            {% elif setting['type'] == 'i2c_bus_num' %}
                                {{ render_input_i2c_bus_num(moduleSection ~ '_devspec_' ~ setting['label'], 'deviceSpecific' ~ mode, defaultConfig[setting['label']], moduleSection ~ '_devspec_i2c_bus_kind') }}
                            {% else %}
                                {{ render_input_string(moduleSection, mode, setting['label'], defaultConfig[setting['label']]) }}
                            {% endif %}
                        {% else %}
                            {% if setting['type'] in ['float', 'int'] %}
                                {{ render_input_float_int(moduleSection, mode, setting['label'], setting['default'], setting['min'], setting['max'], setting['step']) }} 
                            {% elif setting['type'] == 'list' %}
                                {{ render_input_list(moduleSection, mode, setting['label'], setting['default'], setting['list_values'], setting['list_labels']) }}
                            {% elif setting['type'] == 'bt_address' %}
                                {{ render_input_bt_address(moduleSection, mode, setting['label'], defaultConfig[setting['label']]) }}
                            {% elif setting['type'] == 'i2c_bus_num' %}
                                {{ render_input_i2c_bus_num(moduleSection ~ '_devspec_' ~ setting['label'], 'deviceSpecific' ~ mode, setting['default'], moduleSection ~ '_devspec_i2c_bus_kind') }}
                            {% else %}
                                {{ render_input_string(moduleSection, mode, setting['label'], setting['default']) }}
                            {% endif %}
                        {% endif %}
```

(`setting['label']` is always `'i2c_bus_num'` on the entries this branch matches, and its sibling entry's label is always `'i2c_bus_kind'` -- see the manifest excerpts in Task 7. Hence the hardcoded `_devspec_i2c_bus_kind` suffix.)

- [ ] **Step 3: Verify no manifest field triggers this branch yet**

Run: `uv run pytest tests/ -q`
Expected: PASS, no behavior change yet -- no manifest field has `"type": "i2c_bus_num"` until Task 7, so the new branch is unreachable dead code at this point (verified next task by making it reachable).

- [ ] **Step 4: Format and commit**

```bash
uvx ruff format --check blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html 2>/dev/null || true
git add blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html
git commit -m "$(cat <<'EOF'
feat(probeconfig): add free-text + Discover input macro for i2c_bus_num

render_input_i2c_bus_num replaces the strict <select> the i2c_bus_num field
currently gets via render_input_list, once wizard_manifest.json marks that
field's type accordingly (next task). Not yet reachable until that manifest
change lands.
EOF
)"
```
(`ruff format` only reformats Python; the `.html` template edit has no formatter in this repo -- the `--check` line is a no-op guard, safe to skip if it errors.)

---

### Task 7: Manifest -- probe device_specific `i2c_bus_num` becomes free text

**Files:**
- Modify: `wizard/wizard_manifest.json` (5 occurrences: 3 identical "recommended" blocks, 2 identical shorter blocks)
- Modify: `tests/test_i2c_bridge_match_manifest.py`

**Interfaces:**
- Consumes: `render_input_i2c_bus_num` dispatch from Task 6.

- [ ] **Step 1: Update the failing manifest test first**

`test_busio_probe_bus_num_lists_offer_mcp2221` in `tests/test_i2c_bridge_match_manifest.py` currently asserts `list_values`/`list_labels` on the probe `i2c_bus_num` field -- which is going away. Replace that test with:

```python
def test_busio_probe_bus_num_is_free_text_and_documents_bridges():
	"""The busio probe i2c_bus_num field (which drives the Extended bus) is
	free text with a Discover button, and its description documents both
	bridge-name matches and the serial: selector."""
	manifest = _manifest()
	checked = 0
	for name in ('mcp9600_adafruit', 'ads1115_adafruit', 'ads1015_adafruit'):
		cfg = manifest['modules']['probes'][name]['device_specific']['config']
		field = next(c for c in cfg if c['label'] == 'i2c_bus_num')
		assert field['type'] == 'i2c_bus_num'
		assert 'list_values' not in field
		assert 'CP2112' in field['description']
		assert 'MCP2221' in field['description']
		assert 'serial:' in field['description']
		checked += 1
	assert checked == 3
```

Leave `test_every_bridge_selector_offers_mcp2221` and `test_find_i2c_bus_matches_mcp2221_adapter` as-is for now (the former's manifest-wide `options` walk is updated in Task 9, once the last `options`-based `i2c_bus_num` entries are also converted).

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_i2c_bridge_match_manifest.py -k free_text -v`
Expected: FAIL -- `field['type']` is still `'list'`.

- [ ] **Step 3: Update the manifest -- "recommended" variant (3 occurrences)**

In `wizard/wizard_manifest.json`, this exact block appears 3 times (used by `mcp9600_adafruit`, `ads1115_adafruit`, `ads1015_adafruit`):

```json
            {
              "label": "i2c_bus_num",
              "friendly_name": "Extended I2C Bus",
              "description": "Which value to use when I2C Bus Type is Extended, FT232H, or MCP2221. For Extended: 'CP2112' or 'MCP2221' auto-discovers the matching USB-to-I2C bridge by adapter name (recommended -- robust to changing bus numbers), or a number selects /dev/i2c-N explicitly. For FT232H: a pyftdi URL (blank = first). For MCP2221: a device serial (blank = first). Ignored when Basic.",
              "type": "list",
              "list_values": ["CP2112", "MCP2221", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"],
              "list_labels": ["CP2112 USB bridge (auto)", "MCP2221 USB bridge (auto)", "/dev/i2c-0", "/dev/i2c-1", "/dev/i2c-2", "/dev/i2c-3", "/dev/i2c-4", "/dev/i2c-5", "/dev/i2c-6", "/dev/i2c-7", "/dev/i2c-8", "/dev/i2c-9", "/dev/i2c-10", "/dev/i2c-11", "/dev/i2c-12", "/dev/i2c-13", "/dev/i2c-14", "/dev/i2c-15"],
              "default": "CP2112",
              "hidden": false
            },
```

Use the Edit tool with `replace_all: true` to replace every occurrence of that exact block with:

```json
            {
              "label": "i2c_bus_num",
              "friendly_name": "Extended I2C Bus",
              "description": "Which value to use when I2C Bus Type is Extended, FT232H, or MCP2221. For Extended: 'CP2112' or 'MCP2221' auto-discovers the matching USB-to-I2C bridge by adapter name (recommended -- robust to changing bus numbers), 'serial:<ISERIAL>' matches a specific USB device by its serial number (needed when multiple identical bridges are connected), or a number selects /dev/i2c-N explicitly. For FT232H: a pyftdi URL (blank = first). For MCP2221: a device serial (blank = first). Use the Discover button to scan for connected devices. Ignored when Basic.",
              "type": "i2c_bus_num",
              "default": "CP2112",
              "hidden": false
            },
```

- [ ] **Step 4: Update the manifest -- shorter variant (2 occurrences)**

This exact block appears 2 times (used by the two non-eligible-for-ft232h/mcp2221 probes, e.g. the smbus2 `ads1115` and `prototype`):

```json
            {
              "label": "i2c_bus_num",
              "friendly_name": "Extended I2C Bus",
              "description": "Which bus to use when I2C Bus Type is Extended. 'CP2112' or 'MCP2221' auto-discovers the matching USB-to-I2C bridge by adapter name (recommended -- robust to changing bus numbers); a number selects /dev/i2c-N explicitly. Ignored when Basic.",
              "type": "list",
              "list_values": ["CP2112", "MCP2221", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"],
              "list_labels": ["CP2112 USB bridge (auto)", "MCP2221 USB bridge (auto)", "/dev/i2c-0", "/dev/i2c-1", "/dev/i2c-2", "/dev/i2c-3", "/dev/i2c-4", "/dev/i2c-5", "/dev/i2c-6", "/dev/i2c-7", "/dev/i2c-8", "/dev/i2c-9", "/dev/i2c-10", "/dev/i2c-11", "/dev/i2c-12", "/dev/i2c-13", "/dev/i2c-14", "/dev/i2c-15"],
              "default": "CP2112",
              "hidden": false
            },
```

Use the Edit tool with `replace_all: true` to replace every occurrence of that exact block with:

```json
            {
              "label": "i2c_bus_num",
              "friendly_name": "Extended I2C Bus",
              "description": "Which bus to use when I2C Bus Type is Extended. 'CP2112' or 'MCP2221' auto-discovers the matching USB-to-I2C bridge by adapter name (recommended -- robust to changing bus numbers); 'serial:<ISERIAL>' matches a specific USB device by its serial number (needed when multiple identical bridges are connected); a number selects /dev/i2c-N explicitly. Use the Discover button to scan for connected devices. Ignored when Basic.",
              "type": "i2c_bus_num",
              "default": "CP2112",
              "hidden": false
            },
```

- [ ] **Step 5: Validate the manifest is still valid JSON**

Run: `python3 -c "import json; json.load(open('wizard/wizard_manifest.json'))" && echo OK`
Expected: `OK`

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_i2c_bridge_match_manifest.py tests/test_wizard_bus_kinds.py tests/test_i2c_bus_wizard_validation.py tests/test_webapp_sqlite.py -v`
Expected: PASS (all)

- [ ] **Step 7: Manual UI smoke check**

Run the app locally (see this repo's `run` skill / dev-server instructions) and open the probe-device Add/Edit dialog for `mcp9600_adafruit` with I2C Bus Type set to Extended: confirm the Extended I2C Bus field is now a text input with a "Discover" button, and clicking it opens a modal (it may show "No extended I2C buses discovered" on a dev machine with no real i2c-dev adapters -- that is correct best-effort behavior, not a bug).

- [ ] **Step 8: Format and commit**

```bash
git add wizard/wizard_manifest.json tests/test_i2c_bridge_match_manifest.py
git commit -m "$(cat <<'EOF'
feat(wizard): probe i2c_bus_num is free text with Discover, not a dropdown

The field's description already claimed pyftdi URLs and MCP2221 serials
were enterable; the fixed-option <select> made that impossible. Switching
type to i2c_bus_num (Task 6) makes it actually true, and documents the new
serial:<ISERIAL> extended-bus selector.
EOF
)"
```

---

### Task 8: `_macro_wizard_card.html` dispatch + `wizardInstallInfoDefaults` fix

**Files:**
- Modify: `blueprints/wizard/templates/wizard/_macro_wizard_card.html:1,30-42`
- Modify: `blueprints/wizard/wizard.py:57-61`
- Test: `tests/test_webapp_sqlite.py`, new unit test for `wizardInstallInfoDefaults`

**Interfaces:**
- Consumes: `render_input_i2c_bus_num` from Task 6.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_webapp_sqlite.py`:

```python
@pytest.mark.skipif(flask_app is None, reason=f'app import failed (unrelated to datastore): {_APP_IMPORT_ERROR}')
def test_wizard_modulecard_renders_i2c_bus_num_as_free_text():
	flask_app.config.update(TESTING=True)
	client = flask_app.test_client()

	resp = client.post('/wizard/modulecard', data={'module': 'vl53l0x', 'section': 'distance'})
	assert resp.status_code == 200
	body = resp.get_data(as_text=True)
	assert 'type="text"' in body
	assert 'Discover' in body
	assert '<select' not in body or 'device_distance_i2c_bus_num' not in body.split('<select')[1][:500]
```

Add a new file `tests/test_wizard_install_info_defaults.py`:

```python
from blueprints.wizard.wizard import wizardInstallInfoDefaults

_WIZARD_DATA = {
	'modules': {
		'grillplatform': {
			'x86': {
				'default': True,
				'settings_dependencies': {
					'i2c_bus_kind': {
						'options': {'basic': 'Basic', 'extended': 'Extended'},
						'settings': ['platform', 'fan_controller', 'i2c_bus_kind'],
					},
					'i2c_bus_num': {
						'type': 'i2c_bus_num',
						'default': 'CP2112',
						'settings': ['platform', 'fan_controller', 'i2c_bus_num'],
					},
				},
			}
		},
		'display': {'none': {'default': True, 'settings_dependencies': {}}},
		'distance': {'none': {'default': True, 'settings_dependencies': {}}},
	},
	'boards': {'x86': {'probe_map': {'probe_devices': []}}},
}


def test_wizard_install_info_defaults_handles_options_free_field():
	settings = {'display': {'config': {'none': {}}}}
	info = wizardInstallInfoDefaults(_WIZARD_DATA, settings)
	# 'options'-based dependency still seeds its first key.
	assert info['modules']['grillplatform']['settings']['i2c_bus_kind'] == 'basic'
	# 'type: i2c_bus_num' dependency (no 'options') seeds its explicit 'default'.
	assert info['modules']['grillplatform']['settings']['i2c_bus_num'] == 'CP2112'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wizard_install_info_defaults.py -v`
Expected: FAIL with `KeyError: 'options'` (raised inside `wizardInstallInfoDefaults`)

Run: `uv run pytest tests/test_webapp_sqlite.py -k modulecard -v`
Expected: FAIL -- the response still contains a `<select>` for `device_distance_i2c_bus_num`.

- [ ] **Step 3: Fix `wizardInstallInfoDefaults`**

In `blueprints/wizard/wizard.py`, replace (currently lines 57-61):

```python
					for setting in wizardData['modules'][component][module]['settings_dependencies']:
						""" Populate all settings with default value """
						wizardInstallInfo['modules'][component]['settings'][setting] = list(
							wizardData['modules'][component][module]['settings_dependencies'][setting]['options'].keys()
						)[0]
```

with:

```python
					for setting in wizardData['modules'][component][module]['settings_dependencies']:
						""" Populate all settings with default value """
						dep = wizardData['modules'][component][module]['settings_dependencies'][setting]
						if 'options' in dep:
							default_value = list(dep['options'].keys())[0]
						else:
							default_value = dep.get('default', '')
						wizardInstallInfo['modules'][component]['settings'][setting] = default_value
```

- [ ] **Step 4: Wire the dispatch in `_macro_wizard_card.html`**

At the very top of `blueprints/wizard/templates/wizard/_macro_wizard_card.html` (line 1, before `{% macro render_wizard_card ... %}`), add:

```jinja
{% from 'probeconfig/_macro_probes_config.html' import render_input_i2c_bus_num %}
```

Then replace the settings_dependencies loop (currently lines 30-42):

```jinja
				{% for setting in moduleData['settings_dependencies'] %}
				<tr {% if moduleData['settings_dependencies'][setting]['hidden'] %} hidden {% endif %}>
					<td>{{ moduleData['settings_dependencies'][setting]['friendly_name'] }}</td>
					<td>
						<select class="form-control" id="{{ moduleSection }}_{{ setting }}" name="{{ moduleSection }}_{{ setting }}">
						{% for option in moduleData['settings_dependencies'][setting]['options'] %}
							<option value="{{ option }}"{% if moduleSettings['settings'][setting]|string == option %} selected{% endif %}>{{ moduleData['settings_dependencies'][setting]['options'][option] }}</option>
						{% endfor %}
						</select>
					</td>
					<td>{{ moduleData['settings_dependencies'][setting]['description'] }}</td>
					</tr>
				{% endfor %}
```

with:

```jinja
				{% for setting in moduleData['settings_dependencies'] %}
				<tr {% if moduleData['settings_dependencies'][setting]['hidden'] %} hidden {% endif %}>
					<td>{{ moduleData['settings_dependencies'][setting]['friendly_name'] }}</td>
					<td>
						{% if moduleData['settings_dependencies'][setting].get('type') == 'i2c_bus_num' %}
						{{ render_input_i2c_bus_num(moduleSection ~ '_' ~ setting, '', moduleSettings['settings'][setting], moduleSection ~ '_' ~ (setting | replace('_num', '_kind'))) }}
						{% else %}
						<select class="form-control" id="{{ moduleSection }}_{{ setting }}" name="{{ moduleSection }}_{{ setting }}">
						{% for option in moduleData['settings_dependencies'][setting]['options'] %}
							<option value="{{ option }}"{% if moduleSettings['settings'][setting]|string == option %} selected{% endif %}>{{ moduleData['settings_dependencies'][setting]['options'][option] }}</option>
						{% endfor %}
						</select>
						{% endif %}
					</td>
					<td>{{ moduleData['settings_dependencies'][setting]['description'] }}</td>
					</tr>
				{% endfor %}
```

(The paired kind setting is always named by swapping the `_num` suffix for `_kind` -- e.g. `device_distance_i2c_bus_num` / `device_distance_i2c_bus_kind`, `i2c_bus_num` / `i2c_bus_kind` -- the same convention already used throughout `wizard_manifest.json`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_wizard_install_info_defaults.py tests/test_webapp_sqlite.py -v`
Expected: PASS (still no manifest field has `type: i2c_bus_num` in the real `wizard_manifest.json` yet, so `test_wizard_modulecard_renders_i2c_bus_num_as_free_text` -- run against the real manifest -- will still fail until Task 9. Confirm it fails for the *expected* reason only: run it and check the failure is `'type="text"' in body` being False, not an error/traceback.)

- [ ] **Step 6: Format and commit**

```bash
uvx ruff format blueprints/wizard/wizard.py tests/test_wizard_install_info_defaults.py tests/test_webapp_sqlite.py
git add blueprints/wizard/wizard.py blueprints/wizard/templates/wizard/_macro_wizard_card.html tests/test_wizard_install_info_defaults.py tests/test_webapp_sqlite.py
git commit -m "$(cat <<'EOF'
feat(wizard): settings-dependency i2c_bus_num dispatches to free-text input

_macro_wizard_card.html (distance sensor, fan controller) gets the same
Discover-button treatment as the probe device field. wizardInstallInfoDefaults
is fixed to seed a default from 'default' (not 'options') for a dependency
that no longer has a fixed option set. Not yet reachable in the real
manifest until the next task converts those fields.
EOF
)"
```

---

### Task 9: Manifest -- distance sensor / fan controller `i2c_bus_num` become free text

**Files:**
- Modify: `wizard/wizard_manifest.json` (8 occurrences: 7 identical distance blocks, 1 fan_controller block)
- Modify: `tests/test_i2c_bridge_match_manifest.py`

**Interfaces:**
- Consumes: `_macro_wizard_card.html` dispatch from Task 8.

- [ ] **Step 1: Update the failing manifest test first**

`test_every_bridge_selector_offers_mcp2221` in `tests/test_i2c_bridge_match_manifest.py` walks the manifest for any `options` dict containing `'CP2112'` -- after this task, none remain (they're all converted to `type: i2c_bus_num`), so its `assert found` would start failing for the wrong reason (test rot, not a real regression). Replace it with:

```python
def test_every_i2c_bus_num_field_documents_both_bridges_and_serial_match():
	"""Every i2c_bus_num field (settings-dependency or device_specific) is
	free text (type: i2c_bus_num) and documents CP2112, MCP2221, and the
	serial: selector in its description, so no field silently regresses to a
	fixed dropdown that can't express a USB serial."""
	manifest = _manifest()
	found = 0

	def walk(node):
		nonlocal found
		if isinstance(node, dict):
			if node.get('type') == 'i2c_bus_num' or (
				'i2c_bus_num' in node.get('settings', []) if isinstance(node.get('settings'), list) else False
			):
				found += 1
				assert node.get('type') == 'i2c_bus_num', f'i2c_bus_num field is not free text: {node}'
				assert 'CP2112' in node['description']
				assert 'MCP2221' in node['description']
				assert 'serial:' in node['description']
			for value in node.values():
				walk(value)
		elif isinstance(node, list):
			for value in node:
				walk(value)

	walk(manifest['modules'])
	assert found == 13, f'expected 13 i2c_bus_num fields (5 probe + 7 distance + 1 fan controller), found {found}'
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_i2c_bridge_match_manifest.py -k both_bridges_and_serial -v`
Expected: FAIL -- `found == 5` (only the probe fields converted in Task 7), not 13.

- [ ] **Step 3: Update the manifest -- distance sensor variant (7 occurrences)**

In `wizard/wizard_manifest.json`, this exact block appears 7 times:

```json
          "device_distance_i2c_bus_num": {
            "friendly_name": "Distance Sensor Extended I2C Bus",
            "description": "Which value to use when Distance Sensor I2C Bus Type is Extended, FT232H, or MCP2221. For Extended: 'CP2112' or 'MCP2221' auto-discovers the matching USB-to-I2C bridge by adapter name (robust to changing bus numbers), or a number selects /dev/i2c-N explicitly. For FT232H: a pyftdi URL (blank = first). For MCP2221: a device serial (blank = first). Ignored when Basic.",
            "options": {
              "CP2112": "CP2112 (bridge name match)",
              "MCP2221": "MCP2221 (bridge name match)",
              "0": "i2c-0",
              "1": "i2c-1",
              "2": "i2c-2",
              "3": "i2c-3",
              "4": "i2c-4",
              "5": "i2c-5",
              "6": "i2c-6",
              "7": "i2c-7",
              "8": "i2c-8",
              "9": "i2c-9",
              "10": "i2c-10",
              "11": "i2c-11",
              "12": "i2c-12",
              "13": "i2c-13",
              "14": "i2c-14",
              "15": "i2c-15"
            },
            "settings": ["platform", "devices", "distance", "i2c_bus_num"]
          },
```

Use the Edit tool with `replace_all: true` to replace every occurrence of that exact block with:

```json
          "device_distance_i2c_bus_num": {
            "friendly_name": "Distance Sensor Extended I2C Bus",
            "description": "Which value to use when Distance Sensor I2C Bus Type is Extended, FT232H, or MCP2221. For Extended: 'CP2112' or 'MCP2221' auto-discovers the matching USB-to-I2C bridge by adapter name (robust to changing bus numbers), 'serial:<ISERIAL>' matches a specific USB device by its serial number (needed when multiple identical bridges are connected), or a number selects /dev/i2c-N explicitly. For FT232H: a pyftdi URL (blank = first). For MCP2221: a device serial (blank = first). Use the Discover button to scan for connected devices. Ignored when Basic.",
            "type": "i2c_bus_num",
            "default": "CP2112",
            "settings": ["platform", "devices", "distance", "i2c_bus_num"]
          },
```

- [ ] **Step 4: Update the manifest -- fan controller variant (1 occurrence)**

In `wizard/wizard_manifest.json`, replace:

```json
          "i2c_bus_num": {
            "friendly_name": "Fan Controller Extended Bus",
            "description": "Which value to use when I2C Bus Type is Extended, FT232H, or MCP2221. For Extended: 'CP2112' or 'MCP2221' auto-discovers the matching USB-to-I2C bridge by adapter name (robust to changing bus numbers), or a number selects /dev/i2c-N explicitly. For FT232H: a pyftdi URL (blank = first). For MCP2221: a device serial (blank = first). Ignored when Basic.",
            "options": {
              "CP2112": "CP2112 (bridge name match)",
              "MCP2221": "MCP2221 (bridge name match)",
              "0": "i2c-0",
              "1": "i2c-1",
              "2": "i2c-2",
              "3": "i2c-3",
              "4": "i2c-4",
              "5": "i2c-5",
              "6": "i2c-6",
              "7": "i2c-7",
              "8": "i2c-8",
              "9": "i2c-9",
              "10": "i2c-10",
              "11": "i2c-11",
              "12": "i2c-12",
              "13": "i2c-13",
              "14": "i2c-14",
              "15": "i2c-15"
            },
            "settings": ["platform", "fan_controller", "i2c_bus_num"]
          },
```

with:

```json
          "i2c_bus_num": {
            "friendly_name": "Fan Controller Extended Bus",
            "description": "Which value to use when I2C Bus Type is Extended, FT232H, or MCP2221. For Extended: 'CP2112' or 'MCP2221' auto-discovers the matching USB-to-I2C bridge by adapter name (robust to changing bus numbers), 'serial:<ISERIAL>' matches a specific USB device by its serial number (needed when multiple identical bridges are connected), or a number selects /dev/i2c-N explicitly. For FT232H: a pyftdi URL (blank = first). For MCP2221: a device serial (blank = first). Use the Discover button to scan for connected devices. Ignored when Basic.",
            "type": "i2c_bus_num",
            "default": "CP2112",
            "settings": ["platform", "fan_controller", "i2c_bus_num"]
          },
```

- [ ] **Step 5: Validate the manifest is still valid JSON**

Run: `python3 -c "import json; json.load(open('wizard/wizard_manifest.json'))" && echo OK`
Expected: `OK`

- [ ] **Step 6: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
Expected: PASS (all tests, including `test_wizard_modulecard_renders_i2c_bus_num_as_free_text` from Task 8, now reachable)

- [ ] **Step 7: Manual UI smoke check**

Run the app locally, open the wizard's platform (fan controller) and distance-sensor steps: confirm both Extended I2C Bus fields are now free-text inputs with a "Discover" button.

- [ ] **Step 8: Format and commit**

```bash
git add wizard/wizard_manifest.json tests/test_i2c_bridge_match_manifest.py
git commit -m "$(cat <<'EOF'
feat(wizard): distance sensor + fan controller i2c_bus_num free text

Completes the i2c_bus_num free-text conversion across all three surfaces
(probes, distance sensor, fan controller) and all three USB-capable kinds,
so serial:<ISERIAL> and the Discover button work everywhere the field
appears, not just for probe devices.
EOF
)"
```

---

### Task 10: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
Expected: PASS, 0 failures.

- [ ] **Step 2: Run ruff format check across all touched Python files**

```bash
uvx ruff format common/i2c_bus.py blueprints/wizard/routes.py blueprints/wizard/wizard.py \
	tests/test_i2c_bus.py tests/test_i2c_bridge_match_manifest.py tests/test_webapp_sqlite.py \
	tests/test_wizard_install_info_defaults.py
git status --short
```

Expected: `git status --short` shows no unstaged changes from formatting (everything was already formatted per-task); if it does, stage and commit the formatting fix.

- [ ] **Step 3: Manual end-to-end smoke check**

With real MCP2221/CP2112 hardware if available: plug in one bridge, use Discover on any of the three `i2c_bus_num` fields, confirm the numeric and (if a USB ancestor is found) `serial:<ISERIAL>` candidates both appear and both work when selected and saved. If two identical bridges are available, confirm `serial:<ISERIAL>` distinguishes them where the adapter-name match could not.

- [ ] **Step 4: Final commit (if any cleanup was needed)**

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore: final formatting pass for extended I2C serial-match feature
EOF
)"
```
(Skip this commit entirely if Step 2 showed no changes.)
