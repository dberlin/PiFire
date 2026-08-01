# Structured I2C Bus Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `(i2c_bus_kind, i2c_bus_num)` string pair with a dataclass hierarchy whose shape makes an invalid I2C bus configuration unrepresentable, and render that shape in the setup wizard so an operator can only fill in the fields their chosen bus kind actually has.

**Architecture:** A new `common/i2c_bus_config.py` holds six frozen dataclasses — `BasicBus`, three `KernelBus` variants, `FT232HBus`, `MCP2221Bus` — where the kind *is* the class and "exactly one of bus_num/adapter/serial" is a fact about the type rather than a rule to check. They serialize to a tagged object carrying `kind` plus exactly the live field. `common/i2c_bus.py` keeps the process-wide bus cache and the discovery primitives but takes one `I2CBus` argument instead of two strings; the three-way string sniffing in `resolve_i2c_bus()` becomes three `resolve_bus_num()` implementations. A build-gated migration converts stored configs. The wizard's 13 kind/num field pairs collapse to one composite `i2c_bus` field rendered by a new React component.

**Tech Stack:** Python 3.14, Pydantic (settings schema), Flask blueprints, React 19 + TypeScript, rstest (unit), Playwright (e2e), jj (version control), ruff (format/lint), bun (web-react).

**Spec:** `docs/superpowers/specs/2026-08-01-structured-i2c-bus-config-design.md`

## Global Constraints

