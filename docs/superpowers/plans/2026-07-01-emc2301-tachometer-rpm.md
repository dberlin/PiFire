# EMC2301 Tachometer / Fan-Speed (RPM) Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `fan_speed` property (RPM) to the PiFire `EMC2301` driver, plus a `poles` constructor argument that configures the chip's tachometer edge count so the reading is correct for 1–4 pole fans.

**Architecture:** Extend `grillplat/emc2301.py` only. The constructor gains a validated `poles` arg (default 2) that sets the EDGES field of Fan Config 1 (`0x32`) at init via read-modify-write. A new `fan_speed` property reads the two TACH registers (`0x3E`/`0x3F`), assembles the 13-bit count, reads the RANGE multiplier live from `0x32`, and returns `m × 3932160 / count` (or `0.0` for a stalled/zero count). Tests extend the existing `tests/test_emc2301.py` `FakeI2C` register-map fake.

**Tech Stack:** Python 3, pytest, `adafruit_bus_device.i2c_device.I2CDevice`.

**Spec:** `docs/superpowers/specs/2026-07-01-emc2301-tachometer-rpm-design.md`

## Global Constraints

- **Python files use TAB indentation** (repo convention) — every code block below is tab-indented; preserve that exactly.
- **Property name `fan_speed`, returns `float` RPM** — matches the Adafruit `EMC2101.fan_speed` so both fan-controller drivers share the interface.
- **`poles` default is `2`, validated to `{1, 2, 3, 4}`** — anything else raises `ValueError('poles must be 1-4')`. Validation happens **before** the `I2CDevice` is constructed.
- **Existing callers unchanged:** `grillplat/x86_numato.py` constructs `EMC2301(i2c, address=...)`; the `poles=2` default must write EDGES = `0b01`, which equals the chip's power-on default, so behavior is identical for existing callers.
- **RANGE multiplier `m` is read live on every `fan_speed` call** (no caching) from `0x32` bits [6:5], mapped `{0:1, 1:2, 2:4, 3:8}`.
- **RPM formula:** `RPM = round(m × 3932160 / count, 2)`, where `count = ((msb << 8) | lsb) >> 3` from `0x3E` (high) / `0x3F` (low).
- **Stalled fan** (`count >= 0x1FFF`) and **zero count** both return `0.0` — never raise.
- **EDGES init write preserves all non-EDGES bits** of `0x32` (RANGE bits [6:5], update-time bits [2:0], EN_ALGO bit [7]).
- Run tests with the project venv: `.venv/bin/python3 -m pytest`.

---

### Task 1: `poles` constructor arg + EDGES init write

**Files:**
- Modify: `grillplat/emc2301.py` (module constants + `__init__`)
- Test: `tests/test_emc2301.py` (extend `_build_emc` helper + new init tests)

**Interfaces:**
- Consumes: nothing new (existing `_read_register`/`_write_register`).
- Produces: `EMC2301(i2c_bus, address=0x2F, poles=2)` — stores `self.poles`; at init sets `0x32` EDGES bits [4:3] to `poles - 1`. New module constant `_REG_FAN_CONFIG1 = 0x32` and `_EDGES_MASK = 0x18` used by Task 2.

- [ ] **Step 1: Add `poles` support to the test helper and write the failing tests**

In `tests/test_emc2301.py`, first change the `_build_emc` helper to thread a `poles` argument through (existing callers keep the default 2):

```python
def _build_emc(seed=None, poles=2):
	"""Construct an EMC2301 with a FakeI2C, optionally pre-seeding registers
	before __init__ runs. Returns (emc, fake)."""
	import grillplat.emc2301 as mod

	fake = FakeI2C()
	if seed:
		fake.registers.update(seed)
	with mock.patch.object(mod, 'I2CDevice', return_value=fake):
		emc = mod.EMC2301(object(), address=0x2F, poles=poles)
	return emc, fake
```

Then add these tests (anywhere after the existing init tests):

