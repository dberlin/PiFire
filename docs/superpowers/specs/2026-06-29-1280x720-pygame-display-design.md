# 1280x720 pygame display layout — Design

**Date:** 2026-06-29
**Status:** Approved (design); implementation pending
**Author:** PiFire

## Goal

Add a 1280x720 (16:9) variant of the existing 800x480 DSI/pygame touch
display, so PiFire can drive a 720p HDMI/DSI screen. The layout is the 800x480
dash rescaled uniformly and centered — the same approach used for the existing
1024x768 variant.

## Background and the pattern being mirrored

The pygame display engine is **resolution-agnostic**: `display/base_flex.py`
reads `screen_width`/`screen_height` and every element's `position`/`size` from
a layout JSON (`display_data_filename`); nothing is hardcoded to 800x480. A new
resolution is therefore four artifacts plus tests — exactly how 1024x768
(`docs/superpowers/specs/2026-06-23-1024x768-pygame-display-design.md`) was
built:

1. A **generator script** that scales the 800x480 JSON and centers it.
2. The **generated layout JSON** (committed as the runtime artifact).
3. A **one-line re-export module** (`from display.dsi_800x480t import Display`) —
   the same `Display` class object, so there is no code to drift.
4. A **wizard manifest entry** that pairs the module with its JSON.
5. **Three tests** (layout transform/bounds, module re-export, manifest entry).

## Scaling strategy (approved: uniform, centered)

Scale every `position` and `size` by a single uniform factor and center the
result, preserving proportions so circular gauges stay circular.

**Scale factor:** `SCALE = min(1280/800, 720/480) = min(1.6, 1.5) = 1.5`.

Note the binding axis differs from 1024x768: there, width bound (1.28); here,
**height** binds (1.5). The scaled 800x480 content is 1200x720, so the slack is
**horizontal** — a 40px letterbox margin on the left and right. This matches the
approved mockup (strategy 1).

Each display profile is authored for its own rotated canvas, so offsets are
computed per profile as `round((target - source*SCALE) / 2)`:

| Profile | Orientation | Source (w×h) | Target (w×h) | SCALE | (x_off, y_off) |
|---------|-------------|--------------|--------------|-------|----------------|
| profile_1 | landscape | 800 × 480 | 1280 × 720 | 1.5 | (40, 0) |
| profile_2 | portrait  | 480 × 800 | 720 × 1280 | 1.5 | (0, 40) |

Transform applied to every layout object:
- `position = [round(x*1.5 + x_off), round(y*1.5 + y_off)]`
- `size = [round(w*1.5), round(h*1.5)]`
- All other keys (type, name, color, font, labels, …) copied unchanged.

Metadata: `name` → `"dsi_1280x720t"`, `screen_width` → `1280`,
`screen_height` → `720`. `splash_image` stays
`./static/img/display/splash_800x480.png` (the existing splash, centered on the
larger screen — no new asset, mirroring 1024x768).

## Architecture / files

| File | Responsibility |
|------|----------------|
| `tools/generate_dsi_1280x720t.py` (new) | Deterministic generator: reads `display/dsi_800x480t.json`, applies the scale/offset transform per profile, writes `display/dsi_1280x720t.json`. Mirrors `tools/generate_dsi_1024x768t.py` with `SCALE=1.5` and the 1280x720 TARGET/SOURCE dims. |
| `display/dsi_1280x720t.json` (new) | The generated layout (committed runtime artifact). |
| `display/dsi_1280x720t.py` (new) | `from display.dsi_800x480t import Display` — re-export, no copy. |
| `wizard/wizard_manifest.json` | Add the `dsi_1280x720t` display module entry. |
| `tests/test_dsi_1280x720t_layout.py` (new) | Transform + on-screen-bounds tests. |
| `tests/test_dsi_1280x720t_module.py` (new) | `mod.Display is BaseDisplay`. |
| `tests/test_dsi_1280x720t_manifest.py` (new) | Manifest entry + default display config registration. |

The base engine (`base_flex.py`, `flexobject.py`) and the 800x480 layout are
**not** modified.

### Manifest entry

Copy the `dsi_800x480t` entry verbatim, changing only:
- `friendly_name`: "DSI/HDMI Connected Display 1280x720 w/Touch"
- `filename`: `dsi_1280x720t`
- `description`: a 1280x720 (16:9) DSI/HDMI touch display; same engine as the
  800x480 DSI display, layout rescaled to 1280x720.
- the `display_data_filename` config default: `./display/dsi_1280x720t.json`

Everything else (dependencies, `input_types_supported`, `rotation`,
`settings_dependencies`) is identical to 800x480.

## Error handling / correctness

- The generator is pure and deterministic — re-runnable if the 800x480 layout
  changes.
- The layout test asserts every element stays within the screen bounds for both
  profiles (`0 ≤ x` and `x+w ≤ W`, likewise y), so a bad transform fails loudly.
- The 40px horizontal margins are intentional (uniform-scale letterboxing), not
  an error.

## Testing

Hardware-free (no `Display` construction, which needs a framebuffer), mirroring
the 1024x768 tests:

- **`test_dsi_1280x720t_layout.py`:** metadata (`name`, 1280, 720); every
  element on-screen for both profiles (SCREEN = {profile_1:(1280,720),
  profile_2:(720,1280)}); transform correctness — each object's scaled
  `position`/`size` equals `round(src*1.5 + off)` / `round(src*1.5)` using
  OFFSETS = {profile_1:(40,0), profile_2:(0,40)}.
- **`test_dsi_1280x720t_module.py`:** `import display.dsi_1280x720t`; assert
  `mod.Display is display.dsi_800x480t.Display`.
- **`test_dsi_1280x720t_manifest.py`:** the manifest `modules.display`
  `dsi_1280x720t` entry has `filename == 'dsi_1280x720t'` and a
  `display_data_filename` default of `./display/dsi_1280x720t.json`; and
  `common.common._default_display_config()` includes `dsi_1280x720t` with that
  JSON path.

## Out of scope

- **No base-engine changes** — rendering is entirely JSON-driven.
- **No new splash asset** — the existing 800x480 splash is reused centered.
- **No per-element re-layout** — this is a pure uniform scale; spreading the
  side gauges into the extra horizontal width (rather than letterboxing) is a
  possible future enhancement, deliberately not done here to stay faithful to
  the 800x480 design and the established variant pattern.