- **Version control is jj, not git.** `git commit` silently works in this colocated repo and is wrong. Run `jj new` BEFORE the first Write of each task; the working copy *is* a commit, so edits are already in `@`. Describe with `jj describe --stdin` and a quoted heredoc. Never `jj squash` after editing — it moves your work into the parent.
- **Never put backticks inside double-quoted shell arguments** (`-m "..."`, `python3 -c "..."`). zsh eats them. Use `--stdin` with a quoted heredoc (`<<'EOF'`).
- **Format before every commit:** `.venv/bin/ruff format <changed .py files>`. Never `uvx ruff` — the repo pins ruff <0.16.
- **Python tests:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/`. Bare `python`/`pytest` gives false failures (PySide6 lives in the venv).
- **web-react uses bun, never npm.** Tests are **rstest**, not vitest: the API global is `rs`, and the command is `bun run test` — never `bun test`. A `vitest` import mimics a TDD-red failure.
- **web-react gates are three:** `bun run typecheck`, `bun run lint` (Biome; 2 pre-existing react-refresh warnings are expected), `bun run test`.
- `except (A, B)` → `except A, B` is ruff-canonical on Python 3.14 here. Do not "fix" it back.
- Source comments state what the code achieves, never what changed or why an agent did it.
- The stored value `extended` becomes `kernel` everywhere. There is no compatibility shim reading `extended` at runtime — the migration is the only thing that understands it.

## File Structure

**Created**

| File | Responsibility |
| --- | --- |
| `common/i2c_bus_config.py` | The `I2CBus` hierarchy, `parse_i2c_bus()`, `I2CBusConfigError`. No I/O; discovery calls are deferred imports. |
| `tests/unit/i2c/test_i2c_bus_config.py` | Round-trip, rejection, and hashability of the hierarchy. |
| `tests/unit/common/test_settings_migration_i2c.py` | Every legacy shape at every settings site. |
| `web-react/src/helpers/wizard/i2cBusTypes.ts` | The `I2cBusValue` union and `i2cBusError()`, mirroring Python. |
| `web-react/src/components/wizard/fields/I2cBusField.tsx` | The composite bus control. |
| `web-react/tests/unit/helpers/wizard/i2cBusTypes.test.ts` | Validation rules. |
| `web-react/tests/unit/components/wizard/fields/I2cBusField.test.tsx` | Rendering and kind-switching. |
| `tests/web/test_i2c_bus_rule_parity.py` | Pins the TS validation rules against Python's. |

**Deleted**

| File | Reason |
| --- | --- |
| `web-react/src/components/wizard/fields/I2cBusPicker.tsx` | Replaced by `I2cBusField`. |
| `web-react/tests/unit/components/wizard/fields/I2cBusPicker.test.tsx` | Covers the deleted component. |
| `tests/unit/i2c/test_i2c_bus_selectors.py` | Covers the deleted selector validators. |
| `tests/web/test_wizard_bus_selectors.py` | Covers the deleted `wizard_bus_selectors`. |
| `tests/unit/i2c/test_i2c_bus_num_defaults.py` | Asserts a blank `i2c_bus_num` default that no longer exists. |

**Modified:** `common/i2c_bus.py`, `common/settings_schema.py`, `common/defaults.py`, `common/settings_migration.py`, `probes/base.py`, `probes/ads1115.py`, `probes/ads1115_adafruit.py`, `probes/ads1015_adafruit.py`, `probes/mcp9600_adafruit.py`, `distance/_tof_base.py`, `grillplat/x86_numato.py`, `grillplat/ft232h_relay.py`, `tools/emc2301_tach_diag.py`, `wizard/wizard_manifest.json`, `blueprints/wizard/wizard.py`, `blueprints/api_wizard/routes.py`, `blueprints/api/routes.py`, `web-react/schema/settings.schema.json`, `web-react/src/helpers/settings/settingsTypes.gen.ts`, `web-react/src/helpers/wizard/wizardTypes.ts`, `web-react/src/helpers/wizard/probeTypes.ts`, `web-react/src/components/wizard/ModuleCard.tsx`, `web-react/src/components/wizard/probes/DeviceConfigField.tsx`, plus the test files named per task.

## Parallelization

Tasks 1 → 2 → 5 are a hard chain (the type, the factory that consumes it, the call sites that build it). Task 3 gates 4 and 6.

- **Serial:** 1, then 2 and 3 in parallel, then 4 and 5 and 6 in parallel, then 7, then 8, then 9.
- **Safe to run concurrently:** (2, 3) — disjoint files. (4, 5, 6) — disjoint files, all depending only on 1–3.
- **Must be serial:** 7 before 8 (the component consumes the endpoint's new group). 9 last (it is the whole-system gate).
- Concurrent tasks need **isolated jj workspaces** (`jj workspace add`), not just disjoint file lists. Copy `.lsp.json` into each and run `bun install` — both are gitignored, so `workspace add` skips them.
- A subagent's worktree has no Chromium: `[chromium]` tests SKIP there. Re-run any touched `tests/web/*.py` in the main checkout before merging.

---

### Task 1: The `I2CBus` dataclass hierarchy

**Files:**
- Create: `common/i2c_bus_config.py`
- Test: `tests/unit/i2c/test_i2c_bus_config.py`

**Interfaces:**
- Consumes: nothing (this is the base of the chain).
- Produces:
  - `I2CBusConfigError(Exception)`
  - `I2CBus` (abstract base), `BasicBus()`, `KernelBus` (abstract), `KernelBusNumber(bus_num: int)`, `KernelAdapterName(adapter: str)`, `KernelSerialMatch(serial: str)`, `FT232HBus(url: str = "")`, `MCP2221Bus(serial: str = "")`
  - every instance: `.kind: str` (class attribute), `.to_config() -> dict`, `.describe() -> str`
  - every `KernelBus`: `.resolve_bus_num() -> int`
  - `parse_i2c_bus(data: dict | I2CBus) -> I2CBus`
  - `BUS_KINDS: tuple[str, ...] = ("basic", "kernel", "ft232h", "mcp2221")`

- [ ] **Step 1: Start a new commit**

```bash
cd /home/dannyb/sources/PiFire && jj new -m "wip: i2c bus config hierarchy"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/i2c/test_i2c_bus_config.py`:

```python
import pytest

from common.i2c_bus_config import (
    BasicBus,
    FT232HBus,
    I2CBusConfigError,
    KernelAdapterName,
    KernelBusNumber,
    KernelSerialMatch,
    MCP2221Bus,
    parse_i2c_bus,
)

ROUND_TRIP = [
    ({"kind": "basic"}, BasicBus()),
    ({"kind": "kernel", "bus_num": 3}, KernelBusNumber(bus_num=3)),
    ({"kind": "kernel", "adapter": "CP2112"}, KernelAdapterName(adapter="CP2112")),
    ({"kind": "kernel", "serial": "0012AB34"}, KernelSerialMatch(serial="0012AB34")),
    ({"kind": "ft232h", "url": "ftdi://ftdi:232h:FT9ABC/1"}, FT232HBus(url="ftdi://ftdi:232h:FT9ABC/1")),
    ({"kind": "ft232h", "url": ""}, FT232HBus()),
    ({"kind": "mcp2221", "serial": "01234567"}, MCP2221Bus(serial="01234567")),
    ({"kind": "mcp2221", "serial": ""}, MCP2221Bus()),
]


@pytest.mark.parametrize("config,bus", ROUND_TRIP)
def test_parse_produces_the_bus_the_config_names(config, bus):
    assert parse_i2c_bus(config) == bus


@pytest.mark.parametrize("config,bus", ROUND_TRIP)
def test_to_config_round_trips(config, bus):
    assert bus.to_config() == config
    assert parse_i2c_bus(bus.to_config()) == bus


def test_parse_passes_an_already_parsed_bus_through():
    bus = KernelBusNumber(bus_num=1)
    assert parse_i2c_bus(bus) is bus


def test_every_kind_reports_its_own_kind_string():
    assert BasicBus().kind == "basic"
    assert KernelBusNumber(bus_num=1).kind == "kernel"
    assert KernelAdapterName(adapter="CP2112").kind == "kernel"
    assert KernelSerialMatch(serial="AB").kind == "kernel"
    assert FT232HBus().kind == "ft232h"
    assert MCP2221Bus().kind == "mcp2221"


def test_buses_are_hashable_so_they_can_key_the_bus_cache():
    cache = {BasicBus(): "a", KernelBusNumber(bus_num=3): "b"}
    assert cache[BasicBus()] == "a"
    assert cache[KernelBusNumber(bus_num=3)] == "b"


def test_ft232h_blank_and_one_are_the_same_device():
    """'' and '1' both mean the first FT232H, so they must be equal -- an
    unequal pair opens one physical adapter twice."""
    assert FT232HBus(url="1") == FT232HBus(url="")
    assert hash(FT232HBus(url="1")) == hash(FT232HBus(url=""))


def test_kernel_bus_num_accepts_a_numeric_string():
    assert parse_i2c_bus({"kind": "kernel", "bus_num": "3"}) == KernelBusNumber(bus_num=3)


def test_kernel_needs_exactly_one_selector():
    with pytest.raises(I2CBusConfigError, match="exactly one"):
        parse_i2c_bus({"kind": "kernel"})
    with pytest.raises(I2CBusConfigError, match="exactly one"):
        parse_i2c_bus({"kind": "kernel", "bus_num": 3, "adapter": "CP2112"})


def test_kernel_rejects_a_blank_selector():
    with pytest.raises(I2CBusConfigError):
        parse_i2c_bus({"kind": "kernel", "adapter": "   "})


def test_kernel_rejects_a_non_numeric_bus_num():
    with pytest.raises(I2CBusConfigError, match="number"):
        parse_i2c_bus({"kind": "kernel", "bus_num": "CP2112"})


def test_a_kind_rejects_a_field_belonging_to_another_kind():
    """The whole point of the hierarchy: an ft232h bus has nowhere to keep a
    kernel adapter name, so a stale one cannot survive a change of kind."""
    with pytest.raises(I2CBusConfigError, match="adapter"):
        parse_i2c_bus({"kind": "ft232h", "adapter": "CP2112"})
    with pytest.raises(I2CBusConfigError, match="bus_num"):
        parse_i2c_bus({"kind": "mcp2221", "bus_num": 3})
    with pytest.raises(I2CBusConfigError):
        parse_i2c_bus({"kind": "basic", "bus_num": 3})


def test_parse_rejects_an_unknown_kind():
    with pytest.raises(I2CBusConfigError, match="extended"):
        parse_i2c_bus({"kind": "extended", "bus_num": 3})


def test_parse_rejects_a_non_mapping():
    with pytest.raises(I2CBusConfigError):
        parse_i2c_bus("CP2112")


def test_describe_names_the_hardware():
    assert "i2c-3" in KernelBusNumber(bus_num=3).describe()
    assert "CP2112" in KernelAdapterName(adapter="CP2112").describe()
    assert "first" in FT232HBus().describe()


def test_kernel_bus_number_resolves_to_itself():
    assert KernelBusNumber(bus_num=7).resolve_bus_num() == 7


def test_kernel_adapter_name_resolves_via_find_i2c_bus(monkeypatch):
    import common.i2c_bus as i2c_bus

    monkeypatch.setattr(i2c_bus, "find_i2c_bus", lambda match: 5 if match == "CP2112" else None)
    assert KernelAdapterName(adapter="CP2112").resolve_bus_num() == 5


def test_kernel_serial_match_resolves_via_find_i2c_bus_by_serial(monkeypatch):
    import common.i2c_bus as i2c_bus

    monkeypatch.setattr(i2c_bus, "find_i2c_bus_by_serial", lambda serial: 42 if serial == "AB12" else None)
    assert KernelSerialMatch(serial="AB12").resolve_bus_num() == 42
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/i2c/test_i2c_bus_config.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'common.i2c_bus_config'`.

- [ ] **Step 4: Write the implementation**

Create `common/i2c_bus_config.py`:

```python
"""Typed I2C bus configuration.

A bus is one of four kinds, and each kind addresses hardware in its own
namespace: `basic` uses the board's own pins, `kernel` a /dev/i2c-N adapter,
`ft232h` a pyftdi URL over libusb, `mcp2221` a device serial over USB HID.
Carrying that as a `(kind, selector)` string pair gave one field five meanings
and no way to say "this field does not apply", so a kernel adapter name could
sit in a config whose kind was `ft232h`, naming a bus pyftdi can never open.

Here the kind IS the class. `BasicBus` has no field to hold a stale selector,
and the three `KernelBus` variants make "a kernel bus is addressed exactly one
way" a property of the type rather than a rule someone has to remember to
check.

Discovery imports are deferred: common.i2c_bus imports this module, so a
top-level import back would be a cycle.
"""

from dataclasses import dataclass
from typing import ClassVar

BUS_KINDS = ("basic", "kernel", "ft232h", "mcp2221")


class I2CBusConfigError(Exception):
    """Raised for an I2C bus configuration that cannot be built or opened."""


@dataclass(frozen=True)
class I2CBus:
    """Base for the four bus kinds. Frozen so an instance can key the
    process-wide bus cache: two devices naming the same hardware then share one
    open bus, which is what stops a single USB adapter being opened twice."""

    kind: ClassVar[str] = ""

    def to_config(self):
        """The stored mapping form: `kind` plus exactly the live field."""
        raise NotImplementedError

    def describe(self):
        """One human phrase naming the hardware, for logs and error messages."""
        raise NotImplementedError


@dataclass(frozen=True)
class BasicBus(I2CBus):
    kind: ClassVar[str] = "basic"

    def to_config(self):
        return {"kind": "basic"}

    def describe(self):
        return "the board's integrated I2C bus"


@dataclass(frozen=True)
class KernelBus(I2CBus):
    """A kernel i2c-dev adapter, addressed exactly one of three ways."""

    kind: ClassVar[str] = "kernel"

    def resolve_bus_num(self):
        """The /dev/i2c-N number this bus names, discovering it if needed."""
        raise NotImplementedError


@dataclass(frozen=True)
class KernelBusNumber(KernelBus):
    bus_num: int

    def resolve_bus_num(self):
        return self.bus_num

    def to_config(self):
        return {"kind": "kernel", "bus_num": self.bus_num}

    def describe(self):
        return f"/dev/i2c-{self.bus_num}"


@dataclass(frozen=True)
class KernelAdapterName(KernelBus):
    adapter: str

    def resolve_bus_num(self):
        from common.i2c_bus import find_i2c_bus

        return find_i2c_bus(self.adapter)

    def to_config(self):
        return {"kind": "kernel", "adapter": self.adapter}

    def describe(self):
        return f"the kernel I2C adapter named {self.adapter!r}"


@dataclass(frozen=True)
class KernelSerialMatch(KernelBus):
    serial: str

    def resolve_bus_num(self):
        from common.i2c_bus import find_i2c_bus_by_serial

        return find_i2c_bus_by_serial(self.serial)

    def to_config(self):
        return {"kind": "kernel", "serial": self.serial}

    def describe(self):
        return f"the kernel I2C adapter with USB serial {self.serial!r}"


@dataclass(frozen=True)
class FT232HBus(I2CBus):
    kind: ClassVar[str] = "ft232h"
    url: str = ""

    def __post_init__(self):
        # '' and '1' both mean "the first FT232H" (grillplat/ft232h.py), so they
        # have to compare equal -- an unequal pair keys two cache entries and
        # opens one physical adapter twice.
        if self.url == "1":
            object.__setattr__(self, "url", "")

    def to_config(self):
        return {"kind": "ft232h", "url": self.url}

    def describe(self):
        return f"the FT232H at {self.url!r}" if self.url else "the first FT232H found"


@dataclass(frozen=True)
class MCP2221Bus(I2CBus):
    kind: ClassVar[str] = "mcp2221"
    serial: str = ""

    def to_config(self):
        return {"kind": "mcp2221", "serial": self.serial}

    def describe(self):
        return f"the MCP2221 with serial {self.serial!r}" if self.serial else "the first MCP2221 found"


_KERNEL_VARIANTS = {
    "bus_num": KernelBusNumber,
    "adapter": KernelAdapterName,
    "serial": KernelSerialMatch,
}


def parse_i2c_bus(data):
    """Build the I2CBus a stored mapping names.

    Raises rather than falling back to BasicBus: a bus we cannot name is a
    configuration the operator has to fix, and quietly opening the board's own
    pins instead would drive a grill from the wrong hardware.
    """
    if isinstance(data, I2CBus):
        return data
    if not isinstance(data, dict):
        raise I2CBusConfigError(f"An I2C bus configuration must be a mapping, got {data!r}.")

    kind = str(data.get("kind", "")).strip().lower()
    fields = set(data) - {"kind"}

    if kind == "basic":
        if fields:
            raise I2CBusConfigError(f"A basic I2C bus takes no other fields; got {sorted(fields)}.")
        return BasicBus()

    if kind == "kernel":
        unknown = sorted(fields - set(_KERNEL_VARIANTS))
        if unknown:
            raise I2CBusConfigError(f"A kernel I2C bus has no field(s) {unknown}.")
        chosen = sorted(fields & set(_KERNEL_VARIANTS))
        if len(chosen) != 1:
            raise I2CBusConfigError(
                "A kernel I2C bus is addressed by exactly one of bus_num, adapter or serial; "
                f"got {chosen if chosen else 'none of them'}."
            )
        name = chosen[0]
        value = data[name]
        if name == "bus_num":
            try:
                return KernelBusNumber(bus_num=int(value))
            except (TypeError, ValueError):
                raise I2CBusConfigError(f"A kernel bus_num must be a number, got {value!r}.") from None
        text = str(value).strip()
        if not text:
            raise I2CBusConfigError(f"A kernel I2C bus addressed by {name} needs a value.")
        return _KERNEL_VARIANTS[name](**{name: text})

    if kind == "ft232h":
        unknown = sorted(fields - {"url"})
        if unknown:
            raise I2CBusConfigError(f"An ft232h I2C bus has no field(s) {unknown}.")
        return FT232HBus(url=str(data.get("url", "")).strip())

    if kind == "mcp2221":
        unknown = sorted(fields - {"serial"})
        if unknown:
            raise I2CBusConfigError(f"An mcp2221 I2C bus has no field(s) {unknown}.")
        return MCP2221Bus(serial=str(data.get("serial", "")).strip())

    raise I2CBusConfigError(f"Unknown I2C bus kind {kind!r}; expected one of {', '.join(BUS_KINDS)}.")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/i2c/test_i2c_bus_config.py -q`
Expected: all PASS.

- [ ] **Step 6: Format and commit**

```bash
cd /home/dannyb/sources/PiFire && .venv/bin/ruff format common/i2c_bus_config.py tests/unit/i2c/test_i2c_bus_config.py
.venv/bin/ruff check common/i2c_bus_config.py tests/unit/i2c/test_i2c_bus_config.py
jj describe --stdin <<'EOF'
feat(i2c): model an I2C bus as a typed hierarchy, not a string pair

The kind is now the class. BasicBus has no field to hold a stale selector, and
the three KernelBus variants make "a kernel bus is addressed exactly one way" a
property of the type rather than a rule to check, so a kernel adapter name can
no longer survive in a config whose kind is ft232h.
EOF
```

---

### Task 2: `open_i2c_bus` takes one bus object

**Files:**
- Modify: `common/i2c_bus.py`
- Modify: `probes/base.py:25`
- Delete: `tests/unit/i2c/test_i2c_bus_selectors.py`
- Test: `tests/unit/i2c/test_i2c_bus.py`, `tests/unit/ft232h/test_ft232h_bus.py`, `tests/unit/i2c/test_i2c_bus_wizard_validation.py`

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces:
  - `open_i2c_bus(bus) -> busio.I2C`-compatible. One argument, an `I2CBus` or the mapping `parse_i2c_bus` accepts.
  - `configured_bus_kinds(settings, probe_map) -> set[str]` — unchanged name, now reading `config["i2c_bus"]` objects.
  - `I2CBusConfigError` remains importable from `common.i2c_bus` (re-exported from `common.i2c_bus_config`, same object, so existing `except` clauses keep working).
  - Gone: `resolve_i2c_bus`, `_canonical_selector`, `validate_bus_selector`, `validate_bus_selectors`, `configured_bus_selectors`, `_EXTENDED_ADAPTER_NAMES`.
  - Unchanged: `find_i2c_bus`, `find_i2c_bus_by_serial`, `discover_extended_i2c_buses`, `discover_ft232h_devices`, `discover_mcp2221_devices`, `validate_bus_kinds`, `assert_clean_blinka_env`, `reset_bus_state`, `USB_HID_KINDS`, `_LockedI2C`.

- [ ] **Step 1: Start a new commit**

```bash
cd /home/dannyb/sources/PiFire && jj new -m "wip: open_i2c_bus takes a bus object"
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/i2c/test_i2c_bus.py`:

```python
def test_open_i2c_bus_takes_a_bus_object(monkeypatch):
    from common.i2c_bus_config import KernelBusNumber

    i2c_bus.reset_bus_state()
    monkeypatch.setattr(i2c_bus, "_construct_bus", lambda bus: f"bus-{bus.describe()}")
    assert i2c_bus.open_i2c_bus(KernelBusNumber(bus_num=3)) == "bus-/dev/i2c-3"


def test_open_i2c_bus_accepts_the_stored_mapping(monkeypatch):
    i2c_bus.reset_bus_state()
    monkeypatch.setattr(i2c_bus, "_construct_bus", lambda bus: f"bus-{bus.kind}")
    assert i2c_bus.open_i2c_bus({"kind": "basic"}) == "bus-basic"


def test_open_i2c_bus_caches_on_the_bus_object(monkeypatch):
    """Two devices naming the same hardware share one open bus."""
    from common.i2c_bus_config import KernelAdapterName

    i2c_bus.reset_bus_state()
    calls = []
    monkeypatch.setattr(i2c_bus, "_construct_bus", lambda bus: calls.append(bus) or object())
    a = i2c_bus.open_i2c_bus(KernelAdapterName(adapter="CP2112"))
    b = i2c_bus.open_i2c_bus({"kind": "kernel", "adapter": "CP2112"})
    assert a is b
    assert len(calls) == 1


def test_open_i2c_bus_still_refuses_basic_beside_a_usb_hid_bus(monkeypatch):
    from common.i2c_bus_config import FT232HBus

    i2c_bus.reset_bus_state()
    monkeypatch.setattr(i2c_bus, "_construct_bus", lambda bus: object())
    i2c_bus.open_i2c_bus(FT232HBus())
    with pytest.raises(I2CBusConfigError):
        i2c_bus.open_i2c_bus(BasicBus())


def test_configured_bus_kinds_reads_the_bus_objects():
    settings = {
        "platform": {
            "devices": {"distance": {"i2c_bus": {"kind": "kernel", "adapter": "CP2112"}}},
            "fan_controller": {"i2c_bus": {"kind": "basic"}},
        }
    }
    probe_map = {"probe_devices": [{"device": "ADS1115_0", "config": {"i2c_bus": {"kind": "ft232h", "url": ""}}}]}
    assert i2c_bus.configured_bus_kinds(settings, probe_map) == {"kernel", "basic", "ft232h"}


def test_configured_bus_kinds_skips_a_device_with_no_bus():
    assert i2c_bus.configured_bus_kinds({}, None) == set()
    assert i2c_bus.configured_bus_kinds(None, {"probe_devices": [{"device": "SPI_0", "config": {}}]}) == set()


def test_probes_base_no_longer_reexports_resolve_i2c_bus():
    """resolve_i2c_bus is gone; find_i2c_bus stays a discovery primitive."""
    import common.i2c_bus as cib
    import probes.base as base

    assert base.find_i2c_bus is cib.find_i2c_bus
    assert not hasattr(base, "resolve_i2c_bus")
    assert not hasattr(cib, "resolve_i2c_bus")
```

Add `BasicBus` to that file's imports:

```python
from common.i2c_bus_config import BasicBus
```

- [ ] **Step 3: Run to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/i2c/test_i2c_bus.py -q`
Expected: the new tests FAIL — `open_i2c_bus()` still requires two positional args and `resolve_i2c_bus` still exists.

- [ ] **Step 4: Rewrite the factory**

In `common/i2c_bus.py`:

Replace the `I2CBusConfigError` class definition with a re-export, and import the hierarchy:

```python
from common.i2c_bus_config import (  # noqa: F401  # I2CBusConfigError is public here
    BasicBus,
    FT232HBus,
    I2CBus,
    I2CBusConfigError,
    KernelBus,
    MCP2221Bus,
    parse_i2c_bus,
)
```

Delete `resolve_i2c_bus`, `_canonical_selector`, `validate_bus_selector`, `validate_bus_selectors`, `configured_bus_selectors` and the `_EXTENDED_ADAPTER_NAMES` constant outright.

Replace `_construct_bus` and `open_i2c_bus`:

```python
def _construct_bus(bus):
    if isinstance(bus, BasicBus):
        import board
        import busio

        logger.debug("open_i2c_bus[basic]: opening Blinka board.SCL/SDA")
        return busio.I2C(board.SCL, board.SDA)
    if isinstance(bus, KernelBus):
        from adafruit_extended_bus import ExtendedI2C

        bus_num = bus.resolve_bus_num()
        logger.debug("open_i2c_bus[kernel]: opening /dev/i2c-%s (from %s)", bus_num, bus.describe())
        return ExtendedI2C(bus_num)
    if isinstance(bus, FT232HBus):
        from grillplat import ft232h

        return ft232h.construct_i2c_bus(bus.url)
    if isinstance(bus, MCP2221Bus):
        from grillplat import mcp2221

        return mcp2221.construct_i2c_bus(bus.serial)
    raise I2CBusConfigError(f"Unknown I2C bus {bus!r}.")


def open_i2c_bus(bus):
    """Return a busio.I2C-compatible bus for `bus`, opening it if needed.

    `bus` is an I2CBus, or the stored mapping parse_i2c_bus accepts. Open buses
    are cached process-wide keyed by the bus object itself, so two devices that
    name the same hardware share one bus -- a single USB adapter opened twice
    yields two controllers fighting over one MPSSE engine.
    """
    bus = parse_i2c_bus(bus)
    with _cache_lock:
        validate_bus_kinds(_opened_kinds | {bus.kind})
        opened = _bus_cache.get(bus)
        if opened is None:
            logger.debug("open_i2c_bus: opening %s", bus.describe())
            opened = _construct_bus(bus)
            _bus_cache[bus] = opened
            _opened_kinds.add(bus.kind)
        return opened
```

Update the `_bus_cache` comment to `# I2CBus -> bus object`.

Replace `configured_bus_kinds`:

```python
def configured_bus_kinds(settings, probe_map):
    """Every I2C bus kind configured across probe devices, the distance sensor,
    and the platform fan controller. Used to validate a whole wizard config
    before it is installed."""
    kinds = set()
    for device in (probe_map or {}).get("probe_devices", []):
        bus = (device.get("config") or {}).get("i2c_bus")
        if bus:
            kinds.add(parse_i2c_bus(bus).kind)
    platform = (settings or {}).get("platform", {})
    for section in (
        (platform.get("devices", {}) or {}).get("distance", {}) or {},
        platform.get("fan_controller", {}) or {},
    ):
        if section.get("i2c_bus"):
            kinds.add(parse_i2c_bus(section["i2c_bus"]).kind)
    return kinds
```

In `probes/base.py:25`, drop the removed name:

```python
from common.i2c_bus import find_i2c_bus  # noqa: F401  # public re-export
```

and update the comment above it so it no longer promises `resolve_i2c_bus`.

- [ ] **Step 5: Update the tests that exercised the old API**

- `tests/unit/i2c/test_i2c_bus.py` — delete `test_resolve_i2c_bus_numeric_returns_int` and `test_resolve_i2c_bus_serial_prefix_dispatches` (their behavior now lives in Task 1's `resolve_bus_num` tests); drop `resolve_i2c_bus` from the imports; replace `test_probes_base_reexports_bus_helpers` with the new `test_probes_base_no_longer_reexports_resolve_i2c_bus`; rewrite every `open_i2c_bus("mcp2221", "BBBB")` call as `open_i2c_bus(MCP2221Bus(serial="BBBB"))` and `open_i2c_bus("basic")` as `open_i2c_bus(BasicBus())`.
- `tests/unit/ft232h/test_ft232h_bus.py` — rewrite `open_i2c_bus("ft232h", "")` as `open_i2c_bus(FT232HBus())` and `("ft232h", "1")` as `FT232HBus(url="1")`; the assertion `a is b` still holds and is now proved by `__post_init__`.
- `tests/unit/i2c/test_i2c_bus_wizard_validation.py` — update its `i2c_bus_kind`/`i2c_bus_num` fixtures to `i2c_bus` objects.
- Delete `tests/unit/i2c/test_i2c_bus_selectors.py` — every symbol it imports is gone.

- [ ] **Step 6: Run the i2c suites**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/i2c tests/unit/ft232h -q`
Expected: all PASS.

- [ ] **Step 7: Format and commit**

```bash
cd /home/dannyb/sources/PiFire && .venv/bin/ruff format common/i2c_bus.py probes/base.py tests/unit/i2c tests/unit/ft232h
.venv/bin/ruff check common/i2c_bus.py probes/base.py tests/unit/i2c tests/unit/ft232h
jj describe --stdin <<'EOF'
refactor(i2c): open_i2c_bus takes one bus object

The bus cache is keyed by the frozen dataclass itself, which retires
_canonical_selector, and resolve_i2c_bus's three-way string sniffing becomes
three resolve_bus_num implementations. The selector validators added to catch a
kernel adapter name stranded on a USB-HID bus go with them: that state is now
unconstructible, so there is nothing left to check.
EOF
```

---

### Task 3: Settings schema, defaults, and generated types

**Files:**
- Modify: `common/settings_schema.py:116-127` (`_DistanceDeviceConfig`), `:192-196` (`_FanControllerConfig`)
- Modify: `common/defaults.py:77-78`, `:106-107`
- Modify: `web-react/schema/settings.schema.json` (regenerated)
- Modify: `web-react/src/helpers/settings/settingsTypes.gen.ts` (regenerated)
- Test: `tests/unit/common/test_settings_schema.py`

**Interfaces:**
- Consumes: Task 1's serialized shapes (as literal dicts; no Python import of the hierarchy into the schema).
- Produces: `settings["platform"]["devices"]["distance"]["i2c_bus"]` and `settings["platform"]["fan_controller"]["i2c_bus"]`, both defaulting to `{"kind": "basic"}`. `i2c_bus_kind` and `i2c_bus_num` no longer exist at either path.

- [ ] **Step 1: Start a new commit**

```bash
cd /home/dannyb/sources/PiFire && jj new -m "wip: i2c_bus in the settings schema"
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/common/test_settings_schema.py`:

```python
def test_i2c_bus_accepts_every_variant():
    from common.settings_schema import validate_settings_tree

    for bus in (
        {"kind": "basic"},
        {"kind": "kernel", "bus_num": 3},
        {"kind": "kernel", "adapter": "CP2112"},
        {"kind": "kernel", "serial": "0012AB34"},
        {"kind": "ft232h", "url": "ftdi://ftdi:232h:FT9ABC/1"},
        {"kind": "mcp2221", "serial": "01234567"},
    ):
        settings = copy.deepcopy(default_settings())
        settings["platform"]["devices"]["distance"]["i2c_bus"] = bus
        settings["platform"]["fan_controller"]["i2c_bus"] = bus
        validate_settings_tree(settings)


def test_i2c_bus_rejects_a_field_from_another_kind():
    """The schema mirrors the dataclass hierarchy: extra="forbid" on each
    variant is what makes only one member of the union match."""
    from common.settings_schema import SettingsValidationError, validate_settings_tree

    settings = copy.deepcopy(default_settings())
    settings["platform"]["fan_controller"]["i2c_bus"] = {"kind": "ft232h", "adapter": "CP2112"}
    with pytest.raises(SettingsValidationError):
        validate_settings_tree(settings)


def test_i2c_bus_defaults_to_basic():
    settings = default_settings()
    assert settings["platform"]["devices"]["distance"]["i2c_bus"] == {"kind": "basic"}
    assert settings["platform"]["fan_controller"]["i2c_bus"] == {"kind": "basic"}
    assert "i2c_bus_kind" not in settings["platform"]["fan_controller"]
    assert "i2c_bus_num" not in settings["platform"]["fan_controller"]
```

- [ ] **Step 3: Run to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_settings_schema.py -q`
Expected: FAIL — `i2c_bus` is an unmodeled key, and the defaults still carry `i2c_bus_kind`.

- [ ] **Step 4: Add the union to the schema**

In `common/settings_schema.py`, above `_DistanceDeviceConfig`:

```python
# The four I2C bus kinds, mirroring common/i2c_bus_config.py's dataclass
# hierarchy. `kind` alone cannot discriminate -- three kernel variants share it
# -- so this is a left-to-right union whose members each forbid the other
# variants' fields. That is what makes {"kind":"kernel","adapter":"X"} match
# exactly one member, and what makes a stale field from a previous kind a
# validation error rather than a silently carried value.
class _BasicBus(_Section):
    kind: Literal["basic"] = "basic"


class _KernelBusNumber(_Section):
    kind: Literal["kernel"] = "kernel"
    bus_num: int


class _KernelAdapterName(_Section):
    kind: Literal["kernel"] = "kernel"
    adapter: str


class _KernelSerialMatch(_Section):
    kind: Literal["kernel"] = "kernel"
    serial: str


class _FT232hBus(_Section):
    kind: Literal["ft232h"] = "ft232h"
    url: str = ""


class _MCP2221Bus(_Section):
    kind: Literal["mcp2221"] = "mcp2221"
    serial: str = ""


I2CBusConfig = Annotated[
    Union[_BasicBus, _KernelBusNumber, _KernelAdapterName, _KernelSerialMatch, _FT232hBus, _MCP2221Bus],
    Field(union_mode="left_to_right"),
]
```

In `_DistanceDeviceConfig`, replace lines 119-120:

```python
    i2c_bus: I2CBusConfig = _BasicBus()
```

In `_FanControllerConfig`, replace lines 194-195 with the same line.

- [ ] **Step 5: Update the defaults authority**

In `common/defaults.py`, replace lines 77-78 with:

```python
                "i2c_bus": {"kind": "basic"},  # VL53L0X/VL53L4CD/VL53L1X only; see common/i2c_bus_config.py
```

and lines 106-107 with:

```python
            "i2c_bus": {"kind": "basic"},  # fan controller bus; see common/i2c_bus_config.py
```

- [ ] **Step 6: Run the schema tests**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_settings_schema.py -q`
Expected: all PASS, including the existing defaults-parity test.

- [ ] **Step 7: Regenerate the JSON schema and TS types**

```bash
cd /home/dannyb/sources/PiFire && uv run python -m common.settings_schema > web-react/schema/settings.schema.json
cd web-react && bun run gen:types
```

- [ ] **Step 8: Confirm the drift check passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q -k "schema"`
Expected: PASS, including the schema-drift test.

- [ ] **Step 9: Format and commit**

```bash
cd /home/dannyb/sources/PiFire && .venv/bin/ruff format common/settings_schema.py common/defaults.py tests/unit/common/test_settings_schema.py
.venv/bin/ruff check common/settings_schema.py common/defaults.py
jj describe --stdin <<'EOF'
feat(settings): model the I2C bus as a tagged union

Replaces i2c_bus_kind/i2c_bus_num at the distance sensor and the fan controller
with one i2c_bus object. extra="forbid" on each union member is what makes a
field belonging to another kind a validation error instead of a value carried
silently past a change of bus type.
EOF
```

---

### Task 4: Migrate stored configurations

**Files:**
- Modify: `common/settings_migration.py` (append a new gated block to `upgrade_settings`)
- Create: `tests/unit/common/test_settings_migration_i2c.py`

**Interfaces:**
- Consumes: Task 3's schema (the migrated tree must validate against it).
- Produces: `_migrate_i2c_buses(settings)` — rewrites all three sites in place, returns nothing. Idempotent.

- [ ] **Step 1: Start a new commit**

```bash
cd /home/dannyb/sources/PiFire && jj new -m "wip: migrate legacy i2c bus settings"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/common/test_settings_migration_i2c.py`:

```python
"""Every legacy (i2c_bus_kind, i2c_bus_num) shape, at every site that stores one.

The pair is unreadable to the new code, so a config that fails to migrate does
not degrade -- it stops the device from opening at all. Pure string parsing: the
migration runs before any hardware is touched, so it can never probe.
"""

import copy

import pytest

from common.defaults import default_settings
from common.settings_migration import _migrate_i2c_buses

CASES = [
    ({"i2c_bus_kind": "basic", "i2c_bus_num": "CP2112"}, {"kind": "basic"}),
    ({"i2c_bus_kind": "basic", "i2c_bus_num": ""}, {"kind": "basic"}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": "3"}, {"kind": "kernel", "bus_num": 3}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": 3}, {"kind": "kernel", "bus_num": 3}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": "serial:AB12"}, {"kind": "kernel", "serial": "AB12"}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": "SERIAL:AB12"}, {"kind": "kernel", "serial": "AB12"}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": "CP2112"}, {"kind": "kernel", "adapter": "CP2112"}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": ""}, {"kind": "basic"}),
    ({"i2c_bus_kind": "ft232h", "i2c_bus_num": ""}, {"kind": "ft232h", "url": ""}),
    ({"i2c_bus_kind": "ft232h", "i2c_bus_num": "1"}, {"kind": "ft232h", "url": ""}),
    (
        {"i2c_bus_kind": "ft232h", "i2c_bus_num": "ftdi://ftdi:232h:FT9/1"},
        {"kind": "ft232h", "url": "ftdi://ftdi:232h:FT9/1"},
    ),
    ({"i2c_bus_kind": "ft232h", "i2c_bus_num": "CP2112"}, {"kind": "ft232h", "url": ""}),
    ({"i2c_bus_kind": "ft232h", "i2c_bus_num": "serial:AB12"}, {"kind": "ft232h", "url": ""}),
    ({"i2c_bus_kind": "mcp2221", "i2c_bus_num": ""}, {"kind": "mcp2221", "serial": ""}),
    ({"i2c_bus_kind": "mcp2221", "i2c_bus_num": "0123"}, {"kind": "mcp2221", "serial": "0123"}),
    ({"i2c_bus_kind": "mcp2221", "i2c_bus_num": "CP2112"}, {"kind": "mcp2221", "serial": ""}),
    ({"i2c_bus_match": "CP2112"}, {"kind": "kernel", "adapter": "CP2112"}),
]


def _legacy_settings(section):
    settings = copy.deepcopy(default_settings())
    settings["platform"]["devices"]["distance"] = dict(section)
    settings["platform"]["fan_controller"] = {"chip": "emc2101", "address": "0x4c", **section}
    settings["probe_settings"]["probe_map"]["probe_devices"] = [
        {"device": "ADS1115_0", "module": "ads1115_adafruit", "config": dict(section)}
    ]
    return settings


@pytest.mark.parametrize("legacy,expected", CASES)
def test_every_legacy_shape_migrates_at_every_site(legacy, expected):
    settings = _legacy_settings(legacy)
    _migrate_i2c_buses(settings)

    assert settings["platform"]["devices"]["distance"]["i2c_bus"] == expected
    assert settings["platform"]["fan_controller"]["i2c_bus"] == expected
    assert settings["probe_settings"]["probe_map"]["probe_devices"][0]["config"]["i2c_bus"] == expected


@pytest.mark.parametrize("legacy,expected", CASES)
def test_the_legacy_keys_are_removed(legacy, expected):
    settings = _legacy_settings(legacy)
    _migrate_i2c_buses(settings)

    for section in (
        settings["platform"]["devices"]["distance"],
        settings["platform"]["fan_controller"],
        settings["probe_settings"]["probe_map"]["probe_devices"][0]["config"],
    ):
        assert "i2c_bus_kind" not in section
        assert "i2c_bus_num" not in section
        assert "i2c_bus_match" not in section


def test_migration_is_idempotent():
    """upgrade_settings can run twice across an upgrade/downgrade cycle."""
    settings = _legacy_settings({"i2c_bus_kind": "extended", "i2c_bus_num": "CP2112"})
    _migrate_i2c_buses(settings)
    once = copy.deepcopy(settings)
    _migrate_i2c_buses(settings)
    assert settings == once


def test_a_device_with_no_bus_at_all_is_left_alone():
    settings = copy.deepcopy(default_settings())
    settings["probe_settings"]["probe_map"]["probe_devices"] = [
        {"device": "SPI_0", "module": "max31865", "config": {"cs": 0}}
    ]
    _migrate_i2c_buses(settings)
    assert settings["probe_settings"]["probe_map"]["probe_devices"][0]["config"] == {"cs": 0}


def test_the_migrated_tree_validates():
    from common.settings_schema import validate_settings_tree

    settings = _legacy_settings({"i2c_bus_kind": "extended", "i2c_bus_num": "serial:AB12"})
    _migrate_i2c_buses(settings)
    validate_settings_tree(settings)
```

- [ ] **Step 3: Run to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_settings_migration_i2c.py -q`
Expected: collection error — `cannot import name '_migrate_i2c_buses'`.

- [ ] **Step 4: Write the migration**

In `common/settings_migration.py`, add above `upgrade_settings`:

```python
def _legacy_bus_to_config(section):
    """The tagged i2c_bus object a legacy (i2c_bus_kind, i2c_bus_num) pair meant.

    String parsing only -- this runs during settings load, long before any
    adapter can be probed, so an adapter name stays an adapter name rather than
    being resolved to the serial behind it.
    """
    kind = str(section.get("i2c_bus_kind", "")).strip().lower()
    # Pre basic/extended installs stored only the bridge name.
    selector = section.get("i2c_bus_num", section.get("i2c_bus_match", ""))
    selector = "" if selector is None else str(selector).strip()

    if not kind:
        kind = "extended" if selector else "basic"

    if kind == "extended":
        if not selector:
            # find_i2c_bus("") substring-matches every adapter and raises, so
            # this configuration could never open a bus. The board's own pins
            # are the honest repair.
            write_log("I2C bus: 'extended' with no bus selected; falling back to the integrated bus.")
            return {"kind": "basic"}
        if selector.lower().startswith("serial:"):
            return {"kind": "kernel", "serial": selector.split(":", 1)[1].strip()}
        if selector.isdigit():
            return {"kind": "kernel", "bus_num": int(selector)}
        return {"kind": "kernel", "adapter": selector}

    if kind in ("ft232h", "mcp2221"):
        field = "url" if kind == "ft232h" else "serial"
        # '1' is the historical ft232h default and means "the first one found",
        # the same as blank. Normalize before deciding anything, so a legitimate
        # default is never mistaken for a leftover.
        if kind == "ft232h" and selector == "1":
            selector = ""
        # A selector naming a kernel adapter cannot address a USB-HID device.
        # Dropping it leaves "the first one found", which is what a fresh
        # install of this kind means.
        if kind == "ft232h":
            stranded = bool(selector) and not selector.lower().startswith("ftdi://")
        else:
            stranded = selector.lower().startswith("serial:") or selector.lower() in ("cp2112", "mcp2221")
        if stranded:
            write_log(f"I2C bus: dropping {selector!r}, which does not name a {kind} device.")
            selector = ""
        return {"kind": kind, field: selector}

    # A kind we do not recognize (including an explicit None or a non-string)
    # cannot tell us what its selector meant, so the selector goes with it.
    if selector:
        write_log(f"I2C bus: kind {section.get('i2c_bus_kind')!r} is not a bus kind; dropping {selector!r}.")
    return {"kind": "basic"}


def _i2c_bus_sections(settings):
    """Every mapping in the tree that stores one I2C bus configuration."""
    platform = settings.get("platform", {}) or {}
    yield (platform.get("devices", {}) or {}).get("distance")
    yield platform.get("fan_controller")
    probe_map = (settings.get("probe_settings", {}) or {}).get("probe_map", {}) or {}
    for device in probe_map.get("probe_devices", []) or []:
        yield device.get("config")


def _migrate_i2c_buses(settings):
    """Rewrite every legacy (i2c_bus_kind, i2c_bus_num) pair as one i2c_bus
    object. Idempotent: a section that already has i2c_bus is left alone."""
    for section in _i2c_bus_sections(settings):
        if not isinstance(section, dict):
            continue
        legacy = {"i2c_bus_kind", "i2c_bus_num", "i2c_bus_match"} & set(section)
        if "i2c_bus" not in section and legacy:
            section["i2c_bus"] = _legacy_bus_to_config(section)
        for key in legacy:
            section.pop(key, None)
```

Then append this block at the end of `upgrade_settings`, before `return settings`:

```python
    """ Check if upgrading from previous to v1.11 or from v1.11.0 build 71 """
    if (prev_ver[0] == 1 and prev_ver[1] == 11 and settings["versions"].get("build", 0) <= 71) or (
        prev_ver[0] == 1 and prev_ver[1] < 11
    ):
        _migrate_i2c_buses(settings)
```

- [ ] **Step 5: Run the migration tests**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_settings_migration_i2c.py -q`
Expected: all PASS.

- [ ] **Step 6: Format and commit**

```bash
cd /home/dannyb/sources/PiFire && .venv/bin/ruff format common/settings_migration.py tests/unit/common/test_settings_migration_i2c.py
.venv/bin/ruff check common/settings_migration.py tests/unit/common/test_settings_migration_i2c.py
jj describe --stdin <<'EOF'
feat(settings): migrate legacy i2c_bus_kind/i2c_bus_num pairs

Parses each stored pair into the tagged i2c_bus object at all three sites,
including the pre basic/extended i2c_bus_match key. A selector naming a kernel
adapter on an ft232h or mcp2221 bus is dropped and logged: it named a bus
neither could ever open, and "the first one found" is what remains.
EOF
```

---

### Task 5: Python call sites

**Files:**
- Modify: `probes/ads1115_adafruit.py:51-88`, `probes/ads1015_adafruit.py:50-87`, `probes/mcp9600_adafruit.py:52-86`, `probes/ads1115.py:46-99`
- Modify: `distance/_tof_base.py:28-43`
- Modify: `grillplat/x86_numato.py:63-99`
- Modify: `grillplat/ft232h_relay.py:103`
- Modify: `tools/emc2301_tach_diag.py:60-72`
- Test: `tests/unit/probes/`, `tests/unit/distance/`, `tests/unit/platform/test_x86_bus_discovery.py`

**Interfaces:**
- Consumes: `parse_i2c_bus`, `KernelBus`, `FT232HBus` (Task 1); `open_i2c_bus(bus)` (Task 2).
- Produces: `ADSDevice(i2c_bus_addr=..., bus=...)` and `KTTDevice(i2c_bus_addr=..., bus=..., tc_type=...)` — the two-string pair replaced by one `bus` keyword. `i2c_bus_addr` stays a separate argument: it addresses a chip, not a bus.

- [ ] **Step 1: Start a new commit**

```bash
cd /home/dannyb/sources/PiFire && jj new -m "wip: i2c call sites take a bus object"
```

- [ ] **Step 2: Write the failing test**

Append to `tests/unit/platform/test_x86_bus_discovery.py`:

```python
def test_fan_controller_opens_the_configured_bus():
    from common.i2c_bus_config import KernelAdapterName

    _, open_bus = _build_platform({"i2c_bus": {"kind": "kernel", "adapter": "CP2112"}})
    open_bus.assert_called_once_with(KernelAdapterName(adapter="CP2112"))


def test_fan_controller_defaults_to_the_integrated_bus():
    from common.i2c_bus_config import BasicBus

    _, open_bus = _build_platform({})
    open_bus.assert_called_once_with(BasicBus())
```

Append to `tests/unit/distance/test_tof_base.py`:

```python
def test_tof_opens_the_configured_bus(monkeypatch):
    from common.i2c_bus_config import KernelBusNumber

    opened = []
    monkeypatch.setattr("distance._tof_base.open_i2c_bus", lambda bus: opened.append(bus) or object())
    _make_sensor({"distance": {"i2c_bus": {"kind": "kernel", "bus_num": 3}}})
    assert opened == [KernelBusNumber(bus_num=3)]
```

(Use the file's existing sensor-construction helper in place of `_make_sensor` if it is named differently — read the file first.)

- [ ] **Step 3: Run to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/platform/test_x86_bus_discovery.py tests/unit/distance/test_tof_base.py -q`
Expected: FAIL — `open_i2c_bus` is still called with two strings.

- [ ] **Step 4: Update the probe devices**

In `probes/ads1115_adafruit.py` (and identically in `probes/ads1015_adafruit.py`):

```python
    def __init__(self, i2c_bus_addr=0x48, bus=None):
        self.i2c = open_i2c_bus(bus or BasicBus())
```

and at the caller inside the same file:

```python
        bus = parse_i2c_bus(self.device_info["config"].get("i2c_bus") or {"kind": "basic"})
        try:
            self.device = ADSDevice(i2c_bus_addr=i2c_bus_addr, bus=bus)
        except Exception:
            ...
                f"(i2c bus {bus.describe()}, address=0x{i2c_bus_addr:02X})."
```

Import at the top of each: `from common.i2c_bus_config import BasicBus, parse_i2c_bus`.

Apply the same change to `probes/mcp9600_adafruit.py`, keeping its `tc_type` argument:

```python
    def __init__(self, i2c_bus_addr=0x67, bus=None, tc_type="K"):
        self.i2c = open_i2c_bus(bus or BasicBus())
```

In `probes/ads1115.py`, the direct-smbus2 branch:

```python
    def __init__(self, i2c_bus_addr=0x48, bus=None):
        bus = bus or BasicBus()
        ...
        if isinstance(bus, KernelBus):
            self.smbus = smbus2.SMBus(bus.resolve_bus_num())
            self.ads.i2c = self.smbus
```

with `from common.i2c_bus_config import BasicBus, KernelBus, parse_i2c_bus` at the top, replacing the `from probes.base import ProbeInterface, resolve_i2c_bus` import with `from probes.base import ProbeInterface`.

- [ ] **Step 5: Update the distance sensor**

In `distance/_tof_base.py`, replace lines 28-30 and 42-43:

```python
        self.bus = parse_i2c_bus(distance_pins.get("i2c_bus") or {"kind": "basic"})
```

```python
    def _open_i2c_bus(self):
        return open_i2c_bus(self.bus)
```

Import: `from common.i2c_bus_config import parse_i2c_bus`.

- [ ] **Step 6: Update the platforms**

In `grillplat/x86_numato.py`, replace the whole kind-guessing block (lines 66-74) with:

```python
        self.bus = parse_i2c_bus(fan_cfg.get("i2c_bus") or {"kind": "basic"})
```

and line 99 with:

```python
        i2c = open_i2c_bus(self.bus)
```

In `grillplat/ft232h_relay.py:103`:

```python
        self._ft232h_bus = open_i2c_bus(FT232HBus(url=self.url))
```

with `from common.i2c_bus_config import FT232HBus` added to its imports.

- [ ] **Step 7: Update the diagnostic tool**

In `tools/emc2301_tach_diag.py`, replace its `if bus_kind == "extended": ExtendedI2C(resolve_i2c_bus(bus_num))` block with:

```python
    i2c = open_i2c_bus(fan_cfg.get("i2c_bus") or {"kind": "basic"})
```

replacing the `from probes.base import resolve_i2c_bus` import with `from common.i2c_bus import open_i2c_bus`, and dropping the now-unused `ExtendedI2C` import. This tool has no test coverage; verify by hand that it imports:

Run: `cd /home/dannyb/sources/PiFire && uv run python -c 'import tools.emc2301_tach_diag'`
Expected: no output, exit 0.

- [ ] **Step 8: Run the affected suites**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes tests/unit/distance tests/unit/platform tests/unit/ft232h -q`
Expected: all PASS. Update any remaining fixture that still passes `i2c_bus_kind`/`i2c_bus_num`.

- [ ] **Step 9: Format and commit**

```bash
cd /home/dannyb/sources/PiFire && .venv/bin/ruff format probes distance grillplat tools tests/unit/probes tests/unit/distance tests/unit/platform
.venv/bin/ruff check probes distance grillplat tools
jj describe --stdin <<'EOF'
refactor(i2c): build a bus object at every call site

Every device now parses its stored i2c_bus once and hands the object to
open_i2c_bus. x86_numato loses its kind-guessing, ads1115's smbus2 path asks
isinstance(bus, KernelBus) rather than comparing a string, and the emc2301
diagnostic stops resolving the bus itself.
EOF
```

---

### Task 6: Manifest, wizard plumbing, and routes

**Files:**
- Modify: `wizard/wizard_manifest.json` (13 sites)
- Modify: `blueprints/wizard/wizard.py` (`_constrain_to_options`, `wizard_bus_kinds`; delete `wizard_bus_selectors`)
- Modify: `blueprints/api_wizard/routes.py` (imports, `/finish`, `/probes/validate-bus-kinds`)
- Modify: `blueprints/api/routes.py` (imports, `_api_post_probe_map`)
- Delete: `tests/web/test_wizard_bus_selectors.py`, `tests/unit/i2c/test_i2c_bus_num_defaults.py`
- Test: `tests/web/test_wizard_helpers.py`, `tests/web/test_api_wizard.py`, `tests/web/test_wizard_bus_kinds.py`, `tests/unit/wizard/test_manifest_schema_conformance.py`

**Interfaces:**
- Consumes: Task 3's schema, Task 2's `configured_bus_kinds`.
- Produces: manifest deps of `"type": "i2c_bus"` whose `settings` path names an object and whose `default` is `{"kind": "basic"}`. `get_settings_dependencies_values` returns that dict verbatim under the dep's key.

- [ ] **Step 1: Start a new commit**

```bash
cd /home/dannyb/sources/PiFire && jj new -m "wip: one composite i2c_bus wizard field"
```

- [ ] **Step 2: Write the failing tests**

Replace `test_settings_dependency_values_passes_through_a_dep_with_no_options` in `tests/web/test_wizard_helpers.py` and add:

```python
def test_settings_dependency_values_passes_an_i2c_bus_object_through():
    """An i2c_bus dep's value is an object, not a scalar; _constrain_to_options
    must not stringify it on the way to the client."""
    settings = {"platform": {"devices": {"distance": {"i2c_bus": {"kind": "kernel", "adapter": "CP2112"}}}}}
    module_data = {
        "settings_dependencies": {
            "device_distance_i2c_bus": {
                "type": "i2c_bus",
                "settings": ["platform", "devices", "distance", "i2c_bus"],
            }
        }
    }
    assert get_settings_dependencies_values(settings, module_data) == {
        "device_distance_i2c_bus": {"kind": "kernel", "adapter": "CP2112"}
    }
```

Add to `tests/unit/wizard/test_manifest_schema_conformance.py`:

```python
def test_every_i2c_bus_dep_is_a_composite_with_an_object_default():
    """The composite carries a dict default so the conformance sweep above
    actually writes one into the tree and validates it."""
    from common.common import read_wizard

    found = 0
    for modules in read_wizard().get("modules", {}).values():
        for module in modules.values():
            for dep, spec in (module.get("settings_dependencies") or {}).items():
                if dep.endswith("i2c_bus"):
                    found += 1
                    assert spec["type"] == "i2c_bus"
                    assert spec["default"] == {"kind": "basic"}
                assert not dep.endswith(("i2c_bus_kind", "i2c_bus_num"))
    assert found == 8
```

- [ ] **Step 3: Run to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_wizard_helpers.py tests/unit/wizard -q`
Expected: FAIL — the manifest still ships `i2c_bus_kind`/`i2c_bus_num` pairs.

- [ ] **Step 4: Rewrite the manifest**

For each of the 8 grillplatform modules (`custom`, `pcb_2.00a`, `pcb_3.01a`, `pcb_pwm`, `pcb_4.x.x`, `x86_numato` ×2 — its own `i2c_bus_kind` and its `device_distance_i2c_bus_kind` — and `ft232h_relay`), replace the `*_i2c_bus_kind` and `*_i2c_bus_num` dependency pair with a single entry. For the distance-sensor ones:

```json
"device_distance_i2c_bus": {
  "friendly_name": "Distance Sensor I2C Bus",
  "description": "How the I2C distance sensor (VL53L0X/VL53L4CD/VL53L1X) is reached. Ignored by the HCSR04.",
  "type": "i2c_bus",
  "default": { "kind": "basic" },
  "settings": ["platform", "devices", "distance", "i2c_bus"]
}
```

For x86_numato's fan-controller bus, the same entry named `i2c_bus`, with `"friendly_name": "Fan Controller I2C Bus"` and `"settings": ["platform", "fan_controller", "i2c_bus"]`.

For each of the 5 probe modules (`ads1115`, `ads1115_adafruit`, `ads1015_adafruit`, `mcp9600_adafruit`, `prototype`), replace the two `device_specific.config` entries labelled `i2c_bus_kind` and `i2c_bus_num` with:

```json
{
  "label": "i2c_bus",
  "friendly_name": "I2C Bus",
  "description": "How this device's I2C bus is reached.",
  "type": "i2c_bus",
  "default": { "kind": "basic" },
  "hidden": false
}
```

- [ ] **Step 5: Guard the scalar assumption**

In `blueprints/wizard/wizard.py`, at the top of `_constrain_to_options`:

```python
    if not isinstance(value, str) or not options:
        return value
```

replacing the existing `if not options: return value`, and add a sentence to its docstring:

```
    A composite dependency (i2c_bus) reads as an object rather than a scalar and
    carries no option list; it passes through untouched.
```

Delete `wizard_bus_selectors` entirely. Rewrite `wizard_bus_kinds` to read the composite:

```python
def wizard_bus_kinds(wizardInstallInfo, wizardData):
    """Every I2C bus kind the pending wizard selection would install."""
    kinds = set()
    for module, info in (wizardInstallInfo.get("modules") or {}).items():
        module_settings = info.get("settings") or {}
        deps = ((wizardData.get("modules") or {}).get(module) or {}).get(
            info.get("profile_selected", [""])[0], {}
        ).get("settings_dependencies") or {}
        for name, dep in deps.items():
            if dep.get("type") == "i2c_bus" and module_settings.get(name):
                kinds.add(parse_i2c_bus(module_settings[name]).kind)
    return kinds
```

(Read the existing `wizard_bus_kinds` first and keep its module/profile lookup exactly as written — only the inner per-dependency test changes.)

- [ ] **Step 6: Drop the deleted validators from the routes**

In `blueprints/api_wizard/routes.py`: remove `wizard_bus_selectors`, `configured_bus_selectors` and `validate_bus_selectors` from the imports; delete line 467 (`validate_bus_selectors(wizard_bus_selectors(...))`) and line 538 (`validate_bus_selectors(configured_bus_selectors(...))`). The `validate_bus_kinds` calls beside them stay.

In `blueprints/api/routes.py`: remove `configured_bus_selectors` and `validate_bus_selectors` from the import block and delete line 430. `validate_bus_kinds(configured_bus_kinds(settings, probe_map))` stays.

Delete `tests/web/test_wizard_bus_selectors.py` and `tests/unit/i2c/test_i2c_bus_num_defaults.py`.

- [ ] **Step 7: Update the remaining web tests**

In `tests/web/test_api_wizard.py`, the 422 `bus_conflict` test at line 544 posts `{"i2c_bus_kind": "ft232h", "i2c_bus_num": "CP2112"}` — a shape that is now unconstructible. Replace it with a conflict that survives: a probe on `{"kind": "basic"}` beside one on `{"kind": "ft232h", "url": ""}`, which `validate_bus_kinds` still rejects. Assert the response is 422 with `bus_conflict` and that the detail names the process-global Blinka constraint.

In `tests/web/test_wizard_bus_kinds.py`, update the fixtures from kind/num pairs to `i2c_bus` objects.

- [ ] **Step 8: Run the web and wizard suites**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web tests/unit/wizard -q`
Expected: all PASS, including `test_every_manifest_option_can_be_written`.

- [ ] **Step 9: Format and commit**

```bash
cd /home/dannyb/sources/PiFire && .venv/bin/ruff format blueprints tests/web tests/unit/wizard
.venv/bin/ruff check blueprints tests/web tests/unit/wizard
jj describe --stdin <<'EOF'
feat(wizard): one composite i2c_bus field per device

Thirteen kind/num dependency pairs become one dependency whose settings path
names an object. _constrain_to_options passes a non-string through, and the
selector validators drop out of /finish, /probes/validate-bus-kinds and the live
probe-map save: the state they guarded is now unconstructible.
EOF
```

---

### Task 7: Discovery endpoint

**Files:**
- Modify: `blueprints/api_wizard/routes.py:262-300` (`wizard_scan`)
- Test: `tests/web/test_api_wizard.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `POST /api/wizard/scan {"kind": "kernel"}` returns three groups — `"By Bus Number"`, `"By Adapter Name"`, `"By Serial"` — each `{value, label}` where `value` is the JSON-encoded field the picker writes. Task 8's component reads them by title.

- [ ] **Step 1: Start a new commit**

```bash
cd /home/dannyb/sources/PiFire && jj new -m "wip: kernel discovery groups"
```

- [ ] **Step 2: Write the failing test**

Append to `tests/web/test_api_wizard.py`:

```python
def test_scan_kernel_offers_all_three_ways_to_address_an_adapter(client, monkeypatch):
    monkeypatch.setattr(
        "blueprints.api_wizard.routes.discover_extended_i2c_buses",
        lambda: [{"bus_num": 7, "name": "CP2112 SMBus Bridge", "serial": "AB12"}],
    )
    body = client.post("/api/wizard/scan", json={"kind": "kernel"}).get_json()
    titles = [group["title"] for group in body["groups"]]
    assert titles == ["By Bus Number", "By Adapter Name", "By Serial"]
    by_title = {group["title"]: group["items"] for group in body["groups"]}
    assert by_title["By Bus Number"][0]["value"] == "7"
    assert by_title["By Adapter Name"][0]["value"] == "CP2112 SMBus Bridge"
    assert by_title["By Serial"][0]["value"] == "AB12"


def test_scan_no_longer_answers_to_the_old_kind_name(client):
    body = client.post("/api/wizard/scan", json={"kind": "extended"}).get_json()
    assert body["groups"] == []
```

- [ ] **Step 3: Run to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -q -k scan_kernel`
Expected: FAIL — the endpoint matches `"extended"` and returns two groups.

- [ ] **Step 4: Update the endpoint**

In `wizard_scan`, change `if kind == "extended":` to `if kind == "kernel":` and make the group list three entries:

```python
            adapters = discover_extended_i2c_buses()
            groups = [
                {
                    "title": "By Bus Number",
                    "items": [
                        {"value": str(a["bus_num"]), "label": f"{a['name']} (bus {a['bus_num']})"} for a in adapters
                    ],
                },
                {
                    "title": "By Adapter Name",
                    "items": [{"value": a["name"], "label": f"{a['name']} (bus {a['bus_num']})"} for a in adapters],
                },
                {
                    "title": "By Serial",
                    "items": [
                        {"value": a["serial"], "label": f"{a['name']} [{a['serial']}]"}
                        for a in adapters
                        if a.get("serial")
                    ],
                },
            ]
```

Update the docstring's discovery-shape note to say the kernel kind yields three groups, one per way a `KernelBus` can be addressed.

- [ ] **Step 5: Run to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -q`
Expected: all PASS.

- [ ] **Step 6: Format and commit**

```bash
cd /home/dannyb/sources/PiFire && .venv/bin/ruff format blueprints/api_wizard/routes.py tests/web/test_api_wizard.py
jj describe --stdin <<'EOF'
feat(wizard): discovery offers each way to address a kernel adapter

The scan answers to 'kernel' and returns a group per KernelBus variant, so
picking a discovered adapter fills the field the operator actually selected
rather than one spelling of it.
EOF
```

---

### Task 8: The React composite field

**Files:**
- Create: `web-react/src/helpers/wizard/i2cBusTypes.ts`
- Create: `web-react/src/components/wizard/fields/I2cBusField.tsx`
- Create: `web-react/tests/unit/helpers/wizard/i2cBusTypes.test.ts`
- Create: `web-react/tests/unit/components/wizard/fields/I2cBusField.test.tsx`
- Delete: `web-react/src/components/wizard/fields/I2cBusPicker.tsx`, `web-react/tests/unit/components/wizard/fields/I2cBusPicker.test.tsx`
- Modify: `web-react/src/helpers/wizard/wizardTypes.ts`, `web-react/src/helpers/wizard/probeTypes.ts`, `web-react/src/components/wizard/ModuleCard.tsx`, `web-react/src/components/wizard/probes/DeviceConfigField.tsx`, `web-react/src/components/wizard/wizard.css`

**Interfaces:**
- Consumes: Task 6's `"type": "i2c_bus"` deps, Task 7's three kernel groups.
- Produces:
  - `type I2cBusValue` — the union mirroring `to_config()`.
  - `i2cBusError(bus: I2cBusValue): string | null`
  - `<I2cBusField dep value onChange onScan />` where `value: I2cBusValue` and `onChange: (v: I2cBusValue) => void`.
  - `SettingsDependency.type` widens to `"i2c_bus_num" | "usb_serial_device" | "i2c_bus"`; `SettingsDependency.default` widens to `string | I2cBusValue`.
  - `WizardState.settings_dep_values` and `ModuleValues.settings` widen to `Record<string, string | I2cBusValue | null>`; `ModuleCard.onDepChange` widens to `(key: string, value: string | I2cBusValue) => void`.

- [ ] **Step 1: Start a new commit**

```bash
cd /home/dannyb/sources/PiFire && jj new -m "wip: I2cBusField"
```

- [ ] **Step 2: Write the failing validation tests**

Create `web-react/tests/unit/helpers/wizard/i2cBusTypes.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import { type I2cBusValue, i2cBusError } from "../../../../src/helpers/wizard/i2cBusTypes";

describe("i2cBusError", () => {
  it("accepts a basic bus, which has nothing to fill in", () => {
    expect(i2cBusError({ kind: "basic" })).toBe(null);
  });

  it("requires a kernel bus number", () => {
    expect(i2cBusError({ kind: "kernel", bus_num: null })).toMatch(/bus number/i);
  });

  it("survives a JSON round trip, the way a saved draft does", () => {
    const unfilled: I2cBusValue = { kind: "kernel", bus_num: null };
    expect(JSON.parse(JSON.stringify(unfilled))).toEqual(unfilled);
  });

  it("accepts a kernel bus number", () => {
    expect(i2cBusError({ kind: "kernel", bus_num: 3 })).toBe(null);
  });

  it("requires a kernel adapter name", () => {
    expect(i2cBusError({ kind: "kernel", adapter: "  " })).toMatch(/adapter/i);
    expect(i2cBusError({ kind: "kernel", adapter: "CP2112" })).toBe(null);
  });

  it("requires a kernel serial", () => {
    expect(i2cBusError({ kind: "kernel", serial: "" })).toMatch(/serial/i);
    expect(i2cBusError({ kind: "kernel", serial: "AB12" })).toBe(null);
  });

  it("lets an ft232h url be blank, meaning the first device found", () => {
    expect(i2cBusError({ kind: "ft232h", url: "" })).toBe(null);
    expect(i2cBusError({ kind: "ft232h", url: "ftdi://ftdi:232h:FT9/1" })).toBe(null);
  });

  it("rejects an ft232h url that is not a pyftdi url", () => {
    expect(i2cBusError({ kind: "ft232h", url: "CP2112" })).toMatch(/ftdi:\/\//);
  });

  it("lets an mcp2221 serial be blank, meaning the first device found", () => {
    expect(i2cBusError({ kind: "mcp2221", serial: "" })).toBe(null);
  });
});
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd web-react && bun run test tests/unit/helpers/wizard/i2cBusTypes.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 4: Write the types and validation**

Create `web-react/src/helpers/wizard/i2cBusTypes.ts`:

```ts
/** The four I2C bus kinds, mirroring common/i2c_bus_config.py. Each variant
 *  carries only its own field, so switching kind replaces the object rather
 *  than leaving a value behind that the new kind cannot use. */