```python
def test_init_sets_edges_for_default_two_poles():
	_, fake = _build_emc()
	# EDGES bits [4:3] == poles-1 == 1 (0b01) for the default 2-pole fan.
	assert (fake.registers[0x32] >> 3) & 0x03 == 1


def test_init_sets_edges_for_four_poles_preserving_other_bits():
	# Seed 0x32 with RANGE=0b11 (bits 6:5) and update-time bits 0b101; init must
	# set EDGES to 0b11 (4 poles) while preserving RANGE and update-time bits.
	_, fake = _build_emc(seed={0x32: 0b0110_0101}, poles=4)
	assert (fake.registers[0x32] >> 3) & 0x03 == 3  # EDGES == poles-1 == 3
	assert (fake.registers[0x32] >> 5) & 0x03 == 3  # RANGE preserved (0b11)
	assert fake.registers[0x32] & 0x07 == 0b101  # update-time bits preserved


def test_init_rejects_invalid_poles():
	import grillplat.emc2301 as mod

	with mock.patch.object(mod, 'I2CDevice', return_value=FakeI2C()):
		for bad in (0, 5):
			with pytest.raises(ValueError):
				mod.EMC2301(object(), address=0x2F, poles=bad)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_emc2301.py -k "edges or invalid_poles" -v`
Expected: FAIL — `test_init_sets_edges_*` fail with `KeyError: 0x32` (init never writes it), and `test_init_rejects_invalid_poles` fails because the current constructor accepts any `poles` (it has no such parameter, so it raises `TypeError`, not `ValueError` — still a failure, and it becomes `ValueError` once implemented).

- [ ] **Step 3: Add the module constants**

In `grillplat/emc2301.py`, add to the register-address block (after `_REG_PWM_DIVIDE = 0x31`):

```python
_REG_FAN_CONFIG1 = 0x32  # Fan Configuration 1: RANGE[6:5], EDGES[4:3]
```

And add near the other bit-mask constants (after `_CONFIG_WD_EN = 0x20`):

```python
_EDGES_MASK = 0x18  # Fan Config 1 bits [4:3]: tach edges, set to match poles
```

- [ ] **Step 4: Add `poles` to `__init__`**

In `grillplat/emc2301.py`, change the constructor signature and body. Replace:

```python
	def __init__(self, i2c_bus, address=_DEFAULT_ADDRESS):
		self.i2c_device = I2CDevice(i2c_bus, address)
```

with:

```python
	def __init__(self, i2c_bus, address=_DEFAULT_ADDRESS, poles=2):
		if poles not in (1, 2, 3, 4):
			raise ValueError('poles must be 1-4')
		self.poles = poles
		self.i2c_device = I2CDevice(i2c_bus, address)
```

Then, at the end of `__init__` (after the existing `self._write_register(_REG_FAN_SETTING, 0x00)` line), append the EDGES write:

```python
		# Set the tachometer EDGES field to match the fan's pole count so the
		# tach measurement is correct; preserve the RANGE and other bits.
		config1 = self._read_register(_REG_FAN_CONFIG1)
		config1 = (config1 & ~_EDGES_MASK) | ((poles - 1) << 3)
		self._write_register(_REG_FAN_CONFIG1, config1)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_emc2301.py -v`
Expected: PASS (all existing tests plus the 3 new ones).

- [ ] **Step 6: Commit**

```bash
git add grillplat/emc2301.py tests/test_emc2301.py
git commit -m "feat(emc2301): add poles arg configuring tach EDGES at init"
```

---

### Task 2: `fan_speed` property (tach read + RPM conversion)

**Files:**
- Modify: `grillplat/emc2301.py` (module constants + `fan_speed` property)
- Test: `tests/test_emc2301.py` (tach-seed helper + `fan_speed` tests)

**Interfaces:**
- Consumes: `_REG_FAN_CONFIG1` and `_read_register` from Task 1.
- Produces: read-only `EMC2301.fan_speed` property returning `float` RPM.

- [ ] **Step 1: Write the failing tests**

In `tests/test_emc2301.py`, add a tach-seeding helper (near the top, after `_build_emc`):

```python
def _seed_tach(count):
	"""Return a register seed dict encoding a 13-bit tach `count` into the
	TACH high/low registers (inverse of the driver's ((msb<<8)|lsb)>>3)."""
	return {0x3E: (count >> 5) & 0xFF, 0x3F: (count << 3) & 0xF8}
```

Then add the `fan_speed` tests:

```python
def test_fan_speed_default_range_multiplier_two():
	# Power-on default Fan Config 1 0x2B has RANGE bits 0b01 -> m=2.
	seed = {0x32: 0x2B}
	seed.update(_seed_tach(1024))
	emc, _ = _build_emc(seed=seed)
	assert emc.fan_speed == round((2 * 3932160) / 1024, 2)


def test_fan_speed_reads_range_multiplier_one_live():
	# RANGE bits 0b00 -> m=1; the same count must yield half the RPM of the
	# m=2 case, proving the multiplier is read from the register, not assumed.
	seed = {0x32: 0x03}  # RANGE=00; EDGES/UDT bits are irrelevant to m
	seed.update(_seed_tach(1024))
	emc, _ = _build_emc(seed=seed)
	assert emc.fan_speed == round((1 * 3932160) / 1024, 2)


def test_fan_speed_stalled_fan_returns_zero():
	emc, _ = _build_emc(seed=_seed_tach(0x1FFF))
	assert emc.fan_speed == 0.0


def test_fan_speed_zero_count_returns_zero():
	emc, _ = _build_emc(seed=_seed_tach(0))
	assert emc.fan_speed == 0.0
```

