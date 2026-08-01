# Structured I2C Bus Configuration — Design

**Date:** 2026-08-01
**Status:** Approved design, pending implementation plan

## Goal

Replace the `(i2c_bus_kind, i2c_bus_num)` string pair with a typed dataclass
hierarchy whose shape makes an invalid bus configuration unrepresentable, and
render that shape in the setup wizard so an operator can only fill in the fields
their chosen bus kind actually has.

Four bus kinds, each with its own fields:

| Kind | Fields |
| --- | --- |
| `basic` | none — the board's own I2C pins |
| `kernel` (was `extended`) | exactly one of: `/dev/i2c-N` number, adapter name, USB serial |
| `ft232h` | a pyftdi URL |
| `mcp2221` | a device serial |

## Background

### What exists today

Every I2C bus in PiFire is two sibling string keys, `i2c_bus_kind` and
`i2c_bus_num`, stored at three sites:

- `settings["platform"]["devices"]["distance"]` (`_DistanceDeviceConfig`)
- `settings["platform"]["fan_controller"]` (`_FanControllerConfig`)
- `settings["probe_settings"]["probe_map"]["probe_devices"][i]["config"]`

All three live inside the settings tree, so one migration pass reaches all of
them.

`common/i2c_bus.py::open_i2c_bus(bus_kind, bus_selector)` dispatches on the kind
string, and `resolve_i2c_bus(spec)` demultiplexes the single `i2c_bus_num`
string three further ways by sniffing its contents:

```python
if spec.lower().startswith("serial:"):  return find_i2c_bus_by_serial(...)
if spec.isdigit():                      return int(spec)
return find_i2c_bus(match=spec)          # adapter name, e.g. "CP2112"
```

### Why the current shape keeps producing bugs

One string carrying five different meanings, keyed off a second string, has no
way to express "this field does not apply". Three consequences, all of them
observed:

1. **Stale values survive a kind change.** Switching a device from Extended to
   FT232H left `"CP2112"` in `i2c_bus_num`, where it named a bus pyftdi can
   never open. `validate_bus_selector()` was added on 2026-08-01 to reject this
   at Finish.
2. **Fields render when they are meaningless.** The wizard shows the bus field
   for `basic`, where it is ignored entirely.
3. **Errors arrive late.** Nothing is checked until the Finish button, by which
   point the operator has left the step that caused it.

A tagged union fixes all three at the source: an `FT232HBus` has nowhere to keep
`"CP2112"`, a `BasicBus` has no fields to render, and a kind change constructs a
different object rather than editing one.

## Architecture

### New module: `common/i2c_bus_config.py`

Frozen, slotted dataclasses. The hierarchy — not a validator — is what enforces
"exactly one of".

```
I2CBus (ABC)                              kind: ClassVar[str]
├── BasicBus                              {"kind": "basic"}
├── KernelBus (ABC)                       kind = "kernel"
│     .resolve_bus_num() -> int
│   ├── KernelBusNumber   bus_num: int    {"kind":"kernel","bus_num":3}
│   ├── KernelAdapterName adapter: str    {"kind":"kernel","adapter":"CP2112"}
│   └── KernelSerialMatch serial: str     {"kind":"kernel","serial":"0012AB34"}
├── FT232HBus             url: str = ""   {"kind":"ft232h","url":"ftdi://..."}
└── MCP2221Bus            serial: str=""  {"kind":"mcp2221","serial":"01234567"}
```

Interface on `I2CBus`:

- `to_config() -> dict` — the tagged form above, carrying `kind` plus exactly
  the live field.
- `describe() -> str` — one human phrase for log lines and error messages.

and a module-level `parse_i2c_bus(data) -> I2CBus`, dispatching on `kind` and
then on which field is present. It raises `I2CBusConfigError` on an unknown
kind, on a kernel object with zero or more than one selector field, and on any
unrecognized key. A free function rather than a classmethod, so the dataclasses
stay data plus their own behavior and the dispatch table lives in one place.

`resolve_i2c_bus()`'s three-way string sniffing becomes three
`resolve_bus_num()` implementations on the `KernelBus` subtree. `find_i2c_bus`,
`find_i2c_bus_by_serial` and `_enumerate_i2c_adapters` stay as the discovery
primitives those methods call.

