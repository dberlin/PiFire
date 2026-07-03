# Common Wi-Fi Quality with `iw` Fallback

## Problem

`check_wifi_quality` is duplicated across grillplat platforms and only works when
`iwconfig` is installed. On systems that ship the newer `iw` tool instead of the
deprecated `iwconfig` (wireless-tools), Wi-Fi quality reporting silently fails.

Current state:
- `grillplat/raspberry_pi_all.py` — parses `iwconfig wlan0` (`Link Quality=x/y`).
- `grillplat/x86_numato.py` — parses `iwconfig` (no interface arg); near-duplicate.
- `grillplat/prototype.py` — returns canned simulator data.

## Goal

1. Add an `iw` fallback used when `iwconfig` is unavailable.
2. Extract a single shared implementation into `common/common.py`.
3. Wire every real grillplat platform to the shared function.

## Design

### Shared code in `common/common.py`

`common/common.py` already imports `subprocess` and `os`, and is re-exported via
`from common.common import *` in `common/__init__.py`, so new public names are
importable as `from common import get_wifi_quality`.

- `_detect_wireless_interface()` — scans `/sys/class/net/*/wireless`, returns the
  first wireless interface name, falling back to `'wlan0'` if none is found or
  `/sys/class/net` can't be read.

- `_wifi_quality_from_iwconfig(interface)` — runs `iwconfig <interface>`, parses the
  `Link Quality=x/y` field, returns `(value, max)` or `None` when the field is
  absent. Raises `FileNotFoundError` if `iwconfig` is not installed.

- `_wifi_quality_from_iw(interface)` — runs `iw dev <interface> link`, parses
  `signal: N dBm`, converts to a 0-100 quality via the NetworkManager formula
  `clamp(2 * (dBm + 100), 0, 100)`, returns `(percentage, 100)` or `None` when no
  signal line is present. Raises `FileNotFoundError` if `iw` is not installed.

- `get_wifi_quality(interface=None, logger=None)` — public entry point.
  - Auto-detects the interface when `interface is None`.
  - Tries iwconfig first, then iw. `FileNotFoundError` (tool missing) or a parse
    error (`CalledProcessError`, `ValueError`, `IndexError`) on one method advances
    to the next; a debug line is logged when `logger` is provided.
  - On success builds the existing return shape:
    ```python
    {
      'result': 'OK',
      'message': 'Successfully obtained wifi quality data.',
      'data': {
        'wifi_quality_value': value,
        'wifi_quality_max': maximum,
        'wifi_quality_percentage': round(value / maximum * 100, 2),
      },
    }
    ```
  - On total failure returns the unchanged
    `{'result': 'ERROR', 'message': 'Unable to obtain wifi quality data.', 'data': {}}`.

  `percentage` is computed uniformly from `value/max`, so iwconfig `70/70` → `100.0`
  and the iw path stays self-consistent (value already is the percentage, max 100).

### Platform changes

Both real platforms delegate:

```python
def check_wifi_quality(self, arglist):
    return get_wifi_quality(logger=self.logger)
```

- `raspberry_pi_all.py` — add `get_wifi_quality` to `from common import ...`; drops
  the hardcoded `wlan0` (now auto-detected).
- `x86_numato.py` — same; removes the method-local `import subprocess`.
- `prototype.py` — left unchanged; the simulator intentionally returns canned data.

No consumer or UI changes: `blueprints/admin/routes.py` and `admin/index.html`
already read `wifi_quality_value` / `wifi_quality_max` / `wifi_quality_percentage`,
and the return shape is unchanged.

## Testing

New `tests/test_wifi_quality.py` (mirrors `tests/test_x86_system.py` mock style),
with `subprocess.check_output` mocked:

- iwconfig present → parses `Link Quality=x/y`, correct value/max/percentage.
- iwconfig missing (`FileNotFoundError`) → falls back to iw; dBm→percentage
  conversion including clamping at both ends (e.g. `-40` → 100, `-95` → 10, `-30`
  clamps to 100, `-105` clamps to 0).
- both tools missing → `result == 'ERROR'`, empty `data`.
- interface auto-detection reads `/sys/class/net/*/wireless` and falls back to
  `wlan0`.

## Non-goals

- No changes to how/when consumers poll wifi quality.
- No change to the prototype simulator's canned values.