Note: `_build_emc`'s `__init__` runs a read-modify-write on `0x32` that sets the EDGES bits but preserves the RANGE bits [6:5] from the seed — so the RANGE values asserted above survive construction. The stall/zero tests don't seed `0x32` at all; the early `0.0` return fires before any RANGE read, so the (default) RANGE bits are irrelevant there.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_emc2301.py -k fan_speed -v`
Expected: FAIL with `AttributeError: 'EMC2301' object has no attribute 'fan_speed'`.

- [ ] **Step 3: Add the tach/RPM module constants**

In `grillplat/emc2301.py`, add to the register-address block (after the `_REG_FAN_CONFIG1` line from Task 1):

```python
_REG_TACH_HIGH = 0x3E  # TACH reading, high byte
_REG_TACH_LOW = 0x3F  # TACH reading, low byte (bits [7:3])
```

And add near the frequency tables (after `_BASE_VALUE_TO_HZ = ...`):

```python
# Tachometer -> RPM. RANGE bits [6:5] of Fan Config 1 select the multiplier m;
# with EDGES set to match the pole count, RPM = m * 3932160 / count (3932160 =
# 2 * f_TACH * 60, f_TACH = 32768 Hz). A stalled fan reads the max 13-bit count.
_RANGE_TO_MULTIPLIER = {0: 1, 1: 2, 2: 4, 3: 8}
_TACH_STALL_COUNT = 0x1FFF
_RPM_CONSTANT = 3932160
```

- [ ] **Step 4: Add the `fan_speed` property**

In `grillplat/emc2301.py`, add the property after the `pwm_frequency` setter (the last method in the class):

```python
	@property
	def fan_speed(self):
		"""Measured fan speed in RPM from the tachometer, or 0.0 if the fan is
		stopped/stalled. Reads the RANGE multiplier live so the result is
		correct regardless of how RANGE is configured."""
		msb = self._read_register(_REG_TACH_HIGH)
		lsb = self._read_register(_REG_TACH_LOW)
		count = ((msb << 8) | lsb) >> 3
		if count == 0 or count >= _TACH_STALL_COUNT:
			return 0.0
		multiplier = _RANGE_TO_MULTIPLIER[(self._read_register(_REG_FAN_CONFIG1) >> 5) & 0x03]
		return round((multiplier * _RPM_CONSTANT) / count, 2)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_emc2301.py -v`
Expected: PASS (all tests, including the 4 new `fan_speed` tests).

- [ ] **Step 6: Run the fan-controller platform suite to confirm no regression**

Run: `.venv/bin/python3 -m pytest tests/test_emc2301.py tests/test_x86_fan.py -v`
Expected: PASS — the `poles=2` default keeps `grillplat/x86_numato.py`'s `EMC2301(i2c, address=...)` construction behaviorally unchanged.

- [ ] **Step 7: Commit**

```bash
git add grillplat/emc2301.py tests/test_emc2301.py
git commit -m "feat(emc2301): add fan_speed property reading tachometer RPM"
```

---

## Self-Review Notes

- **Spec coverage:** `fan_speed` property returning RPM (Task 2), `poles` constructor arg with 1–4 validation (Task 1), EDGES init write preserving other bits (Task 1), live RANGE-multiplier read / no caching (Task 2), stalled/zero → `0.0` (Task 2), `round(x, 2)` matching the sibling driver (Task 2), existing-caller compatibility via `poles=2` default (Task 1 constraint + Task 2 Step 6 regression run). All spec sections map to a task.
- **Type consistency:** `_REG_FAN_CONFIG1 = 0x32` and `_EDGES_MASK = 0x18` are introduced in Task 1 and reused by name in Task 2. `_RANGE_TO_MULTIPLIER`/`_TACH_STALL_COUNT`/`_RPM_CONSTANT`/`_REG_TACH_HIGH`/`_REG_TACH_LOW` are introduced and used within Task 2. The `count` assembly (`((msb << 8) | lsb) >> 3`) and the test seeder (`_seed_tach`) are exact inverses.
- **No placeholders:** every code step shows the complete code and exact register/bit values.
