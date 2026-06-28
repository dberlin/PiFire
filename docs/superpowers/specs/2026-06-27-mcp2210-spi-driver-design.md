# MCP2210 CircuitPython-compatible SPI driver — Design

**Date:** 2026-06-27
**Status:** Approved (design); implementation pending
**Author:** PiFire

## Goal

Provide a host-side (Adafruit Blinka) driver for the Microchip **MCP2210**
USB-to-SPI bridge that is **duck-type compatible** with the CircuitPython
`busio.SPI` and `digitalio` APIs, so that stock Adafruit/CircuitPython sensor
libraries run unmodified against a USB-attached MCP2210.

Concretely, after wiring an MCP2210 over USB, code like this should work:

```python
from mcp2210 import MCP2210
from adafruit_bus_device.spi_device import SPIDevice
import adafruit_max31865

mcp = MCP2210()                      # opens first MCP2210 by VID/PID
spi = mcp.spi                        # busio.SPI-compatible bus
cs  = mcp.get_pin(0)                 # digitalio-compatible CS on GP0
sensor = adafruit_max31865.MAX31865(spi, cs)
print(sensor.temperature)
```

This serves PiFire's SPI temperature probes (e.g. MAX31865 RTD, MAX31855
thermocouple, MCP3xxx ADC) on host platforms that lack native SPI, mirroring the
existing I2C-over-MCP2221 path already present via Blinka.

## Background / constraints

- **Device:** MCP2210, USB-HID class device. VID `0x04D8`, PID `0x00DE`.
- **Transport:** 64-byte HID input and output reports. Command byte is report
  byte 0. Driver uses the `hid` (hidapi) Python package — same dependency the
  in-tree MCP2221 Blinka driver uses.
- **SPI engine:** 8-bit words only; SPI modes 0–3; bit-rate set in Hz. Max
  **60 data bytes per SPI-transfer command** — larger transfers must be chunked
  across multiple `0x42` commands while the SPI engine keeps the transaction
  open.
- **GPIO:** 9 general-purpose pins (GP0–GP8). Each can be GPIO, a dedicated
  function, or a chip-select. No internal pull-ups/pull-downs.
- **CS semantics:** CircuitPython `busio.SPI` does **not** own chip-select; the
  caller (e.g. `SPIDevice`) toggles a separate `digitalio.DigitalInOut`. The
  MCP2210 can drive CS automatically as part of a transfer, but to honor busio
  semantics the default configuration sets *idle CS value == active CS value*
  so the chip does not auto-toggle CS, and a companion DigitalInOut on a GPIO
  owns CS. Hardware auto-CS is offered as an opt-in (see Future/opt-in).

## Architecture

A standalone importable package `mcp2210/` (not slotted into the Blinka
microcontroller tree, since the MCP2210 is not a Blinka-detected board). Four
modules, layered so every byte of USB traffic flows through one transport
method.

```
mcp2210/
  __init__.py     # public exports: MCP2210, SPI, Pin, exceptions
  mcp2210.py      # MCP2210 device class + transport + command constants
  spi.py          # SPI: busio.SPI-compatible bus
  pin.py          # Pin + GPIO (digitalio-compatible) support
```

(Full EEPROM/NVRAM/USB-config/interrupt-counter helpers live as methods on the
`MCP2210` class in `mcp2210.py`, or a thin `extras` mixin if the file grows
large.)

### 1. `mcp2210.py` — device + transport

`MCP2210` class responsibilities:

- Open the HID handle: `hid.device()`, `.open(VID, PID)` or open-by-serial when
  multiple devices are attached. Register `atexit` close (matches MCP2221).
- **One private transport method** `_xfer(command_bytes) -> bytes` that:
  - left-pads/truncates the payload to a 64-byte report,
  - writes the output report, reads the 64-byte input report,
  - validates `response[0] == command` echo and the completion-status byte,
    raising a mapped exception on error (busy / bus-not-available / blocked /
    no-such-command).
- Command-wrapper methods that build payloads and parse responses for each chip
  feature; SPI and Pin layers call these, never `hid` directly.
- Holds the command-code and status constants (verified against datasheet).
- Lazily exposes `self.spi` (an `SPI` instance) and `get_pin(n)` factory.

Command codes (to be confirmed against datasheet DS20005286 during impl):

| Code | Command |
|------|---------|
| 0x10 | Get chip status |
| 0x11 | SPI bus cancel |
| 0x12 | Get interrupt event counter |
| 0x20 | Get current (VM) chip settings |
| 0x21 | Set current (VM) chip settings |
| 0x30 | Set current GPIO pin values |
| 0x31 | Get current GPIO pin values |
| 0x32 | Set current GPIO pin direction |
| 0x33 | Get current GPIO pin direction |
| 0x40 | Set current SPI transfer settings |
| 0x41 | Get current SPI transfer settings |
| 0x42 | SPI transfer |
| 0x50 | Read EEPROM |
| 0x51 | Write EEPROM |
| 0x60 | Set NVRAM (power-up) settings |
| 0x61 | Get NVRAM (power-up) settings |
| 0x70 | Send access password |
| 0x80 | Request SPI bus release |

### 2. `spi.py` — `SPI` (busio.SPI-compatible)

Implements exactly the surface `adafruit_bus_device.SPIDevice` and sensor
libraries use:

