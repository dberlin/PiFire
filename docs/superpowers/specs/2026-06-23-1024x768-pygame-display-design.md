# 1024×768 pygame Display Support

## Overview

Add a 1024×768 pygame-based display to PiFire, mirroring the existing 800×480
DSI/pygame display (`display/dsi_800x480t.py`). The display module is
resolution-agnostic — all dimensions and layout come from a JSON layout file —
so the new resolution is delivered as a new layout file plus a thin module that
reuses the existing class, registered as a selectable display in the wizard.

The layout is rescaled from the 800×480 layout using a uniform 1.28× scale with
centering ("strategy A"), because the aspect ratio changes from 5:3 (800×480)
to 4:3 (1024×768).

## Goals

- A new selectable display option that renders the PiFire dashboard/menus at
  1024×768 via pygame, identical in behavior to the 800×480 display.
- A layout that preserves the 800×480 design intent — round gauges,
  proportional fonts and spacing — at the larger size.

## Non-Goals

- No changes to the shared display framework (`base_flex.py`, `flexobject.py`)
  or to the 800×480 display.
- No new gauge/menu types or behavioral changes.
- No dedicated 1024×768 splash artwork (the existing splash is reused; see
  "Splash").

## Architecture

PiFire selects a display by module name: `settings['modules']['display']` →
`importlib.import_module('display.<name>')`, instantiating `Display(...)`.
`base_flex.DisplayBase` loads the layout from
`config['display_data_filename']` and reads `WIDTH`/`HEIGHT` and every element's
`position`/`size` from that JSON. The framework does **not** auto-scale layouts
between resolutions; positions/sizes are authored in the JSON.

Because `display/dsi_800x480t.py` contains no hardcoded dimensions (it reads
everything from the JSON), the 1024×768 module is a thin re-export of the same
class. The only resolution-specific artifact is the layout JSON.

## Components / Files

1. **`display/dsi_1024x768t.py`** — thin module:
   ```python
   from display.dsi_800x480t import Display
   ```
   This is intentional reuse, not duplication: the class is fully
   resolution-agnostic, and a copy would risk drift. `importlib` resolves
   `Display` from this module exactly as for any other display.

2. **`display/dsi_1024x768t.json`** — the 1024×768 layout, produced by the
   deterministic transform in "Layout transform" below.

3. **`wizard/wizard_manifest.json`** — a new `modules.display.dsi_1024x768t`
   entry, copied from the `dsi_800x480t` entry with these changes:
   - `friendly_name`: `"Raspberry Pi DSI Connected Display 1024x768 w/Touch"`
   - `filename`: `"dsi_1024x768t"`
   - `description`: updated to describe a 1024×768 DSI/HDMI display
   - the `config` array's `display_data_filename` `default`:
     `"./display/dsi_1024x768t.json"`
   - all other fields (`py_dependencies`, `apt_dependencies`, `command_list`,
     `image`, `reboot_required`, `settings_dependencies`, the `rotation` and
     `input_types_supported` config options with default `["button", "touch"]`)
     are identical to `dsi_800x480t`.

No `common/common.py` or `settings.json` edits are required:
`_default_display_config()` derives `settings['display']['config'][<name>]`
from each manifest display's `config` defaults, so adding the manifest entry is
sufficient for the settings default to appear.

## Layout transform (strategy A)

`display/dsi_1024x768t.json` is generated from `display/dsi_800x480t.json` by a
deterministic transform applied independently to each profile:

- **Scale factor** = `min(1024/800, 768/480) = 1.28` for both profiles (the
  smaller ratio, so content always fits and is centered on the slack axis).
- **`profile_1` (landscape, 1024×768):** width-bound. Y offset =
  `round((768 − 480·1.28) / 2) = 77`, X offset = 0.
- **`profile_2` (portrait, 768×1024):** the framework swaps dimensions for
  90°/270° rotation, so this profile is authored for 768 wide × 1024 tall.
  Height-bound. X offset = `round((768 − 480·1.28) / 2) = 77`, Y offset = 0.
- For every object in `home`, `dash`, `menus`, and `input`:
  - `position = [round(x·1.28 + x_offset), round(y·1.28 + y_offset)]`
  - `size = [round(w·1.28), round(h·1.28)]`
  - all other keys (colors, `font`, `button_list`, `type`, `label`, etc.)
    copied unchanged.
- Fonts need no explicit handling: `flexobject.py` derives font point size from
  each object's `size`, so scaling `size` scales the text proportionally.
- **`metadata`:** `name` → `"dsi_1024x768t"`, `screen_width` → `1024`,
  `screen_height` → `768`. `dash_background` unchanged (the base class resizes
  the background image to the screen). `splash_image` per "Splash". All other
  metadata (`framerate`, `splash_delay`, `max_food_probes`, `default_profile`)
  copied unchanged.

The transform is implemented as a small generator script that reads the 800×480
JSON and writes the 1024×768 JSON; the generated JSON is committed as the
runtime artifact. Keeping the generator reproducible lets the layout be
regenerated if the 800×480 layout changes.

## Splash

`metadata.splash_image` reuses the existing `./static/img/display/splash_800x480.png`.
`base_flex._init_splash` center-pastes a splash smaller than the screen, so the
800×480 splash renders centered on the 1024×768 screen with a margin. A
dedicated `splash_1024x768.png` can be added later if edge-to-edge artwork is
wanted; it is out of scope here.

## Error Handling

No new error paths. If the layout JSON is missing or malformed, the existing
`read_generic_json` / display-load behavior applies, identical to every other
display. A fresh checkout has the committed JSON, so the module loads.

## Testing (hardware-free)

The module spawns a pygame display loop and needs a framebuffer, so tests do
**not** construct `Display`. Tests cover the verifiable artifacts:

1. **JSON validity & metadata:** `display/dsi_1024x768t.json` parses;
   `metadata.screen_width == 1024`, `metadata.screen_height == 768`,
   `metadata.name == "dsi_1024x768t"`.
2. **On-screen bounds:** for both profiles and every object in
   `home`/`dash`/`menus`/`input`, `0 ≤ x` and `x + w ≤ screen_w`, and
   `0 ≤ y` and `y + h ≤ screen_h`, where (screen_w, screen_h) is
   (1024, 768) for `profile_1` and (768, 1024) for `profile_2`.
3. **Transform correctness:** each 1024×768 object equals its 800×480
   counterpart (matched by `name`/key) scaled by 1.28 plus the profile's
   offset, within ±1 px rounding, for `position` and `size`.
4. **Module import:** `import display.dsi_1024x768t` succeeds and the module
   exposes `Display` (without constructing it).
5. **Registration:** the `dsi_1024x768t` manifest entry exists with
   `filename == "dsi_1024x768t"` and its `display_data_filename` config default
   points at `./display/dsi_1024x768t.json`; `_default_display_config()`
   includes a `dsi_1024x768t` entry.