`resolve_i2c_bus` itself cannot simply be deleted: `probes/base.py:25` re-exports
it alongside `find_i2c_bus` as deliberate public API
(`# noqa: F401  # public re-export`), `probes/ads1115.py` imports it from there
rather than from `common.i2c_bus`, and
`tests/unit/i2c/test_i2c_bus.py::test_probes_base_reexports_bus_helpers` pins
the identity of both. It goes away together with its re-export, that test's
`resolve_i2c_bus` half, and `probes/ads1115.py`'s use of it — in one change, so
nothing is left importing a name that no longer exists.

Frozen dataclasses are hashable, so a bus object is its own `_bus_cache` key,
replacing the `(kind, selector)` tuple and `_canonical_selector()`. FT232H's
"blank and `1` both mean the first device" normalization moves into
`FT232HBus.__post_init__` (via `object.__setattr__`), so the two spellings
collapse to one cache entry before the object ever exists.

### What this deletes

`validate_bus_selector()`, `validate_bus_selectors()`,
`configured_bus_selectors()`, `wizard_bus_selectors()` and
`_EXTENDED_ADAPTER_NAMES` — added 2026-08-01 to catch a kernel selector stranded
on a USB-HID bus — all go. That state is now unconstructible, and the migration
drops such values on upgrade rather than raising at Finish. Their call sites in
`blueprints/api_wizard/routes.py` (`/finish`, `/probes/validate-bus-kinds`) and
`blueprints/api/routes.py` lose those calls.

`validate_bus_kinds()` **stays**. It enforces that `basic` cannot share a
process with a USB-HID kind, which is a constraint across separate device
configurations and has nothing to do with a single bus's shape.

### Pydantic schema

`kind` alone cannot discriminate — three kernel variants share it — so the
schema is a left-to-right `Union` of per-variant models with `extra="forbid"`.
`{"kind":"kernel","adapter":"X"}` then matches exactly one member, because every
other member forbids the `adapter` key.

```python
I2CBusConfig = Annotated[
    Union[_BasicBus, _KernelBusNumber, _KernelAdapterName,
          _KernelSerialMatch, _FT232HBus, _MCP2221Bus],
    Field(union_mode="left_to_right"),
]
```

`_DistanceDeviceConfig` and `_FanControllerConfig` each drop `i2c_bus_kind` and
`i2c_bus_num` and gain `i2c_bus: I2CBusConfig = _BasicBus()`. `common/defaults.py`
changes in lockstep — it is the defaults authority, and
`tests/unit/common/test_settings_schema.py` fails on divergence.

Probe device configs are not modelled by the schema (they are manifest-driven
dicts); `I2CBus.from_config` is their validation.

### Migration

A build-gated block in `common/settings_migration.py::upgrade_settings`, pure
string parsing with no hardware access, walking all three sites:

| legacy `kind` | legacy `i2c_bus_num` | result |
| --- | --- | --- |
| `basic` | anything | `{"kind":"basic"}` |
| `extended` | `"3"` | `{"kind":"kernel","bus_num":3}` |
| `extended` | `"serial:X"` | `{"kind":"kernel","serial":"X"}` |
| `extended` | `"CP2112"` | `{"kind":"kernel","adapter":"CP2112"}` |
| `extended` | blank | `{"kind":"basic"}` + `write_log` |
| `ft232h` | `"ftdi://…"` / `"1"` / blank | `{"kind":"ft232h","url":…}` |
| `ft232h` | a kernel selector | `{"kind":"ft232h"}`, value dropped + logged |
| `mcp2221` | a plain serial / blank | `{"kind":"mcp2221","serial":…}` |
| `mcp2221` | a kernel selector | `{"kind":"mcp2221"}`, value dropped + logged |

`extended` + blank is already broken today rather than merely unusual:
`find_i2c_bus(match="")` substring-matches every adapter and raises "multiple
adapters match". Falling back to `basic` and logging is the honest repair.

`grillplat/x86_numato.py` carries a still older key, `i2c_bus_match` (pre-dating
the basic/extended split), read at its call site as a fallback. That fallback
moves into the migration — `i2c_bus_match: "CP2112"` becomes
`{"kind":"kernel","adapter":"CP2112"}` — and the call site stops handling it.

### Call-site migration

`open_i2c_bus(bus: I2CBus)` — one argument. The inventory below is from LSP
reference lookups, not a text search; an earlier grep pass missed the last two
rows because it was scoped to the obvious directories.

