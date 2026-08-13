"""Cover the five standalone/legacy display drivers that are not part
of the _base_fixed shim family exercised by test_fixed_base_drivers_load.py:

  ssd1306b.py, ssd1306.py, st7789p.py, prototype.py   -- fully standalone
      Display classes (no display._base_fixed / display._base_flex parent).
  ili9341f.py                                          -- subclasses
      display._base_flex.DisplayBase.

(A second _base_flex subclass formerly covered here was removed as a ~95%
duplicate of display/_base_dsi.py's engine; its coverage is now redundant
with test_pygame_qt_drivers.py's _base_dsi tests, and its unique 320x240
layout lives on as display/dsi_320x240t.json, exercised there too.)

None of these import display._base_fixed, so the ordering hazard documented
in test_fixed_base_drivers_load.py (PIL.Image.UnidentifiedImageError from
importing a driver while a hardware-stub overlay is already installed) has
not been independently reproduced against this file's target modules. The
docstring there says the pre-warm avoids "the whole hazard" empirically
found via st7789e, so `import display._base_fixed` for real, before
installing any hardware overlay, is kept below anyway as a cheap, harmless
safeguard (it is the officially documented mitigation and these drivers
share the same PIL/qrcode import machinery).

Every driver here that reaches `_init_display_device` starts a background
worker via `threading.Thread(target=...)` (ssd1306b / ssd1306 / st7789p /
ili9341f); `threading.Thread` is therefore patched to a no-op for every
construction below, matching the _base_fixed 16-driver harness.

`mock.patch.dict(sys.modules, overlay)` (used by `_load_driver` below, mirroring
test_fixed_base_drivers_load.py) does not merely remove the overlay's own keys
on exit -- it snapshots the *entire* `sys.modules` dict at __enter__ and does
`sys.modules.clear(); sys.modules.update(snapshot)` at __exit__, discarding
EVERY key added during the `with` block, not just the overlay's. This was
empirically invisible for the 16 _base_fixed drivers (their transitive imports
-- luma/gpiozero/pyky040/threading/PIL/qrcode/common -- were all either part
of the overlay itself, already pre-warmed, or already imported by pytest/its
plugins by the time any driver loaded), and remains so for the drivers left
in this file.

`os.system` is neutralized the same way as the _base_fixed harness: only
`display/_base_flex.py` (ili9341f's parent) and `display/_base_fixed.py`
actually call `os.system("... sudo reboot ...")`, but the patch is applied
unconditionally for every driver as a blanket safety measure.

`display._base_flex.DisplayBase.__init__` reads `common.system.is_real_hardware()`
(-> `read_settings()["platform"]["real_hw"]`, i.e. the SQLite settings blob) to
set `self.real_hardware`. That flag defaults to `True`
(`common/defaults.py::default_settings`) and is what a fresh store is seeded
with -- NOT a hardware probe, just a config value read verbatim. If left
unpatched, ili9341f's
`_init_display_device` (`if self.real_hardware: from rpi_backlight import
Backlight`) raises ModuleNotFoundError in this dev/CI environment, since
rpi_backlight is not installed here (mirrors test_qtquick_parity.py's existing
`monkeypatch.setattr(qmod, "is_real_hardware", lambda: False)` precedent).
`display._base_flex.is_real_hardware` is therefore forced to False for the
_base_flex-derived driver below.

st7789p.py's `_display_text`/`_display_current` used to call the Pillow
`ImageFont.getsize()` API, which Pillow removed in 10.0.0 (this environment
runs Pillow 12.3.0; even the wizard manifest's pinned `pillow==11.1.0`
postdates the removal) -- calling either method raised AttributeError. This
was a genuine latent bug, independent of this dev sandbox lacking the
`impact.ttf`/`trebuc.ttf` font files that ssd1306/ssd1306b/st7789p all
`ImageFont.truetype()` by bare filename (those two are not vendored in the
repo at all, on any driver -- presumably resolved via a system font path on
real Raspberry Pi OS that is absent here). `ImageFont.truetype` is patched
below to fall back to `ImageFont.load_default()` for missing files, isolating
the render path from that environment-only OSError. Production code now
measures text via `font.getbbox(...)`, matching the idiom already used by
the sibling ssd1306/ssd1306b/pygame_64x128 drivers; see
test_st7789p_display_text_completes_and_forwards_to_device and
test_st7789p_display_current_completes_and_forwards_to_device below. Not
validated on real ST7789 hardware.
"""

import importlib
import sys
import threading
import types
from unittest import mock

import pytest
from PIL import Image, ImageFont

import display._base_fixed  # noqa: F401  pre-warm real PIL/qrcode/common imports; see module docstring

from common.modes import Mode
from tests.ui._menu_walk import build_dash_menu_input_touch_steps, run_menu_walk

FULL_DEV_PINS = {
    "display": {"dc": 24, "led": 5, "rst": 25},
    "input": {"up_clk": 16, "down_dt": 20, "enter_sw": 21},
}

