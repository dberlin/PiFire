# DFRobot SEN0628 USB Distance Sensor — Design

Date: 2026-07-15

## Summary

Add support for the [DFRobot SEN0628](https://www.dfrobot.com/product-2999.html) ("Gravity: 8x8 Matrix ToF 3D Distance Sensor") as a new pluggable hopper-level distance module, connected via its onboard USB-C port. Also add a generic "Discover" button for USB-serial device paths in the setup wizard, reusable by any future USB-serial module.

## Background

The SEN0628 is a VL53L7CX-based Time-of-Flight depth sensor with an onboard RP2040 doing protocol handling. Per DFRobot's wiki, it exposes three live data interfaces — I2C, UART, and USB — all documented separately from the "USB interface update" (firmware) row. A dedicated setup doc confirms the USB-C port enumerates as a standard virtual COM port at a fixed 115200 baud and streams the same real-time distance protocol as the UART pins (verified via a serial-monitor walkthrough in DFRobot's docs). So "over USB" means: plug the sensor's own USB-C cable into the Pi, it shows up as a serial device (e.g. `/dev/ttyACM0`), and PiFire talks the documented UART/USB command protocol over that port — no separate USB-to-TTL adapter needed.

Range: 20mm–3500mm, 60°/90° FOV, 15–60Hz. This is a good option for taller hoppers, similar in spirit to the existing VL53L1X support.

### Protocol

DFRobot publishes a reference Python driver (`DFRobot_matrixLidar.py`, from `github.com/DFRobot/DFRobot_MatrixLidar`, `python/raspberry/` folder) for both I2C and UART/USB transports. Relevant framing (verified by reading the actual source, not just the docs):

- Sync byte `0x55` precedes every outbound packet.
- Outbound packet: `[len_hi, len_lo, cmd, ...args]`.
- Inbound packet: `[status, cmd, len_lo, len_hi, ...data]`, where `status` is `0x53` (success) or `0x63` (failure).
- Commands used here: `CMD_SETMODE = 1` (configure ranging matrix, e.g. 8x8), `CMD_FIXED_POINT = 3` (read one `(x, y)` point, returned as a little-endian 16-bit value in the response data).
- The vendor's own reference script has two Python-2-only bugs that would crash verbatim under Python 3 (`ord()` on an already-int byte from iterating `bytes`, and writing a raw `list` instead of `bytes`/`bytearray` to `pyserial`). Our port fixes both; this is not "fixing a vendor bug in vendor code" since we're writing our own driver from the documented protocol, not vendored their file unmodified.
- No units are documented explicitly, but given the 20–3500mm range spec and the existing I2C ToF drivers' convention (VL53L0X/L1X/L4CD all return raw millimeters), point values are treated as millimeters.

## Read strategy