export type I2cBusValue =
  | { kind: "basic" }
  /** `bus_num` is null while the operator has selected "Bus number" but not
   *  typed one yet. JSON.stringify(NaN) is `null`, so an unfilled number has to
   *  BE null or a saved draft reads back as something it was not written as. */
  | { kind: "kernel"; bus_num: number | null }
  | { kind: "kernel"; adapter: string }
  | { kind: "kernel"; serial: string }
  | { kind: "ft232h"; url: string }
  | { kind: "mcp2221"; serial: string };

export type KernelBy = "bus_num" | "adapter" | "serial";

export const BUS_KIND_LABELS: Record<I2cBusValue["kind"], string> = {
  basic: "Basic (integrated I2C bus)",
  kernel: "Kernel (/dev/i2c-N adapter)",
  ft232h: "FT232H (USB)",
  mcp2221: "MCP2221 (USB)",
};

export const KERNEL_BY_LABELS: Record<KernelBy, string> = {
  bus_num: "Bus number",
  adapter: "Adapter name",
  serial: "USB serial",
};

/** The empty value for a kind, used when the operator switches kinds. */
export function emptyBus(kind: I2cBusValue["kind"], by: KernelBy = "adapter"): I2cBusValue {
  if (kind === "basic") return { kind: "basic" };
  if (kind === "ft232h") return { kind: "ft232h", url: "" };
  if (kind === "mcp2221") return { kind: "mcp2221", serial: "" };
  if (by === "bus_num") return { kind: "kernel", bus_num: null };
  if (by === "serial") return { kind: "kernel", serial: "" };
  return { kind: "kernel", adapter: "" };
}