SAMPLE_IN_DATA = {
    "probe_history": {"primary": {"Grill": 225}, "food": {"Probe1": 145}},
    "primary_setpoint": 225,
    "notify_targets": {"Grill": 0, "Probe1": 165},
}
SAMPLE_STATUS_DATA = {
    "mode": "Smoke",
    "units": "F",
    "outpins": {"fan": True, "igniter": True, "auger": True},
    "notify_data": [{"req": True, "type": "probe"}],
    "hopper_level": 80,
}


# ---------------------------------------------------------------------------
# Hardware-stub overlay builders (extends the _base_fixed 16-driver harness's
# stub set: this file's drivers additionally need luma.oled/i2c (ssd1306,
# ssd1306b -- distinct from the luma.lcd/spi surface the fixed-base drivers
# use) and a real-ish luma.core.render.canvas context manager, since
# ssd1306/ssd1306b draw straight through `with canvas(self.device) as draw`
# rather than through a base class draw sink).
# ---------------------------------------------------------------------------


class _FakeLumaCanvas:
    """Stand-in for luma.core.render.canvas: a real PIL ImageDraw context
    manager over a blank image sized like the driver's own WIDTH/HEIGHT, so
    draw.text(...)/getbbox() behave like real Pillow calls. On exit it calls
    the (faked) device.display(image), matching real canvas's contract."""

    def __init__(self, device, **kwargs):
        self.device = device
        size = getattr(device, "size", None) or (128, 64)
        self.image = Image.new("RGB", size)

    def __enter__(self):
        from PIL import ImageDraw

        self.draw = ImageDraw.Draw(self.image)
        return self.draw

    def __exit__(self, *exc_info):
        self.device.display(self.image)
        return False


def _luma_oled_stubs():
    """luma.oled/i2c surface used by ssd1306.py and ssd1306b.py."""
    overlay = {}
    overlay["luma"] = types.ModuleType("luma")
    overlay["luma.core"] = types.ModuleType("luma.core")
    overlay["luma.core.interface"] = types.ModuleType("luma.core.interface")
    serial_mod = types.ModuleType("luma.core.interface.serial")
    serial_mod.i2c = mock.MagicMock(name="i2c")
    overlay["luma.core.interface.serial"] = serial_mod
    render_mod = types.ModuleType("luma.core.render")
    render_mod.canvas = _FakeLumaCanvas
    overlay["luma.core.render"] = render_mod
    overlay["luma.oled"] = types.ModuleType("luma.oled")
    device_mod = types.ModuleType("luma.oled.device")
    ssd1306_device = mock.MagicMock(name="ssd1306_device_class")
    ssd1306_device.return_value.size = (128, 64)
    ssd1306_device.return_value.mode = "1"
    device_mod.ssd1306 = ssd1306_device
    overlay["luma.oled.device"] = device_mod
    return overlay


def _gpiozero_stub():
    gz_mod = types.ModuleType("gpiozero")
    gz_mod.Button = mock.MagicMock(name="Button")
    overlay = {"gpiozero": gz_mod}
    return overlay


def _pimoroni_st7789_stub():
    st7789_mod = types.ModuleType("ST7789")
    st7789_cls = mock.MagicMock(name="ST7789_class")
    st7789_cls.return_value.width = 240
    st7789_cls.return_value.height = 240
    st7789_mod.ST7789 = st7789_cls
    return {"ST7789": st7789_mod}


def _luma_lcd_stub():
    """luma.lcd/spi surface used by ili9341f.py (mirrors the _base_fixed
    16-driver harness's luma=True stub)."""
    overlay = {}
    overlay["luma"] = types.ModuleType("luma")
    overlay["luma.core"] = types.ModuleType("luma.core")
    overlay["luma.core.interface"] = types.ModuleType("luma.core.interface")
    serial_mod = types.ModuleType("luma.core.interface.serial")
    serial_mod.spi = mock.MagicMock(name="spi")
    overlay["luma.core.interface.serial"] = serial_mod
    overlay["luma.lcd"] = types.ModuleType("luma.lcd")
    device_mod = types.ModuleType("luma.lcd.device")
    device_mod.ili9341 = mock.MagicMock(name="ili9341_device")
    overlay["luma.lcd.device"] = device_mod
    return overlay


def _pyky040_stub():
    inner = types.ModuleType("pyky040.pyky040")
    inner.Encoder = mock.MagicMock(name="Encoder")
    outer = types.ModuleType("pyky040")
    outer.pyky040 = inner
    return {"pyky040": outer, "pyky040.pyky040": inner}


def _load_driver(module_path, overlay):
    with mock.patch.dict(sys.modules, overlay):
        return importlib.import_module(module_path)


def _no_bg_threads():
    """threading.Thread patched to a no-op, matching test_fixed_base_drivers_load.py's
    _instantiate: every driver here that starts a background worker via
    threading uses `threading.Thread(target=...)`, and this patches the
    shared singleton `threading` module so it covers every call site."""
    patched = mock.patch.object(threading, "Thread")
    return patched


def _no_os_system(name):
    return mock.patch("os.system", side_effect=AssertionError(f"os.system blocked for {name}"))


_real_truetype = ImageFont.truetype