| File | Change |
| --- | --- |
| `probes/ads1115_adafruit.py:53` | `parse_i2c_bus(device_info["config"]["i2c_bus"])` |
| `probes/ads1015_adafruit.py:52` | same |
| `probes/mcp9600_adafruit.py:55` | same |
| `probes/ads1115.py:59` | the direct-smbus2 branch becomes `isinstance(bus, KernelBus)` → `smbus2.SMBus(bus.resolve_bus_num())` |
| `distance/_tof_base.py:43` | reads `distance_pins["i2c_bus"]`; the `"CP2112"` default disappears with the string |
| `grillplat/x86_numato.py:98` | `parse_i2c_bus(fan_cfg["i2c_bus"])`; drops its kind-guessing and `i2c_bus_match` fallback |
| `grillplat/ft232h_relay.py:103` | `open_i2c_bus(FT232HBus(url=self.url))` |
| `probes/base.py:25` | drops the `resolve_i2c_bus` half of its public re-export |
| `tools/emc2301_tach_diag.py:68` | a standalone diagnostic that reads the settings tree and runs its own `if bus_kind == "extended": ExtendedI2C(resolve_i2c_bus(bus_num))`. It reads the shape being changed, so it breaks silently if skipped — it is not covered by the test suite. |

The `ADSDevice`/`MCP9600Device` constructors take `bus: I2CBus` in place of
`i2c_bus_kind` + `i2c_bus_num`; `i2c_bus_addr` is a device address, not part of
the bus, and stays a separate parameter.

`platform.ft232h.url` stays where it is. It names the FT232H board for GPIO as
well as I2C, so `ft232h_relay` builds an `FT232HBus` from it at the call site
rather than the setting moving under a bus object.

## Wizard

### Manifest

Thirteen sites collapse from a pair of fields to one composite: eight
`grillplatform` `settings_dependencies` pairs and five probe
`device_specific.config` pairs (`ads1115`, `ads1115_adafruit`, `ads1015_adafruit`,
`mcp9600_adafruit`, `prototype`).

```json
{
  "friendly_name": "I2C Bus",
  "description": "How this device's I2C bus is reached.",
  "type": "i2c_bus",
  "settings": ["platform", "devices", "distance", "i2c_bus"]
}
```

The `settings` path now names an **object**. The value coercion chain already
handles that, verified rather than assumed:

- `wizard.py::_convert_value` opens with `if not isinstance(value, str): return
  value`, so a dict passes through untouched.
- `common/settings_schema.py::coerce_setting_value` delegates to that fallback
  for any non-string, so it does too.

Neither needs changing. What does:

- `blueprints/wizard/wizard.py::get_settings_dependencies_values` — must return
  the dict as read. Its two callers are `/api/wizard/module-values` and
  `_build_state` (which backs `/api/wizard/state`), so the object flows to the
  client through both.
- `_constrain_to_options` — returns non-string values unchanged. An `i2c_bus`
  dep carries no `options`, so this is a guard against a future one, not a
  behavior change.

The `i2c_bus` dep must carry a dict `default` (`{"kind": "basic"}`). That is
what puts it in front of
`tests/unit/wizard/test_manifest_schema_conformance.py::test_every_manifest_option_can_be_written`,
whose sweep collects each dep's `options` keys plus its `default` and asserts
the result validates against the schema — the test that exists because a
manifest option the schema had no type for once bricked a detached install run.

### React

A new `<I2cBusField>` under `web-react/src/components/wizard/fields/`, replacing
the `i2c_bus_num` special cases in `ModuleCard.tsx` (which pairs fields by
`key.replace("_num","_kind")`) and `DeviceConfigField.tsx` (which pairs via
`allValues.i2c_bus_kind`). `I2cBusPicker.tsx` is deleted.

```
I2C Bus  [ Kernel (/dev/i2c-N)  v ]

  ( ) Bus number     [ 3       ]
  (o) Adapter name   [ CP2112  ]   [Discover]
  ( ) USB serial     [         ]
```

- `basic` renders the select alone.
- `kernel` renders a three-way radio and the one matching input.
- `ft232h` renders a URL input; `mcp2221` a serial input.
- Changing the kind, or the kernel radio, **replaces** the value object rather
  than editing it, so no stale field can survive the change.
- Discover reuses `POST /api/wizard/scan` with the current kind. That endpoint
  matches on `kind == "extended"` and must accept `"kernel"`; it returns two
  groups today (By Bus Number, By Serial) and gains a third, By Adapter Name,
  now that adapter matching is a field of its own. Picking a row writes into
  whichever field the radio has selected, and switching the radio to match the
  group the operator picked from is the least surprising behavior.