export function kernelBy(bus: I2cBusValue): KernelBy {
  if (bus.kind !== "kernel") return "adapter";
  if ("bus_num" in bus) return "bus_num";
  if ("serial" in bus) return "serial";
  return "adapter";
}

/** The per-field format rules. The XOR between kernel selectors is not checked
 *  here -- the type makes it unrepresentable. Python keeps the authoritative
 *  copy of these rules for configs that arrive by import or by hand; see
 *  tests/web/test_i2c_bus_rule_parity.py. */
export function i2cBusError(bus: I2cBusValue): string | null {
  if (bus.kind === "kernel") {
    if ("bus_num" in bus) {
      return bus.bus_num !== null && Number.isInteger(bus.bus_num) && bus.bus_num >= 0
        ? null
        : "Enter the bus number, the N in /dev/i2c-N.";
    }
    if ("adapter" in bus) {
      return bus.adapter.trim() ? null : "Enter the adapter name, e.g. CP2112.";
    }
    return bus.serial.trim() ? null : "Enter the adapter's USB serial.";
  }
  if (bus.kind === "ft232h") {
    if (!bus.url.trim()) return null;
    return bus.url.startsWith("ftdi://") ? null : "An FT232H URL starts with ftdi:// — or leave it blank for the first one found.";
  }
  return null;
}
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd web-react && bun run test tests/unit/helpers/wizard/i2cBusTypes.test.ts`
Expected: all PASS.

- [ ] **Step 6: Write the failing component tests**

Create `web-react/tests/unit/components/wizard/fields/I2cBusField.test.tsx`:

```tsx
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { I2cBusField } from "../../../../../src/components/wizard/fields/I2cBusField";
import type { I2cBusValue } from "../../../../../src/helpers/wizard/i2cBusTypes";