def _truetype_with_fallback(name, size, *args, **kwargs):
    """impact.ttf/trebuc.ttf are not vendored anywhere in this repo (see
    module docstring); fall back to the Pillow default bitmap font so
    draw-method bodies can execute instead of raising OSError on missing
    font file. Fonts that ARE vendored (static/font/FA-Free-Solid.otf) still
    load for real."""
    try:
        return _real_truetype(name, size, *args, **kwargs)
    except OSError:
        return ImageFont.load_default()


@pytest.fixture(autouse=True)
def _patch_missing_fonts():
    with mock.patch("PIL.ImageFont.truetype", side_effect=_truetype_with_fallback):
        yield


# ---------------------------------------------------------------------------
# ssd1306.py -- standalone, no input/menu, simplest of the six.
# ---------------------------------------------------------------------------


def _make_ssd1306():
    mod = _load_driver("display.ssd1306", _luma_oled_stubs())
    with _no_bg_threads() as mock_thread, _no_os_system("ssd1306"):
        mock_thread.return_value.start = lambda: None
        d = mod.Display(dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=0, units="F", config={})
    return mod, d


# _FakeLumaCanvas backs `self.device` (a MagicMock) with a real PIL image and,
# on context-manager exit, calls device.display(image) exactly like the real
# luma canvas -- so the last rendered frame is recoverable from
# device.display.call_args and is real pixel data, not a stand-in.
#
# These boxes are generous crops around each icon's draw.text(...) call site
# in display/ssd1306.py's _display_current (fan/igniter/auger at fixed
# corners, the notify bell top-right), sized from the real, vendored
# static/font/FA-Free-Solid.otf glyphs actually drawn there (confirmed via a
# standalone bbox probe: fan (-1,-1,25,25), igniter (0,25,18,49), auger
# (107,31,128,48), notify (106,0,128,24)). The grill-temp and mode text use
# impact.ttf/trebuc.ttf, which _patch_missing_fonts falls back to Pillow's
# tiny default bitmap font for (missing from the repo) -- their bboxes land
# at roughly x:[55,73] y:[2,10] and x:[34,94] y:[56,64], well outside every
# icon box below, so a lit icon can't be mistaken for temp/mode text.
_ICON_REGIONS = {
    "fan": (0, 0, 26, 26),
    "igniter": (0, 24, 20, 50),
    "auger": (100, 26, 128, 50),
    "notify": (100, 0, 128, 26),
}


def _rendered_icons(d):
    """Return the set of icon names whose region in the last frame sent to
    d.device.display(...) contains any drawn (non-background) pixel."""
    image = d.device.display.call_args.args[0]
    lit = set()
    for name, box in _ICON_REGIONS.items():
        if image.crop(box).getbbox() is not None:
            lit.add(name)
    return lit


def test_ssd1306_constructs_and_sizes():
    mod, d = _make_ssd1306()
    assert (d.WIDTH, d.HEIGHT) == (128, 64)
    assert d.device is not None