- `try_lock() -> bool`, `unlock()` — single-owner boolean flag.
- `configure(baudrate=100000, polarity=0, phase=0, bits=8)` — maps
  `(polarity, phase)` to MCP2210 SPI mode 0–3, sets the bit-rate (Hz) and mode
  via the SPI-settings command (`0x40`). `bits != 8` raises `ValueError`.
  Must be called while locked, per CircuitPython contract.
- `frequency` property (effective Hz).
- `write(buffer, *, start=0, end=None)`
- `readinto(buffer, *, start=0, end=None, write_value=0)`
- `write_readinto(out_buffer, in_buffer, *, out_start=0, out_end=None, in_start=0, in_end=None)`
- `deinit()`.

Transfer engine:

- Each `0x42` command moves ≤60 bytes full-duplex. For longer buffers, the
  driver loops issuing `0x42` chunks and concatenating returned RX bytes,
  polling the SPI-engine status field between chunks until the transaction
  completes. The MCP2210 "bytes-per-transaction" setting is set to the total
  length so the engine treats the chunks as one CS-held transaction.
- Default settings keep CS non-auto-toggling (idle==active); the companion
  DigitalInOut drives CS.
- `write`/`readinto` are implemented in terms of the full-duplex transfer
  (TX padding with `write_value` for reads; RX discarded for writes).

### 3. `pin.py` — GPIO (digitalio-compatible)

- `Pin` class implementing the Blinka Pin protocol so stock
  `digitalio.DigitalInOut(pin)` works: `.init(mode=IN|OUT)`, `.value(val=None)`,
  module-level `IN`/`OUT` constants. Pull requests raise `NotImplementedError`
  (chip has no internal pulls), consistent with other Blinka USB bridges.
- Pin objects know their GPIO index (0–8) and a back-reference to the `MCP2210`
  for issuing direction (`0x32/0x33`) and value (`0x30/0x31`) commands, and for
  flipping the pin's designation to GPIO in the chip settings (`0x21`) on init.
- `MCP2210.get_pin(n)` returns/caches the `Pin` for index `n`; convenience
  attributes `G0..G8` may alias these.

### 4. EEPROM / NVRAM / misc (methods on `MCP2210`)

- `read_eeprom(addr)` / `write_eeprom(addr, value)` (`0x50/0x51`).
- `get_nvram_settings()` / `set_nvram_settings(...)` for power-up chip + SPI
  settings and USB descriptor/key config (`0x60/0x61`).
- `interrupt_count(reset=False)` (`0x12`).
- `chip_status()` (`0x10`), `cancel_spi()` (`0x11`), `release_bus()` (`0x80`).
- `send_password(pw)` (`0x70`).

## Data flow

```
sensor lib ─► SPIDevice ─► SPI.configure/write/readinto ─┐
                                                          ├─► MCP2210._xfer ─► hid report ─► USB
digitalio.DigitalInOut(CS) ─► Pin.value/init ────────────┘
```

## Error handling

- Transport: non-OK completion byte → specific exceptions
  (`MCP2210BusBusyError`, `MCP2210BusUnavailableError`,
  `MCP2210BlockedError`, etc.) subclassing a base `MCP2210Error(RuntimeError)`.
- `configure` outside a lock, or `bits != 8` → `ValueError`/`RuntimeError`
  matching CircuitPython behavior.
- SPI transfer that never completes within a retry budget → timeout error.
- Pull-up/down requests on a pin → `NotImplementedError`.

## Testing strategy

- **TDD with a fake HID transport.** A `FakeHID` object returns canned 64-byte
  responses and records the output reports written. Unit tests assert exact
  command framing (codes, offsets, little-endian fields, chunk boundaries) and
  response parsing — no hardware required. This is the primary safety net given
  the protocol is byte-exact.
- Tests cover: transport padding/echo/status checks; `configure` mode/bit-rate
  encoding; chunking of >60-byte transfers and full-duplex `write_readinto`;
  `write`/`readinto` padding semantics; GPIO direction/value round-trips;
  EEPROM/NVRAM encode/decode; error mapping.
- **Hardware smoke test (documented, manual):** a short script that opens a real
  device, reads chip status, and clocks a known SPI sensor; run once a device is
  attached. Documented, not part of CI.

## Implementation sequencing

Cohesive enough for one spec, but built in this order so the SPI path is usable
early:

1. Transport + `MCP2210` open/close + `_xfer` + error mapping.
2. `SPI` (configure, transfer, chunking) — unblocks SPI probes.
3. `Pin`/GPIO digitalio compatibility — unblocks CS and general I/O.
4. EEPROM / NVRAM / USB-config / interrupt counter / status / password.

## Future / opt-in (out of scope for first cut unless trivial)

- Hardware auto-CS mode (let the MCP2210 drive a CS pin per transaction) as an
  explicit opt-in, for users who want the chip's native CS timing.
- Multi-device selection helper (enumerate by serial).
- Integration into PiFire's probe/platform configuration + wizard.

## Open items to confirm during implementation

- Exact response byte offsets and SPI-engine status codes for `0x42`
  (datasheet DS20005286), including the "transfer started / in progress /
  finished" sub-states used to drive the chunk-polling loop.
- Whether changing a pin to GPIO requires a full chip-settings (`0x21`) write or
  only direction (`0x32`); affects `Pin.init` cost.