const dep = { friendly_name: "I2C Bus", settings: [], type: "i2c_bus" as const };
const noScan = () => Promise.resolve({ groups: [] });

function renderField(value: I2cBusValue, onChange = rs.fn()) {
  render(<I2cBusField dep={dep} value={value} onChange={onChange} onScan={noScan} />);
  return onChange;
}

afterEach(cleanup);

describe("I2cBusField", () => {
  it("renders no other field for a basic bus", () => {
    renderField({ kind: "basic" });
    expect(screen.queryByRole("textbox")).toBe(null);
    expect(screen.queryByRole("radio")).toBe(null);
  });

  it("renders the three ways to address a kernel bus", () => {
    renderField({ kind: "kernel", adapter: "CP2112" });
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    expect(screen.getByDisplayValue("CP2112")).toBeTruthy();
  });

  it("renders one input for ft232h and one for mcp2221", () => {
    renderField({ kind: "ft232h", url: "ftdi://ftdi:232h:FT9/1" });
    expect(screen.getByDisplayValue("ftdi://ftdi:232h:FT9/1")).toBeTruthy();
    expect(screen.queryByRole("radio")).toBe(null);
  });

  it("replaces the value when the kind changes, so no stale field survives", () => {
    const onChange = renderField({ kind: "kernel", adapter: "CP2112" });
    fireEvent.change(screen.getByLabelText("I2C Bus"), { target: { value: "ft232h" } });
    expect(onChange).toHaveBeenCalledWith({ kind: "ft232h", url: "" });
  });

  it("replaces the value when the kernel radio changes", () => {
    const onChange = renderField({ kind: "kernel", adapter: "CP2112" });
    fireEvent.click(screen.getByLabelText("Bus number"));
    expect(onChange).toHaveBeenCalledWith({ kind: "kernel", bus_num: null });
  });

  it("shows the validation error inline while typing", () => {
    renderField({ kind: "kernel", adapter: "" });
    expect(screen.getByRole("alert").textContent).toMatch(/adapter/i);
  });

  it("shows no error for a blank ft232h url", () => {
    renderField({ kind: "ft232h", url: "" });
    expect(screen.queryByRole("alert")).toBe(null);
  });

  it("writes a discovered value into the selected kernel field", async () => {
    const onChange = rs.fn();
    render(
      <I2cBusField
        dep={dep}
        value={{ kind: "kernel", adapter: "" }}
        onChange={onChange}
        onScan={() =>
          Promise.resolve({
            groups: [{ title: "By Adapter Name", items: [{ value: "CP2112", label: "CP2112 (bus 7)" }] }],
          })
        }
      />,
    );
    fireEvent.click(screen.getByText("Discover"));
    fireEvent.click(await screen.findByText("CP2112 (bus 7)"));
    expect(onChange).toHaveBeenCalledWith({ kind: "kernel", adapter: "CP2112" });
  });

  it("renders nothing when the dep is hidden", () => {
    const { container } = render(
      <I2cBusField dep={{ ...dep, hidden: true }} value={{ kind: "basic" }} onChange={rs.fn()} onScan={noScan} />,
    );
    expect(container.textContent).toBe("");
  });
});
```

- [ ] **Step 7: Run to verify they fail**

Run: `cd web-react && bun run test tests/unit/components/wizard/fields/I2cBusField.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 8: Write the component**