def test_ssd1306_draw_methods_forward_to_device():
    mod, d = _make_ssd1306()
    d.device.display.reset_mock()

    d._display_splash()
    assert d.device.display.called

    d.display_data = "225"
    d._display_text()
    assert d.device.display.call_count == 2

    d._display_current(SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    assert d.device.display.call_count == 3

    d._display_clear()
    d.device.clear.assert_called_once()


def test_ssd1306_display_current_all_outpins_off_and_no_notify():
    # Cover the "nothing lit up" branches (fan/igniter/auger False, no
    # notify_data requiring a bell icon) -- and assert the outcome, rather
    # than only that rendering did not raise.
    mod, d = _make_ssd1306()
    status = dict(SAMPLE_STATUS_DATA)
    status["outpins"] = {"fan": False, "igniter": False, "auger": False}
    status["notify_data"] = []

    d._display_current(SAMPLE_IN_DATA, status)

    lit = _rendered_icons(d)
    assert lit == set(), f"nothing should be lit, got {lit}"


def test_ssd1306_public_status_methods_set_state_without_touching_device():
    mod, d = _make_ssd1306()
    d.device.display.reset_mock()

    d.display_status(SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    assert d.in_data == SAMPLE_IN_DATA
    assert d.status_data == SAMPLE_STATUS_DATA
    assert d.units == SAMPLE_STATUS_DATA["units"]

    d.display_splash()
    assert d.display_command == "splash"
    d.clear_display()
    assert d.display_command == "clear"
    d.display_text("hi")
    assert d.display_command == "text" and d.display_data == "hi"
    d.display_network()
    assert d.display_command == "network"

    # None of the public setters touch the device directly; only the
    # (unstarted) _display_loop would.
    assert not d.device.display.called


# ---------------------------------------------------------------------------
# ssd1306b.py -- adds gpiozero button input + a full _menu_display state
# machine driven off common.persistence.control.read_control/enqueue_control_delta.
# ---------------------------------------------------------------------------


def _fake_control_pair(initial_mode=Mode.STOP):
    """A stand-in control process for the fixed-layout drivers.

    Displays queue delta envelopes (common/control_delta.py), so this applies
    them the way the real drain does. What it records in `calls` is the members
    the delta actually changed, so an assertion like calls[-1][0]["mode"] still
    reads "the write said mode".
    """
    from common.control_delta import apply_control_delta
    from common.defaults import default_control

    state = default_control()
    state["mode"] = initial_mode
    calls = []

    def read_control():
        return dict(state)

    def enqueue_control_delta(delta, *, origin=None):
        calls.append((dict(delta.get("set", {})), origin))
        apply_control_delta(state, delta)

    return state, read_control, enqueue_control_delta, calls


def _make_ssd1306b(monkeypatch, initial_mode=Mode.STOP):
    overlay = dict(_luma_oled_stubs())
    overlay.update(_gpiozero_stub())
    mod = _load_driver("display.ssd1306b", overlay)
    state, read_control, enqueue_control_delta, calls = _fake_control_pair(initial_mode)
    monkeypatch.setattr(mod, "read_control", read_control)
    monkeypatch.setattr(mod, "enqueue_control_delta", enqueue_control_delta)
    with _no_bg_threads() as mock_thread, _no_os_system("ssd1306b"):
        mock_thread.return_value.start = lambda: None
        d = mod.Display(dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=0, units="F", config={})
    return mod, d, state, calls


def test_ssd1306b_constructs_with_buttons_and_menu():
    mod, d, _state, _calls = _make_ssd1306b(pytest.MonkeyPatch())
    assert (d.WIDTH, d.HEIGHT) == (128, 64)
    assert d.up_button is not None and d.down_button is not None and d.enter_button is not None
    assert "inactive" in d.menu and "active" in d.menu


def test_ssd1306b_input_callbacks_set_event(monkeypatch):
    _mod, d, _state, _calls = _make_ssd1306b(monkeypatch)
    d._up_callback()
    assert d.input_event == "UP"
    d._down_callback()
    assert d.input_event == "DOWN"
    d._enter_callback()
    assert d.input_event == "ENTER"


def test_ssd1306b_draw_methods_forward_to_device(monkeypatch):
    _mod, d, _state, _calls = _make_ssd1306b(monkeypatch)
    d.device.display.reset_mock()

    d._display_splash()
    assert d.device.display.called

    d.display_data = "225"
    d._display_text()
    assert d.device.display.call_count == 2

    d._display_current(SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    assert d.device.display.call_count == 3

    d._display_network("192.168.1.50")  # real qrcode.make(...) call
    assert d.device.display.call_count == 4

    d._display_clear()
    d.device.clear.assert_called_once()


def test_ssd1306b_event_detect_opens_inactive_menu(monkeypatch):
    _mod, d, state, _calls = _make_ssd1306b(monkeypatch, initial_mode=Mode.STOP)
    d.device.display.reset_mock()

    d.input_event = "ENTER"
    d._event_detect()  # mode == none -> opens "inactive" menu (Stop mode)
    assert d.menu["current"]["mode"] == "inactive"
    assert d.menu_active is True
    assert d.device.display.called  # _menu_display drew the menu canvas


def test_ssd1306b_menu_navigates_and_selects_startup(monkeypatch):
    _mod, d, state, calls = _make_ssd1306b(monkeypatch, initial_mode=Mode.STOP)

    d.input_event = "ENTER"
    d._event_detect()  # open inactive menu, option 0 == "Startup"
    assert d.menu["current"]["mode"] == "inactive"

    d.input_event = "DOWN"
    d._event_detect()  # wrap to last item
    d.input_event = "UP"
    d._event_detect()  # back to "Startup"

    d.input_event = "ENTER"
    d._event_detect()  # selects "Startup" -> enqueue_control_delta(mode=Mode.STARTUP)
    assert d.menu["current"]["mode"] == "none"
    assert calls[-1][0]["mode"] == Mode.STARTUP


def test_ssd1306b_menu_active_mode_shutdown_and_network(monkeypatch):
    _mod, d, state, calls = _make_ssd1306b(monkeypatch, initial_mode=Mode.HOLD)

    d.input_event = "ENTER"
    d._event_detect()  # active menu (mode=Hold != inactive modes)
    assert d.menu["current"]["mode"] == "active"

    d.input_event = "ENTER"
    d._event_detect()  # selects "Shutdown" (first entry)
    assert calls[-1][0]["mode"] == Mode.SHUTDOWN

    # Re-open and walk to "Network" (last item) to hit display_network().
    # DOWN decrements the option index and wraps to len-1 when it goes
    # negative, so a single DOWN from option 0 lands directly on the last
    # entry ("Network").
    d.input_event = "ENTER"
    d._event_detect()
    d.input_event = "DOWN"
    d._event_detect()
    d.input_event = "ENTER"
    d._event_detect()
    assert d.display_command == "network"


def test_ssd1306b_menu_grill_hold_value_adjusts_and_commits(monkeypatch):
    _mod, d, state, calls = _make_ssd1306b(monkeypatch, initial_mode=Mode.HOLD)

    d.input_event = "ENTER"
    d._event_detect()  # active menu
    # Walk to "Hold" (2nd entry, index 1). UP increments the option index
    # (DOWN decrements and wraps the other way -- see _menu_display).
    d.input_event = "UP"
    d._event_detect()
    d.input_event = "ENTER"
    d._event_detect()
    assert d.menu["current"]["mode"] == "grill_hold_value"
    assert d.menu["current"]["option"] == 225

    d.input_event = "UP"
    d._event_detect()
    assert d.menu["current"]["option"] == 230
    d.input_event = "DOWN"
    d._event_detect()
    assert d.menu["current"]["option"] == 225

    d.input_event = "ENTER"
    d._event_detect()
    assert calls[-1][0]["mode"] == Mode.HOLD
    assert calls[-1][0]["primary_setpoint"] == 225


def test_ssd1306b_menu_grill_hold_value_celsius_bounds(monkeypatch):
    _mod, d, state, calls = _make_ssd1306b(monkeypatch, initial_mode=Mode.HOLD)
    d.units = "C"

    d.input_event = "ENTER"
    d._event_detect()
    d.input_event = "UP"  # navigate to "Hold" (index 1)
    d._event_detect()
    d.input_event = "ENTER"
    d._event_detect()
    assert d.menu["current"]["option"] == 100  # C starting point

    d.input_event = "DOWN"
    d._event_detect()
    assert d.menu["current"]["option"] == 98  # stepValue 2 in C


def test_ssd1306b_menu_smokeplus_toggle(monkeypatch):
    _mod, d, state, calls = _make_ssd1306b(monkeypatch, initial_mode=Mode.HOLD)
    d.input_event = "ENTER"
    d._event_detect()
    for _ in range(4):  # UP: Shutdown(0)->Hold(1)->Smoke(2)->Stop(3)->SmokePlus(4)
        d.input_event = "UP"
        d._event_detect()
    d.input_event = "ENTER"
    d._event_detect()
    assert calls[-1][0]["s_plus"] is True


def test_ssd1306b_menu_monitor_and_stop_from_inactive(monkeypatch):
    _mod, d, state, calls = _make_ssd1306b(monkeypatch, initial_mode=Mode.STOP)
    d.input_event = "ENTER"
    d._event_detect()
    d.input_event = "UP"
    d._event_detect()  # Startup(0)->Monitor(1)
    d.input_event = "ENTER"
    d._event_detect()
    assert calls[-1][0]["mode"] == Mode.MONITOR

    d.input_event = "ENTER"
    d._event_detect()
    d.input_event = "UP"
    d._event_detect()
    d.input_event = "UP"
    d._event_detect()  # Startup(0)->Monitor(1)->Stop(2)
    d.input_event = "ENTER"
    d._event_detect()
    assert calls[-1][0]["mode"] == Mode.STOP


def test_ssd1306b_event_detect_ignores_unknown_and_menu_timeout(monkeypatch):
    _mod, d, state, calls = _make_ssd1306b(monkeypatch)
    d.input_event = "SOMETHING_ELSE"
    d._event_detect()
    assert d.menu_active is False  # unrecognized command -> early return

    d.input_event = None
    d._event_detect()  # no-op branch (falsy input_event)


def test_ssd1306b_public_status_methods(monkeypatch):
    _mod, d, _state, _calls = _make_ssd1306b(monkeypatch)
    d.display_status(SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    assert d.in_data == SAMPLE_IN_DATA
    d.display_splash()
    assert d.display_command == "splash"
    d.clear_display()
    assert d.display_command == "clear"
    d.display_text("hi")
    assert d.display_command == "text"
    d.display_network()
    assert d.display_command == "network"


# ---------------------------------------------------------------------------
# st7789p.py -- standalone Pimoroni ST7789 driver.
# ---------------------------------------------------------------------------


def _make_st7789p():
    mod = _load_driver("display.st7789p", _pimoroni_st7789_stub())
    with _no_bg_threads() as mock_thread, _no_os_system("st7789p"):
        mock_thread.return_value.start = lambda: None
        d = mod.Display(dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=0, units="F", config={})
    return mod, d


def test_st7789p_constructs_and_takes_device_geometry():
    mod, d = _make_st7789p()
    assert (d.WIDTH, d.HEIGHT) == (240, 240)


def test_st7789p_splash_and_clear_forward_to_device():
    mod, d = _make_st7789p()
    d.device.display.reset_mock()

    d._display_splash()
    assert d.device.display.called

    d._display_clear()
    assert d.device.display.call_count == 2
    d.device.set_backlight.assert_called_with(0)


def test_st7789p_display_text_completes_and_forwards_to_device():
    """FIXED: st7789p._display_text used to call the removed Pillow
    ImageFont.getsize() API (removed in Pillow 10.0.0; this env runs Pillow
    12.3.0), raising AttributeError on every render. Production code now
    measures text via font.getbbox(...), matching the idiom already used by
    the sibling ssd1306/ssd1306b/pygame_64x128 drivers (font_width =
    bbox[2], font_height = bbox[3]). ImageFont.truetype is patched (fixture)
    to succeed even though impact.ttf is not vendored in this repo, isolating
    this test to the render path itself, not an environment font-availability
    gap. Not validated on real ST7789 hardware."""
    mod, d = _make_st7789p()
    d.device.display.reset_mock()
    d.display_data = "225"

    d._display_text()

    assert d.device.display.called


def test_st7789p_display_current_completes_and_forwards_to_device():
    """See test_st7789p_display_text_completes_and_forwards_to_device --
    _display_current hits the same fixed getbbox text-measurement path, at
    every callsite (grill temp, fan/igniter/auger icons, notification icon,
    mode label). Not validated on real ST7789 hardware."""
    mod, d = _make_st7789p()
    d.device.display.reset_mock()

    d._display_current(SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)

    assert d.device.display.called


def test_st7789p_public_status_methods():
    mod, d = _make_st7789p()
    d.display_status(SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    assert d.in_data == SAMPLE_IN_DATA and d.units == SAMPLE_STATUS_DATA["units"]
    d.display_splash()
    assert d.display_command == "splash"
    d.clear_display()
    assert d.display_command == "clear"
    d.display_text("hi")
    assert d.display_command == "text" and d.display_data == "hi"
    d.display_network()  # a no-op `pass` on this driver


def test_st7789p_device_geometry_overridden_by_hardware():
    mod = _load_driver("display.st7789p", _pimoroni_st7789_stub())
    mod.ST7789.ST7789.return_value.width = 135
    mod.ST7789.ST7789.return_value.height = 240
    with _no_bg_threads() as mock_thread, _no_os_system("st7789p"):
        mock_thread.return_value.start = lambda: None
        d = mod.Display(dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=180, units="F", config={})
    assert (d.WIDTH, d.HEIGHT) == (135, 240)


# ---------------------------------------------------------------------------
# ili9341f.py -- display._base_flex.DisplayBase subclass.
# ---------------------------------------------------------------------------

# Used by ili9341f's _configure_dash() (reads config["probe_info"]),
# mirroring test_base_flex_dash_update.py's _config().
_PROBE_INFO = {
    "primary": {"name": "Grill"},
    "food": [{"label": "Probe1", "name": "Chicken"}],
}


def _flex_status_data(**overrides):
    base = {
        "mode": "Stop",
        "units": "F",
        "recipe": False,
        "recipe_paused": False,
        "outpins": {"fan": False, "auger": False, "igniter": False},
        "hopper_level_enabled": True,
        "hopper_level": 80,
        "notify_data": [],
        "s_plus": False,
        "p_mode": 0,
        "cycle_ratio": 0,
        "fan_duty": 0,
        "start_time": 0,
        "start_duration": 0,
        "prime_duration": 0,
        "shutdown_duration": 0,
        "startup_timestamp": 0,
        "lid_open_detected": False,
        "lid_open_endtime": 0,
    }
    base.update(overrides)
    return base


class _FakeFlexObject:
    """A minimal stand-in for a display.flexobject.FlexObject, giving full
    control over button_list/button_selected/touch_areas/data so
    _process_button/_process_touch's deeper branches (cmd_ dispatch,
    menu_close, nested menu_/input_ navigation, button_ input editing, touch
    hit-testing) can be driven directly without reconstructing the real
    layout-JSON-driven object graph test_base_flex_dash_update.py already
    covers for the base class itself."""

    def __init__(
        self, button_list, button_selected=1, button_value=None, touch_areas=None, extra_data=None, command=None
    ):
        data = {"button_selected": button_selected}
        if extra_data:
            data.update(extra_data)
        self._obj = {"button_list": button_list, "data": data, "touch_areas": touch_areas or []}
        if button_value is not None:
            self._obj["button_value"] = button_value
        if command is not None:
            self._obj["command"] = command

    def get_object_data(self):
        return self._obj

    def update_object_data(self, updated_objectData=None):
        self._obj = updated_objectData


def _drive_flex_input_and_menu_coverage(d, monkeypatch):
    """Exercises _event_detect/_process_button/_process_touch's branches for
    ili9341f.py. The same _base_flex-era input dispatch code is duplicated
    into display/_base_dsi.py and is covered directly there by
    test_pygame_qt_drivers.py, using dsi_800x480t.json's layout instead of
    reconstructing exact ili9341f layout-JSON button lists. Real dash
    construction (_init_dash -> real FlexObject instances, already covered
    by test_base_flex_dash_update.py for the base class) drives the
    dash->menu transition; a fully-controlled _FakeFlexObject drives the
    deeper menu/input/touch branches that would otherwise require
    reconstructing exact layout-JSON button lists.

    The actual step-by-step walk (dash->menu_main via "UP", every Mode's
    menu_main_* branch, deep menu/input navigation, touch dispatch) is
    shared with test_pygame_qt_drivers.py's _base_dsi walk -- see
    tests/ui/_menu_walk.py for the parts that differ between the two
    (entry event, status-data shape, and _base_dsi's pygame.time.delay
    patch requirement)."""
    d.status_data = _flex_status_data()
    d.last_status_data = {"lid_open_detected": False}
    d.in_data = None
    d.last_in_data = {}
    d.display_active = "dash"
    d._init_dash()

    monkeypatch.setattr(d, "_command_handler", lambda: None)

    steps = build_dash_menu_input_touch_steps(
        d, entry_event="UP", status_data_factory=_flex_status_data, fake_flex_object_cls=_FakeFlexObject
    )
    run_menu_walk(d, steps)


def _ili9341f_config(**overrides):
    config = {
        "display_data_filename": "./display/ili9341f_320x240.json",
        "input_types_supported": ["button", "encoder", "touch"],
        "buttonslevel": "HIGH",
        "rotation": 0,
        "spi_device": 0,
        "probe_info": _PROBE_INFO,
    }
    config.update(overrides)
    return config


def _make_ili9341f(monkeypatch, **config_overrides):
    overlay = dict(_luma_lcd_stub())
    overlay.update(_gpiozero_stub())
    overlay.update(_pyky040_stub())
    mod = _load_driver("display.ili9341f", overlay)
    monkeypatch.setattr(mod, "is_real_hardware", lambda: False, raising=False)
    import display._base_flex as base_flex_mod

    monkeypatch.setattr(base_flex_mod, "is_real_hardware", lambda: False)
    config = _ili9341f_config(**config_overrides)
    with _no_bg_threads() as mock_thread, _no_os_system("ili9341f"):
        mock_thread.return_value.start = lambda: None
        d = mod.Display(
            dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=config["rotation"], units="F", config=config
        )
    return mod, d


def test_ili9341f_constructs_device_and_input(monkeypatch):
    mod, d = _make_ili9341f(monkeypatch)
    assert d.device is not None
    assert d.up_button is not None and d.encoder is not None
    assert d.background is not None and d.display_canvas is not None


def test_ili9341f_rotation_90_translates_dimensions(monkeypatch):
    mod, d = _make_ili9341f(monkeypatch, rotation=90)
    # ili9341f swaps translated_width/height for odd rotations when talking
    # to the device constructor; the shim/base itself already sets
    # WIDTH/HEIGHT from the json metadata (320x240 base profile).
    assert mod.ili9341.call_args.kwargs["width"] == d.HEIGHT
    assert mod.ili9341.call_args.kwargs["height"] == d.WIDTH


def test_ili9341f_wake_sleep_and_clear_forward_to_device(monkeypatch):
    _mod, d = _make_ili9341f(monkeypatch)
    d._wake_display()
    d.device.backlight.assert_any_call(True)
    d.device.show.assert_called()

    d._sleep_display()
    d.device.backlight.assert_any_call(False)
    d.device.hide.assert_called()

    d._display_clear()
    d.device.clear.assert_called_once()


def test_ili9341f_display_canvas_forwards_rgb_image(monkeypatch):
    _mod, d = _make_ili9341f(monkeypatch)
    d._display_canvas()
    d.device.display.assert_called_once()
    (sent_image,) = d.device.display.call_args.args
    assert sent_image.mode == "RGB"


def test_ili9341f_background_and_menu_background_roundtrip(monkeypatch):
    _mod, d = _make_ili9341f(monkeypatch)
    d._display_background()  # paste real background.png onto display_canvas
    d._capture_background()  # blur canvas -> menu_background
    assert d.menu_background is not None
    d._display_menu_background()  # paste menu_background back onto canvas


def test_ili9341f_button_callbacks(monkeypatch):
    _mod, d = _make_ili9341f(monkeypatch)
    d._up_callback()
    assert d.input_event == "UP"
    d._down_callback()
    assert d.input_event == "DOWN"
    d._enter_callback()
    assert d.input_event == "ENTER"


def test_ili9341f_encoder_callbacks_set_events(monkeypatch):
    # _inc_callback/_dec_callback both gate on `self.last_direction` and a
    # 0.5s debounce window (see the callback bodies): _inc_callback only
    # fires when last_direction is None/"DOWN"/stale, _dec_callback only
    # when None/"UP"/stale. Reset both between calls to exercise each
    # independently rather than accidentally hitting the debounce no-op.
    _mod, d = _make_ili9341f(monkeypatch)
    d.last_direction = None
    d.last_movement_time = 0
    d._inc_callback(1)
    assert d.input_event == "DOWN"

    d.last_direction = None
    d.last_movement_time = 0
    d._dec_callback(1)
    assert d.input_event == "UP"

    d._click_callback()
    assert d.input_event == "ENTER" and d.enter_received is True


def test_ili9341f_no_input_types_disables_input(monkeypatch):
    mod, d = _make_ili9341f(monkeypatch, input_types_supported=["none"])
    assert d.input_enabled is False


def test_ili9341f_event_detect_and_menu_touch_branches(monkeypatch):
    _mod, d = _make_ili9341f(monkeypatch)
    _drive_flex_input_and_menu_coverage(d, monkeypatch)


# ---------------------------------------------------------------------------
# prototype.py -- curses-based terminal simulator. Does NOT subclass
# _base_fixed/_base_flex, and calls curses.wrapper(self._curses_main) directly
# from __init__, which under a real curses import requires a controlling
# terminal (raises curses.error / fails initscr under pytest's captured,
# non-tty stdout). curses itself is therefore stubbed out via the same
# sys.modules-overlay technique used for the hardware libraries above,
# rather than actually initializing a terminal -- there is no real hardware
# dependency here to preserve realism for, just an unavailable tty.
# ---------------------------------------------------------------------------


class _FakeCursesScreen:
    def __init__(self):
        self.calls = []

    def clear(self):
        self.calls.append(("clear",))

    def getmaxyx(self):
        return (24, 80)

    def box(self):
        self.calls.append(("box",))

    def addstr(self, *args):
        self.calls.append(("addstr", args))

    def refresh(self):
        self.calls.append(("refresh",))


def _curses_stub():
    curses_mod = types.ModuleType("curses")

    def wrapper(func):
        func(curses_mod._screen)

    curses_mod.wrapper = wrapper
    curses_mod._screen = _FakeCursesScreen()
    curses_mod.curs_set = mock.MagicMock(name="curs_set")
    curses_mod.start_color = mock.MagicMock(name="start_color")
    curses_mod.init_pair = mock.MagicMock(name="init_pair")
    curses_mod.color_pair = mock.MagicMock(name="color_pair", return_value=0)
    for i, name in enumerate(
        ["COLOR_BLUE", "COLOR_BLACK", "COLOR_CYAN", "COLOR_WHITE", "COLOR_YELLOW", "COLOR_RED", "COLOR_GREEN"]
    ):
        setattr(curses_mod, name, i)
    return {"curses": curses_mod}


def _make_prototype():
    mod = _load_driver("display.prototype", _curses_stub())
    # prototype.py neither spawns a threading.Thread nor calls os.system, but
    # the guards are applied here too so the invariant documented in the
    # module docstring ("unconditionally for every driver") actually holds
    # for all six drivers, not just five of them -- defense-in-depth, not a
    # behavior change (see _no_bg_threads/_no_os_system docstrings above).
    with _no_bg_threads() as mock_thread, _no_os_system("prototype"):
        mock_thread.return_value.start = lambda: None
        with mock.patch("time.sleep"):  # display_splash() sleeps 1s for real otherwise
            d = mod.Display(dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=0, units="F", config={})
    return mod, d


def test_prototype_constructs_and_shows_splash_on_init():
    mod, d = _make_prototype()
    assert d.screen is not None
    # __init__ calls self.display_splash() itself; confirm it drew something.
    assert any(call[0] == "addstr" for call in d.screen.calls)


def test_prototype_display_status_draws_probe_and_notify_and_pins(monkeypatch):
    mod, d = _make_prototype()
    d.screen.calls.clear()
    status_data = {
        "mode": "Hold",
        "notify_data": [{"req": True, "label": "Grill"}, {"req": False, "label": "Probe1"}],
        "outpins": {"fan": True, "igniter": False, "auger": True},
        "hopper_level": 80,
    }
    in_data = {
        "probe_history": {"primary": {"Grill": 225}, "food": {"Probe1": 145}, "tr": {"ignored": 1}},
        "primary_setpoint": 225,
        "notify_targets": {"Grill": 0, "Probe1": 165},
    }
    d.display_status(in_data, status_data)
    assert any(call[0] == "refresh" for call in d.screen.calls)


def test_prototype_display_status_low_and_mid_hopper_levels(monkeypatch):
    # Cover the hopper-level color branches (<25 red, 25-70 yellow) and
    # assert they actually select different curses color pairs -- the
    # "renders differently" behavior the test name promises, rather than
    # only that display_status() completes without raising. curses.color_pair
    # is a MagicMock with a fixed return_value (see _curses_stub), so the
    # *return value* forwarded into addstr can't distinguish the branches;
    # the call args recorded on the mock can.
    mod, d = _make_prototype()
    status_data = {
        "mode": "Stop",
        "notify_data": [],
        "outpins": {"fan": False, "igniter": False, "auger": False},
        "hopper_level": 10,
    }
    in_data = {"probe_history": {"primary": {}}, "primary_setpoint": 0, "notify_targets": {}}
    d.display_status(in_data, status_data)  # hopper_level < 25 branch
    low_pair = mod.curses.color_pair.call_args_list[-1]

    status_data["hopper_level"] = 50
    d.display_status(in_data, status_data)  # 25 <= hopper_level < 70 branch
    mid_pair = mod.curses.color_pair.call_args_list[-1]

    assert low_pair == mock.call(5), f"hopper_level=10 should select the red pair, got {low_pair}"
    assert mid_pair == mock.call(4), f"hopper_level=50 should select the yellow pair, got {mid_pair}"
    assert low_pair != mid_pair, "low and mid hopper branches must select different color pairs"


def test_prototype_clear_display_and_display_text():
    mod, d = _make_prototype()
    d.screen.calls.clear()
    d.clear_display()
    assert any(c[0] == "refresh" for c in d.screen.calls)

    d.screen.calls.clear()
    d.display_text("hello")
    assert any(c[0] == "addstr" for c in d.screen.calls)


def test_prototype_display_network_is_a_noop():
    mod, d = _make_prototype()
    d.display_network()  # `pass` -- just proves it doesn't raise