The 8x8 matrix gives 64 points; hopper level only needs one number. Per discussion, the driver averages a 2x2 center block — points `(3,3), (3,4), (4,3), (4,4)` in the 0-indexed 8x8 grid — using four separate `CMD_FIXED_POINT` queries per poll, rather than decoding the full `CMD_ALLData` 64-point blob. The single-point response format is unambiguous in the vendor protocol; the full-matrix byte ordering (row-major vs. column-major, starting corner) is not documented anywhere verifiable, so decoding it would risk silently wrong values. Four extra round-trips per poll (well under the existing sensing loop's ~60s cadence) is an acceptable cost for that certainty.

## Architecture

### New driver: `distance/sen0628.py`

Implements the existing duck-typed `HopperLevel` interface (`__init__(dev_pins, empty, full, debug)`, `set_level`, `update_distances`, `get_distances`, `get_level(override)`) — the same contract as `distance/hcsr04.py` and `distance/none.py`.

Because the sensor's protocol can time out for multiple seconds on a bad read, the driver polls in a background thread rather than blocking `get_level()` directly — this is the same rationale already documented in `distance/_tof_base.py` for the I2C ToF sensors (the control loop calls `get_level(override=True)` at most every 60s from `controller/runtime/modes/base.py`, and a multi-second stall there would hurt temperature-control responsiveness).

### New base: `distance/_serial_tof_base.py`

A `SerialToFHopperLevel` class carrying the same threaded-polling / empty-full-percentage-math scaffold as `_tof_base.py`'s `ToFHopperLevel`, but opening a `pyserial` port (device path + fixed baud) instead of an I2C bus. This is a deliberate, small duplication of `_tof_base.py`'s ~50-line `_sensing_loop`, not a refactor of the existing I2C base — `_tof_base.py` and its three consumers (`vl53l0x.py`, `vl53l1x.py`, `vl53l4cd.py`) are tested and shipped; forcing them through a transport-agnostic base to serve one new serial consumer isn't justified, and matches how `hcsr04.py`/`none.py`/`prototype.py` already each carry their own small amount of boilerplate rather than being forced into a shared base.

Constructor reads `dev_pins["distance"]["device"]` (serial port path, e.g. `/dev/ttyACM0`) from the same `settings["platform"]["devices"]["distance"]` dict the I2C sensors already read `i2c_bus_kind`/`address` from. Baud is fixed at 115200 (per datasheet, "fixed, non-modifiable") — not user-configurable.

Subclasses implement `_read_distance_mm()`; `sen0628.py` implements it via the embedded protocol client described above, plus `begin()`/`set_Ranging_Mode(8)` once in `_open_sensor()`.

Module docstring flags the driver as untested against real hardware, matching the existing disclaimer style in `hcsr04.py`.

### Wizard registration: `wizard/wizard_manifest.json`

New entry under `modules.distance.sen0628`:

```json
"sen0628": {
  "friendly_name": "DFRobot SEN0628 8x8 Matrix ToF Distance Sensor (USB)",
  "filename": "sen0628",
  "description": "An 8x8-point Time-of-Flight depth sensor (20mm-3.5m range) connected via its onboard USB-C port. A good option for taller hoppers.",
  "default": false,
  "image": "sen0628.png",
  "py_dependencies": [],
  "apt_dependencies": [],
  "command_list": [],
  "settings_dependencies": {
    "sen0628_device": {
      "friendly_name": "Serial Device (USB)",
      "description": "Path to the SEN0628's USB-C serial device (e.g. /dev/ttyACM0). Use Discover to scan for connected USB serial devices.",
      "type": "usb_serial_device",
      "vid": null,
      "pid": null,
      "default": "/dev/ttyACM0",
      "options": {
        "/dev/ttyACM0": "/dev/ttyACM0",
        "/dev/ttyACM1": "/dev/ttyACM1",
        "/dev/ttyUSB0": "/dev/ttyUSB0",
        "/dev/ttyUSB1": "/dev/ttyUSB1"
      },
      "settings": ["platform", "devices", "distance", "device"]
    }
  }
}
```

`py_dependencies: []` because `pyserial` is already a core project dependency. `options` is retained as a static fallback list (same convention as `numato_device`) in case the field type falls back to a plain `<select>` anywhere; the `type: usb_serial_device` branch takes priority in the templates that know about it.

`vid`/`pid` are `null` for now — the real hardware's USB VID/PID isn't available yet (DFRobot doesn't publish it, it's not in the public USB ID registry, and the GitHub firmware repo doesn't expose it). Per user decision, the Discover button falls back to silently listing every connected serial device when `vid`/`pid` are unset, with no separate warning banner. Once the real VID/PID is known it's a two-value JSON edit — no code changes.

An image asset (`sen0628.png`) needs to be added to the wizard's image directory; a placeholder can stand in until a proper product photo is provided.

### Generic USB-serial "Discover" feature

Modeled directly on the existing I2C-bridge Discover button (`type: i2c_bus_num`), and designed to be reusable by any future USB-serial module, not sensor-specific:

- **`common/usb_serial.py`** (new): `discover_usb_serial_devices(vid=None, pid=None)` using `serial.tools.list_ports.comports()`. Filters by `vid`/`pid` only when provided (both are matched as ints when given); never raises (returns `[]` on enumeration failure), matching the contract of `discover_extended_i2c_buses()` in `common/i2c_bus.py`.
- **`blueprints/wizard/routes.py`**: new `action == "usb_serial_scan"` branch (modeled on the existing `i2c_bus_scan` branch), reading `itemID`/`vid`/`pid` from the POST body, parsing `vid`/`pid` as hex strings when present, building `groups`/`error` in the same shape the existing `render_i2c_scan_table` macro already expects (`{"title": ..., "items": [{"value": ..., "label": ...}]}`), and rendering with that *same* macro — no new results-table markup or JS is needed, since `render_i2c_scan_table` and `selectI2CBus()` are already generic over "what kind of thing got discovered."
- **`blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html`**: new macro `render_input_usb_serial_device(dom_id, css_class, default, vid, pid)` — an input + Discover button + modal, modeled on `render_input_i2c_bus_num` (lines 542-585), but reusing the `i2c_{{dom_id}}_Modal` / `i2c_{{dom_id}}_Select` DOM id scheme so the existing `selectI2CBus()` JS works unmodified against it.
- **`blueprints/probeconfig/static/probeconfig/js/probeconfig.js`**: new `scanUsbSerial(itemID, vid, pid)` function (modeled on `scanI2CBus`), POSTing to `/wizard/usb_serial_scan` with `itemID`/`vid`/`pid` and loading the response into the same modal content div `selectI2CBus()` already knows how to close.
- **`blueprints/wizard/templates/wizard/_macro_wizard_card.html`**: one more `{% elif %}` branch dispatching on `settings_dependencies[setting].type == 'usb_serial_device'`, calling the new macro with `vid`/`pid` pulled from the manifest field.

## Testing

- `tests/unit/distance/test_sen0628.py`, following the `test_vl53l0x.py` pattern: mock `serial.Serial`, verify the device path/baud used to open it, verify outbound packet framing (sync byte, length, command, args) for `set_Ranging_Mode`/`get_fixed_point_data`, verify response parsing (including the failure-status and read-timeout paths), verify the 2x2 center-block average.
- `tests/unit/distance/test_serial_tof_base.py`: threaded polling / re-init-on-stall / percentage math, mirroring `test_tof_base.py`'s coverage but for the serial base.
- A manifest assertion alongside the existing ones in `tests/unit/distance/test_distance_manifest.py` (entry present, `py_dependencies == []`, `settings_dependencies.sen0628_device` shape).
- `tests/unit/...` coverage for `common/usb_serial.py`'s `discover_usb_serial_devices()` (mocking `serial.tools.list_ports.comports()`), and for the new `usb_serial_scan` route branch (mocking the discovery call), following whatever pattern the existing `i2c_bus_scan` tests use (if any exist — worth checking during implementation).

## Out of scope

- Any change to the existing I2C ToF sensors (`_tof_base.py`, `vl53l0x.py`, `vl53l1x.py`, `vl53l4cd.py`) or their tests.
- Decoding the full 64-point `CMD_ALLData` response (unverifiable byte ordering — see "Read strategy" above).
- Hardware-in-the-loop verification — this integration is written from DFRobot's documented protocol and reference driver source, adapted for Python 3, but is not verified against physical hardware (flagged in the module docstring, same convention as `hcsr04.py`).
- Filling in the real USB VID/PID — left as `null` in the manifest until the user can check the physical device.