Create `web-react/src/components/wizard/fields/I2cBusField.tsx`:

```tsx
import { useId, useState } from "react";
import {
  BUS_KIND_LABELS,
  emptyBus,
  type I2cBusValue,
  i2cBusError,
  KERNEL_BY_LABELS,
  type KernelBy,
  kernelBy,
} from "../../../helpers/wizard/i2cBusTypes";
import type { ScanResult, SettingsDependency } from "../../../helpers/wizard/wizardTypes";
import { DiscoveryPanel } from "../DiscoveryPanel";

export interface I2cBusFieldProps {
  dep: SettingsDependency;
  value: I2cBusValue;
  onChange: (value: I2cBusValue) => void;
  onScan: (kind: I2cBusValue["kind"]) => Promise<ScanResult>;
}

/** The kernel discovery groups, keyed by the field each fills. Picking a row
 *  writes into the field the radio has selected, so a serial picked from the
 *  serial group produces a serial-addressed bus and not an adapter name. */
const GROUP_FOR: Record<KernelBy, string> = {
  bus_num: "By Bus Number",
  adapter: "By Adapter Name",
  serial: "By Serial",
};

export function I2cBusField({ dep, value, onChange, onScan }: I2cBusFieldProps) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ScanResult | null>(null);
  const kindId = useId();
  const by = kernelBy(value);

  if (dep.hidden) return null;

  const error = i2cBusError(value);

  async function handleDiscover() {
    setLoading(true);
    try {
      setResult(await onScan(value.kind));
    } finally {
      setLoading(false);
    }
  }

  function handlePick(picked: string) {
    if (value.kind === "kernel") {
      if (by === "bus_num") onChange({ kind: "kernel", bus_num: Number(picked) });
      else if (by === "serial") onChange({ kind: "kernel", serial: picked });
      else onChange({ kind: "kernel", adapter: picked });
    } else if (value.kind === "ft232h") {
      onChange({ kind: "ft232h", url: picked });
    } else if (value.kind === "mcp2221") {
      onChange({ kind: "mcp2221", serial: picked });
    }
  }

  // Only the group matching the selected radio is offered: the others would
  // write a value into the field the operator did not choose.
  const scoped: ScanResult | null =
    result && value.kind === "kernel"
      ? { ...result, groups: (result.groups ?? []).filter((g) => g.title === GROUP_FOR[by]) }
      : result;

  return (
    <div className="pf-field pf-i2c-bus-field">
      <label className="pf-field-label" htmlFor={kindId}>
        {dep.friendly_name}
      </label>
      <select
        id={kindId}
        className="pf-input"
        value={value.kind}
        onChange={(e) => onChange(emptyBus(e.target.value as I2cBusValue["kind"], by))}
      >
        {(Object.keys(BUS_KIND_LABELS) as I2cBusValue["kind"][]).map((kind) => (
          <option key={kind} value={kind}>
            {BUS_KIND_LABELS[kind]}
          </option>
        ))}
      </select>

      {value.kind === "kernel" && (
        <fieldset className="pf-i2c-bus-kernel">
          {(Object.keys(KERNEL_BY_LABELS) as KernelBy[]).map((option) => (
            <label key={option}>
              <input
                type="radio"
                name={`${kindId}-by`}
                checked={by === option}
                aria-label={KERNEL_BY_LABELS[option]}
                onChange={() => onChange(emptyBus("kernel", option))}
              />
              {KERNEL_BY_LABELS[option]}
            </label>
          ))}
          {"bus_num" in value ? (
            <input
              className="pf-input"
              inputMode="numeric"
              aria-label={KERNEL_BY_LABELS.bus_num}
              value={value.bus_num === null ? "" : String(value.bus_num)}
              onChange={(e) => {
                const n = Number.parseInt(e.target.value, 10);
                onChange({ kind: "kernel", bus_num: Number.isNaN(n) ? null : n });
              }}
            />
          ) : "serial" in value ? (
            <input
              className="pf-input"
              aria-label={KERNEL_BY_LABELS.serial}
              value={value.serial}
              onChange={(e) => onChange({ kind: "kernel", serial: e.target.value })}
            />
          ) : (
            <input
              className="pf-input"
              aria-label={KERNEL_BY_LABELS.adapter}
              value={value.adapter}
              onChange={(e) => onChange({ kind: "kernel", adapter: e.target.value })}
            />
          )}
        </fieldset>
      )}

      {value.kind === "ft232h" && (
        <input
          className="pf-input"
          aria-label="FT232H URL"
          placeholder="blank = the first FT232H found"
          value={value.url}
          onChange={(e) => onChange({ kind: "ft232h", url: e.target.value })}
        />
      )}

      {value.kind === "mcp2221" && (
        <input
          className="pf-input"
          aria-label="MCP2221 serial"
          placeholder="blank = the first MCP2221 found"
          value={value.serial}
          onChange={(e) => onChange({ kind: "mcp2221", serial: e.target.value })}
        />
      )}

      {dep.description && <span className="pf-field-hint">{dep.description}</span>}
      {error && <span role="alert" className="pf-field-error">{error}</span>}
      {value.kind !== "basic" && (
        <button type="button" onClick={() => void handleDiscover()} disabled={loading}>
          {loading ? "Scanning…" : "Discover"}
        </button>
      )}
      {scoped && <DiscoveryPanel result={scoped} onPick={handlePick} />}
    </div>
  );
}
```