Field state is the tagged object itself, so what the component holds and what
gets persisted are the same shape.

### Live validation

The shape removes the cross-field rules, leaving only per-field format checks,
which run on every keystroke and gate Back/Next and Finish:

| Kind | Rule |
| --- | --- |
| kernel / bus number | required, digits only |
| kernel / adapter name | required |
| kernel / USB serial | required |
| ft232h | blank allowed (first device); otherwise must start `ftdi://` |
| mcp2221 | blank allowed (first device) |

These are duplicated in TypeScript rather than round-tripped to the server —
they are small, and an HTTP round trip per keystroke is the wrong mechanism for
"this field is empty". Python keeps the authoritative copy for configurations
that arrive by other paths (settings import, a hand-edited backup, the live
`/api/probe_map` save), and a shape-pinning test asserts the two rule sets agree
so they cannot drift apart silently.

## Testing

**New**

- `tests/unit/i2c/test_i2c_bus_config.py` — `to_config`/`from_config` round
  trip for all six variants; `from_config` rejects an unknown kind, a kernel
  object with zero or two selector fields, and unrecognized keys; frozen
  instances are usable as dict keys; FT232H `""` and `"1"` produce equal objects.
- `tests/unit/common/test_settings_migration_i2c.py` — every row of the
  migration table, at all three settings sites, plus the `i2c_bus_match` case
  and idempotency (running the migration over already-migrated settings is a
  no-op).
- `web-react/tests/unit/components/wizard/fields/I2cBusField.test.tsx` — each
  kind renders exactly its own fields; switching kind clears the previous
  value; the kernel radio switches which input is live; each validation rule
  reports inline.
- A shape-pinning test tying the TS validation rules to the Python ones.

**Updated**

- `tests/unit/i2c/test_i2c_bus.py`, `test_i2c_bus_selectors.py`,
  `test_i2c_bus_wizard_validation.py`, `test_i2c_bridge_match_manifest.py`,
  `test_i2c_bus_num_defaults.py` — to the new API; the tests covering deleted
  validators go with them, as does the `resolve_i2c_bus` half of
  `test_probes_base_reexports_bus_helpers`.
- `tests/web/test_wizard_helpers.py::test_settings_dependency_values_passes_through_a_dep_with_no_options`
  — its fixture is a raw `"serial:0012AB34"` selector.
- `tests/unit/wizard/test_manifest_schema_conformance.py` — no code change
  expected, but it is the sweep that must stay green; its
  `("platform","ft232h","url") in paths` assertion also pins the decision to
  leave that setting alone.
- `web-react/tests/unit/components/wizard/fields/I2cBusPicker.test.tsx` —
  8 tests, deleted with the component they cover.
- `tests/unit/platform/test_x86_bus_discovery.py`, `tests/unit/distance/`,
  `tests/unit/ft232h/`, `tests/unit/probes/` — `open_i2c_bus` call assertions
  (`open_bus.assert_called_once_with("extended", "CP2112")` and friends become
  single-argument assertions against a bus object).
- `tests/web/test_api_wizard.py`, `test_wizard_bus_kinds.py`,
  `test_wizard_bus_selectors.py`, `test_wizard_helpers.py`,
  `test_wizard_install_info_defaults.py`, `test_api_probe_map.py`.
- `tests/unit/common/test_settings_schema.py` — defaults parity.
- e2e fixtures `probe-modules.json`, `wizard-state.json`, `settings.json`,
  recaptured from live payloads rather than hand-edited.

**Regenerated**

`web-react/schema/settings.schema.json` via
`uv run python -m common.settings_schema`, then `bun run gen:types`. The drift
check test covers this.

## Backward compatibility

Settings written by any prior version migrate on first read; nothing else in the
tree changes. A settings backup exported before this change imports cleanly,
because import runs through the same migration pipeline.

Nothing outside PiFire consumes these keys — no API surface exposes
`i2c_bus_kind` directly, and the wizard is the only writer.

## Out of scope

- Moving `platform.ft232h.url` under a bus object.
- Probing hardware during migration to convert an adapter name into a serial.
- Any change to the `basic`-versus-USB-HID single-process rule
  (`validate_bus_kinds`).
- The display modules' I2C usage, which goes through luma rather than
  `open_i2c_bus`.