- [ ] **Step 9: Run to verify they pass**

Run: `cd web-react && bun run test tests/unit/components/wizard/fields/I2cBusField.test.tsx`
Expected: all PASS.

- [ ] **Step 10: Widen the types and wire the component in**

In `web-react/src/helpers/wizard/wizardTypes.ts`:

```ts
import type { I2cBusValue } from "./i2cBusTypes";
```

- `SettingsDependency.type` → `"i2c_bus_num" | "usb_serial_device" | "i2c_bus"`
- `SettingsDependency.default` → `string | I2cBusValue`, and rewrite its doc comment: the manifest fallback for an `i2c_bus` dep is the object `{kind: "basic"}`.
- `WizardState.settings_dep_values` → `Record<WizardSection, Record<string, string | I2cBusValue | null>>`
- `ModuleValues.settings` → `Record<string, string | I2cBusValue | null>`

In `web-react/src/helpers/wizard/probeTypes.ts`, add `"i2c_bus"` to `ProbeFieldType`.

In `ModuleCard.tsx`: widen `depValues` to `Record<string, string | I2cBusValue | null>` and `onDepChange` to `(key: string, value: string | I2cBusValue) => void`; replace the `dep.type === "i2c_bus_num"` branch with:

```tsx
    if (dep.type === "i2c_bus") {
      const bus = (typeof value === "object" && value !== null ? value : { kind: "basic" }) as I2cBusValue;
      return (
        <I2cBusField
          key={key}
          dep={dep}
          value={bus}
          onChange={(v) => onDepChange(key, v)}
          onScan={(kind) => scan(baseUrl, { kind })}
        />
      );
    }
```

and adjust the surrounding `const value = depValues[key] ?? ""` so the scalar branches still receive a string.

In `DeviceConfigField.tsx`: replace the `case "i2c_bus_num":` branch with a `case "i2c_bus":` rendering `<I2cBusField>` against `value`, and drop the `I2cBusPicker` import.

Delete `web-react/src/components/wizard/fields/I2cBusPicker.tsx` and `web-react/tests/unit/components/wizard/fields/I2cBusPicker.test.tsx`.

Add to `wizard.css`:

```css
.pf-i2c-bus-kernel {
  border: none;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin: 0;
  padding: 0;
}
.pf-field-error {
  color: var(--pf-danger, #c0392b);
}
```

- [ ] **Step 11: Run the three web-react gates**

```bash
cd /home/dannyb/sources/PiFire/web-react && bun run typecheck && bun run lint && bun run test
```

Expected: typecheck clean; lint 0 errors (2 pre-existing react-refresh warnings); all tests PASS. Typecheck is what surfaces every consumer of the widened `settings_dep_values` — fix each at the site it reports.

- [ ] **Step 12: Commit**

```bash
cd /home/dannyb/sources/PiFire && jj describe --stdin <<'EOF'
feat(wizard): render the I2C bus as one composite field

Basic renders nothing beyond the kind select; kernel renders a three-way radio
and the one matching input; ft232h and mcp2221 render their own. Changing kind
or radio replaces the value object, so a field the new kind cannot use has
nowhere to persist, and the format rules report inline instead of at Finish.
EOF
```

---

### Task 9: Rule parity, fixtures, and the full gate

**Files:**
- Create: `tests/web/test_i2c_bus_rule_parity.py`
- Modify: `web-react/tests/e2e/fixtures/probe-modules.json`, `wizard-state.json`, `settings.json`

**Interfaces:**
- Consumes: everything.
- Produces: nothing new — this task proves the system.

- [ ] **Step 1: Start a new commit**

```bash
cd /home/dannyb/sources/PiFire && jj new -m "wip: i2c bus rule parity and fixtures"
```

- [ ] **Step 2: Write the parity test**

Create `tests/web/test_i2c_bus_rule_parity.py`:

```python
"""The TS validation rules and the Python ones must agree.

i2cBusTypes.ts duplicates the per-field format checks so the wizard can report
them on a keystroke rather than an HTTP round trip. Python stays authoritative
for configurations that arrive by settings import or a hand-edited backup. Two
copies of a rule drift; this reads the TS source and asserts the pairs still
match, so the drift is a red test rather than a config the wizard accepts and
the control process rejects.
"""

import pathlib

import pytest

from common.i2c_bus_config import I2CBusConfigError, parse_i2c_bus

TS = pathlib.Path(__file__).resolve().parents[2] / "web-react/src/helpers/wizard/i2cBusTypes.ts"

REJECTED = [
    {"kind": "kernel", "adapter": ""},
    {"kind": "kernel", "serial": ""},
    {"kind": "kernel", "bus_num": "CP2112"},
    # What a saved draft holds when the operator picked "Bus number" and typed
    # nothing: i2cBusTypes.ts writes null, and JSON carries it across unchanged.
    {"kind": "kernel", "bus_num": None},
]

ACCEPTED = [
    {"kind": "basic"},
    {"kind": "kernel", "bus_num": 3},
    {"kind": "kernel", "adapter": "CP2112"},
    {"kind": "kernel", "serial": "AB12"},
    {"kind": "ft232h", "url": ""},
    {"kind": "ft232h", "url": "ftdi://ftdi:232h:FT9/1"},
    {"kind": "mcp2221", "serial": ""},
]


@pytest.mark.parametrize("config", REJECTED)
def test_python_rejects_what_the_ts_rules_reject(config):
    with pytest.raises(I2CBusConfigError):
        parse_i2c_bus(config)


@pytest.mark.parametrize("config", ACCEPTED)
def test_python_accepts_what_the_ts_rules_accept(config):
    parse_i2c_bus(config)


def test_the_ts_rules_cover_the_same_fields():
    """A field checked on one side and not the other is the drift this catches."""
    source = TS.read_text()
    for token in ("bus_num", "adapter", "serial", "ftdi://"):
        assert token in source, f"i2cBusTypes.ts no longer mentions {token}"
```

- [ ] **Step 3: Run it**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_i2c_bus_rule_parity.py -q`
Expected: all PASS.

- [ ] **Step 4: Recapture the e2e fixtures from live payloads**

Start the dev backend, then capture rather than hand-editing — a hand-written fixture only proves it agrees with the type it was written from:

```bash
cd /home/dannyb/sources/PiFire && curl -s localhost:8080/api/wizard/state > web-react/tests/e2e/fixtures/wizard-state.json
curl -s localhost:8080/api/settings > web-react/tests/e2e/fixtures/settings.json
```

For `probe-modules.json`, capture whatever endpoint the e2e suite stubs (read `web-react/tests/e2e/` for the route it intercepts). Confirm each captured file now carries `i2c_bus` objects and no `i2c_bus_kind`.

- [ ] **Step 5: Run every gate**

```bash
cd /home/dannyb/sources/PiFire && QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
.venv/bin/ruff format --check .
.venv/bin/ruff check .
cd web-react && bun run typecheck && bun run lint && bun run test && bunx playwright test
```

Expected: Python suite green (two known-flaky `test_scan_bluetooth_no_devices` failures under some random orderings are pre-existing — confirm by running them in isolation, do not "fix" them). `ruff format --check` flags `tests/web/test_spa.py`, which is pre-existing drift unrelated to this work. web-react typecheck clean, lint 0 errors, all rstest green, Playwright green.

- [ ] **Step 6: Grep for survivors**

```bash
cd /home/dannyb/sources/PiFire && grep -rn 'i2c_bus_kind\|i2c_bus_num\|i2c_bus_match\|"extended"' --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ . | grep -v settings_migration.py | grep -v test_settings_migration_i2c.py
```

Expected: no hits outside the migration and its test. Anything else is a site the migration will orphan.

- [ ] **Step 7: Commit**

```bash
cd /home/dannyb/sources/PiFire && .venv/bin/ruff format tests/web/test_i2c_bus_rule_parity.py
jj describe --stdin <<'EOF'
test(i2c): pin the TS bus rules against Python's and recapture fixtures

The wizard validates a bus on every keystroke, which means the format rules
exist twice. This asserts the two copies still agree, so a divergence surfaces
as a red test rather than a configuration the wizard accepts and the control
process refuses to open.
EOF
```

---

## Self-Review

**Spec coverage** — every section maps to a task: the hierarchy (1), `open_i2c_bus` and the deleted validators (2), schema/defaults/regen (3), migration (4), the nine call sites incl. `probes/base.py` and `tools/emc2301_tach_diag.py` (5), the 13 manifest sites and wizard plumbing and routes (6), the scan endpoint's `kernel` rename and third group (7), the React composite and live validation (8), rule parity and fixtures (9). The spec's "out of scope" items appear in no task, as intended.

**Type consistency** — `parse_i2c_bus` (not `from_config`) is the parser everywhere; the spec's prose named `I2CBus.from_config`, and the plan settles on the module-level function so the classes stay data-only. `resolve_bus_num()`, `to_config()`, `describe()`, `.kind` are used identically in Tasks 1, 2, 5. `I2cBusValue`, `i2cBusError`, `emptyBus`, `kernelBy` match between Tasks 8's type module and its component. The `bus=` keyword on `ADSDevice`/`KTTDevice` is declared in Task 5's Interfaces and used only there.

**Known soft spots for the implementer** — Task 5's `_make_sensor` and Task 6's `wizard_bus_kinds` module/profile lookup are described rather than quoted, because both depend on existing local structure that must be read first. Both steps say so explicitly.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-01-structured-i2c-bus-config.md`.
