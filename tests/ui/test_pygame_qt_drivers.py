"""Task 8: cover the pygame/Qt/DSI display drivers not exercised elsewhere:

  pygame_64x128.py    -- standalone Display class (no base_fixed/base_flex
                          parent), tiny 128x64 debug panel.
  pygame_240x320.py,
  pygame_240x320b.py  -- display.base_fixed.DisplayBase subclasses (via the
                          base_240x320 shim), part of the same 16-driver
                          family exercised (construction-only) by
                          test_fixed_base_drivers_load.py. This file drives
                          each one's actual status-render path (splash/text/
                          current/network/clear), which that file does not.
  dsi_base.py         -- display.base_flex.DisplayBase subclass (a "flex"
                          driver over a real pygame framebuffer surface,
                          faked here exactly like test_fixed_drivers_methods
                          .py's ili9341f coverage: construct with the
                          multiprocessing display worker and threading
                          menu timers patched to no-ops, then drive the
                          render/input methods directly rather than running
                          the real infinite _display_loop). This is the
                          resolution-agnostic engine behind the dsi_800x480t/
                          dsi_1024x600t/dsi_1024x768t/dsi_1280x720t/
                          dsi_320x240t stubs (the last of these re-homes a
                          removed sibling module's unique compact 320x240
                          layout onto this engine); this file exercises it
                          via both the 800x480 and 320x240 JSON layouts.
  qtapp.py            -- the Qt Quick display-process host: build_engine/
                          build_backend/_fetch/_make_backlight/DummyBacklight
                          /bind_backend_power, driven directly under
                          QT_QPA_PLATFORM=offscreen. `run_app()` itself is
                          NOT called (it blocks forever in `app.exec()`).

None of the four pygame drivers spawn a real background thread/process
during these tests: `threading.Thread` (all four) and `multiprocessing.
Process` (dsi_base only) are patched to no-ops for every construction,
mirroring test_fixed_base_drivers_load.py / test_fixed_drivers_methods.py's
`_no_bg_threads`/`_no_multiprocessing` guards, so no infinite `while True`
loop ever actually starts. `os.system` is blocked (raises AssertionError) for
every base_fixed/base_flex construction as a blanket safety measure, even
though none of the paths exercised here reach it (only `_menu_display`'s
"Power_"/reboot branches do, which this file does not select).

trebuc.ttf/impact.ttf (bare filenames, resolved via system fontconfig on
this dev box) and FA-Free-Solid.otf (vendored in static/font/) all load
successfully here -- unlike the sibling st7789p driver, none of these five
modules call the removed Pillow `ImageFont.getsize()` API (all use
`font.getbbox()`), so no font-fallback fixture or pinned-bug test is needed.
"""

import contextlib
import multiprocessing
import threading
import time
from pathlib import Path
from unittest import mock

import pygame
import pytest
from PIL import ImageDraw
from PySide6.QtGui import QGuiApplication

import display.dsi_base as dsi_mod
import display.pygame_240x320 as mod_320
import display.pygame_240x320b as mod_320b
import display.pygame_64x128 as mod_64
import display.qtapp as qtapp_mod
from common.modes import Mode
from display.qtbackend import PiFireBackend

FULL_DEV_PINS = {
    "display": {"dc": 24, "led": 5, "rst": 25},
    "input": {"up_clk": 16, "down_dt": 20, "enter_sw": 21},
}


# ---------------------------------------------------------------------------
# pygame_64x128.py -- standalone Display class.
# ---------------------------------------------------------------------------


def _make_pygame_64x128():
    with mock.patch.object(threading, "Thread") as mock_thread:
        mock_thread.return_value.start = lambda: None
        d = mod_64.Display(dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=0, units="F", config={})
    return d


_IN_DATA_64 = {"probe_history": {"primary": {"Grill": 225}}}
_STATUS_64 = {
    "units": "F",
    "outpins": {"fan": True, "igniter": True, "auger": True},
    "mode": "Hold",
    "notify_data": [{"req": True, "type": "probe"}],
}


def test_pygame_64x128_constructs():
    d = _make_pygame_64x128()
    assert (d.WIDTH, d.HEIGHT) == (128, 64)
    assert d.splash is not None


def test_pygame_64x128_draw_methods_run_without_error():
    d = _make_pygame_64x128()
    pygame.init()
    try:
        d.display_surface = pygame.display.set_mode(size=(d.WIDTH, d.HEIGHT), flags=pygame.SHOWN)
        blank = pygame.image.tobytes(d.display_surface, "RGB")

        d._display_splash()
        after_splash = pygame.image.tobytes(d.display_surface, "RGB")

        d.display_data = "225"
        d._display_text()
        after_text = pygame.image.tobytes(d.display_surface, "RGB")

        d._display_current(_IN_DATA_64, _STATUS_64)
        assert d.units == "F"
        after_current = pygame.image.tobytes(d.display_surface, "RGB")

        # "nothing lit up, no notification" branches.
        status_off = dict(_STATUS_64, outpins={"fan": False, "igniter": False, "auger": False}, notify_data=[])
        d._display_current(_IN_DATA_64, status_off)

        d._display_network("192.168.1.50")  # real qrcode.make(...) call
        after_network = pygame.image.tobytes(d.display_surface, "RGB")

        d._display_clear()
        after_clear = pygame.image.tobytes(d.display_surface, "RGB")

        # Real-draw proof: each stage's fill+blit actually changed the real
        # surface's pixels, not just "didn't raise".
        assert after_splash != blank, "_display_splash did not draw anything"
        assert after_text != after_splash, "_display_text did not draw anything new"
        assert after_current != after_text, "_display_current did not draw anything new"
        assert after_network != after_current, "_display_network (QR code) did not draw anything new"
        assert after_clear != after_network, "_display_clear did not draw anything"
    finally:
        pygame.quit()


def test_pygame_64x128_public_status_methods():
    d = _make_pygame_64x128()
    d.display_status(_IN_DATA_64, _STATUS_64)
    assert d.in_data == _IN_DATA_64 and d.units == "F" and d.display_active is True

    d.display_splash()
    assert d.display_command == "splash"
    d.clear_display()
    assert d.display_command == "clear"
    d.display_text("hi")
    assert d.display_command == "text" and d.display_data == "hi"
    d.display_network()
    assert d.display_command == "network"


# ---------------------------------------------------------------------------
# pygame_240x320.py / pygame_240x320b.py -- display.base_fixed.DisplayBase
# subclasses (via the base_240x320 shim). Same construction pattern as
# test_fixed_base_drivers_load.py's 16-driver harness (no hardware-stub
# overlay needed: neither driver imports a hardware library).
# ---------------------------------------------------------------------------


def _instantiate_fixed(mod, **overrides):
    kwargs = dict(dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=0, units="F", config={})
    kwargs.update(overrides)
    with (
        mock.patch.object(threading, "Thread") as mock_thread,
        mock.patch("os.system", side_effect=AssertionError(f"os.system blocked for {mod.__name__}")),
    ):
        mock_thread.return_value.start = lambda: None
        return mod.Display(**kwargs)


@contextlib.contextmanager
def _fixed_driver_guarded(mod, **overrides):
    """Constructs a base_fixed-family driver (pygame_240x320/240x320b) with
    `os.system` kept neutralized as a recording no-op for the FULL body of
    the `with` block -- not just construction.

    `base_fixed._menu_display`'s "Power_" branch calls
    `os.system("... sudo reboot ...")` / `os.system("... shutdown ...")`
    UNCONDITIONALLY, with no `real_hardware` gate (unlike base_flex's
    equivalent in `_command_handler`). Any test that goes on to call
    `_event_detect()` / `_display_loop()` / otherwise drive menu navigation
    on one of these drivers must keep `os.system` patched for as long as it
    can reach `_menu_display` -- constructing under the patch and then
    letting it expire before the real risk (menu navigation) happens is
    avoidance-by-design, not a guard. Use `_instantiate_fixed` instead for
    tests that never touch `_event_detect`/`_display_loop`/menu state."""
    kwargs = dict(dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=0, units="F", config={})
    kwargs.update(overrides)
    with (
        mock.patch.object(threading, "Thread") as mock_thread,
        mock.patch("os.system") as mock_os_system,
    ):
        mock_thread.return_value.start = lambda: None
        d = mod.Display(**kwargs)
        yield d, mock_os_system


def _assert_no_reboot_or_shutdown(mock_os_system):
    """Defense-in-depth proof for the `_fixed_driver_guarded` tests: even
    though none of them intentionally select a menu "Power_" item, confirm
    the neutralized `os.system` was never actually asked to reboot, shut
    down, or power off the machine running the test suite."""
    dangerous_terms = ("reboot", "shutdown", "poweroff", "power off")
    for call in mock_os_system.call_args_list:
        command = str(call.args[0]) if call.args else ""
        lowered = command.lower()
        assert not any(term in lowered for term in dangerous_terms), (
            f"os.system was called with a reboot/shutdown-shaped command: {command!r}"
        )


def _in_data_320(food_count=2):
    food = {"Probe1": 120, "Probe2": 80}
    if food_count == 0:
        food = {}
    elif food_count == 1:
        food = {"Probe1": 120}
    return {
        "probe_history": {"primary": {"Grill": 225}, "food": food},
        "primary_setpoint": 225,
        "notify_targets": {"Grill": 225, "Probe1": 165, "Probe2": 0},
    }


def _status_320(**overrides):
    base = {
        "mode": "Hold",
        "outpins": {"fan": True, "igniter": True, "auger": True},
        "notify_data": [{"req": True, "type": "probe"}, {"req": True, "type": "probe"}],
        "recipe": False,
        "recipe_paused": False,
        "s_plus": False,
        "hopper_level_enabled": True,
        "hopper_level": 55,
        "p_mode": 2,
        "lid_open_detected": False,
        "lid_open_endtime": 0,
        "start_time": time.time(),
        "start_duration": 30,
        "prime_duration": 10,
        "shutdown_duration": 20,
    }
    base.update(overrides)
    return base


def _drive_fixed_status_render(d):
    """Constructs+drives the status-render path shared by both base_fixed
    subclasses: splash, text, several _display_current variants (hitting the
    food-probe-count / mode / recipe / notify branches), network, clear. Each
    call reaches the driver's own `_display_canvas`/`_display_clear`
    override.

    Returns a dict of {stage_name: raw RGB bytes of `d.display_surface`
    captured right after that stage's draw call}, so callers can assert the
    driver actually painted something different at each stage rather than
    merely "didn't raise" -- `_display_canvas` fills white then blits the
    freshly-rendered PIL image, and `_display_clear` fills solid black, so a
    real draw is expected to change the surface's bytes every time."""
    pygame.init()
    snapshots = {}
    try:
        d.display_surface = pygame.display.set_mode(size=(d.WIDTH, d.HEIGHT), flags=pygame.SHOWN)
        snapshots["blank"] = pygame.image.tobytes(d.display_surface, "RGB")

        d._display_splash()
        snapshots["splash"] = pygame.image.tobytes(d.display_surface, "RGB")

        d.display_data = "225"
        d._display_text()
        snapshots["text"] = pygame.image.tobytes(d.display_surface, "RGB")

        d._display_current(_in_data_320(2), _status_320(mode="Hold"))
        snapshots["current_1"] = pygame.image.tobytes(d.display_surface, "RGB")
        d._display_current(_in_data_320(1), _status_320(mode="Smoke", p_mode=3, recipe=True))
        d._display_current(_in_data_320(0), _status_320(mode="Startup", recipe_paused=True))
        d._display_current(
            _in_data_320(2), _status_320(mode="Hold", lid_open_detected=True, lid_open_endtime=1e15, s_plus=True)
        )
        snapshots["current_4"] = pygame.image.tobytes(d.display_surface, "RGB")

        d._display_network("192.168.1.50")
        snapshots["network"] = pygame.image.tobytes(d.display_surface, "RGB")

        d._display_clear()
        snapshots["clear"] = pygame.image.tobytes(d.display_surface, "RGB")
    finally:
        pygame.quit()
    return snapshots


def _assert_fixed_status_render_drew_something(snapshots):
    """Concrete "it actually drew" proof for `_drive_fixed_status_render`'s
    caller tests: each named stage must differ from the previous one --
    splash overwrites the blank surface, the first _display_current call
    overwrites splash, the network QR code overwrites the last status frame,
    and clear (solid black fill) overwrites the QR code."""
    assert snapshots["splash"] != snapshots["blank"], "_display_splash did not draw anything"
    assert snapshots["current_1"] != snapshots["splash"], "_display_current did not draw anything new"
    assert snapshots["network"] != snapshots["current_4"], "_display_network (QR code) did not draw anything new"
    assert snapshots["clear"] != snapshots["network"], "_display_clear did not draw anything"


def test_pygame_240x320_constructs():
    d = _instantiate_fixed(mod_320)
    assert (d.WIDTH, d.HEIGHT) == (320, 240)
    assert d.min_transition_delay == 0.1


def test_pygame_240x320_draw_status_render_path():
    d = _instantiate_fixed(mod_320)
    snapshots = _drive_fixed_status_render(d)
    _assert_fixed_status_render_drew_something(snapshots)


def _stop_loop_after(n_events, on_event=None):
    """Build a `pygame.event.get` replacement that lets a driver's real
    `_display_loop` run for exactly `n_events` iterations (each iteration
    calls `pygame.event.get()` exactly once), calling `on_event(n)` at the
    start of the n-th iteration to steer `display_command`/`display_active`/
    etc. into the next branch, then raises to unwind out of the otherwise-
    infinite `while True` -- the same "no infinite loop may start" guard as
    patching `threading.Thread`/`multiprocessing.Process`, just applied to a
    loop that runs synchronously in the test's own thread instead of a
    background one."""
    state = {"n": 0}

    def fake_get():
        state["n"] += 1
        if state["n"] >= n_events:
            raise RuntimeError("loop-guard-stop")
        if on_event:
            on_event(state["n"])
        return []

    return fake_get


def test_pygame_240x320_display_loop_drives_each_command_branch(monkeypatch):
    # Runs the *real* `_display_loop` (not `_display_current` etc. called
    # directly) for a handful of iterations, steering `display_command`
    # through splash -> clear -> text -> network -> the final "draw current
    # data" branch -- the ~50-line body that `_drive_fixed_status_render`
    # cannot reach (that helper calls the draw methods directly, not the
    # loop that dispatches to them). `pygame.time.delay` is patched to a
    # no-op so the loop's real `splash` branch doesn't cost 3 real seconds.
    with _fixed_driver_guarded(mod_320) as (d, mock_os_system):
        monkeypatch.setattr(pygame.time, "delay", lambda ms: None)

        def on_event(n):
            if n == 2:
                d.display_command = "text"
                d.display_data = "225"
            elif n == 3:
                d.display_command = "network"
            elif n == 4:
                d.display_timeout = None
                d.display_active = True
                d.in_data = _in_data_320(1)
                d.status_data = _status_320()

        monkeypatch.setattr(pygame.event, "get", _stop_loop_after(5, on_event))
        try:
            with pytest.raises(RuntimeError, match="loop-guard-stop"):
                d._display_loop()
        finally:
            pygame.quit()
        _assert_no_reboot_or_shutdown(mock_os_system)


def test_pygame_240x320b_constructs_with_menu():
    d = _instantiate_fixed(mod_320b)
    assert d.input_enabled is True
    assert "inactive" in d.menu and "active" in d.menu
    assert d.eventLogger is not None


def test_pygame_240x320b_draw_status_render_path():
    d = _instantiate_fixed(mod_320b)
    snapshots = _drive_fixed_status_render(d)
    _assert_fixed_status_render_drew_something(snapshots)


def test_pygame_240x320b_display_loop_drives_each_command_and_menu_timeout_branch(monkeypatch):
    # Same technique as pygame_240x320's version, but this driver's loop also
    # reads real key state (unused/no-op here: nothing is ever pressed under
    # the dummy driver) and adds the `_event_detect()` call plus the
    # menu-inactivity-timeout branch (menu_active and > 5s idle -> closes the
    # menu, and clears the display if nothing else is active).
    with _fixed_driver_guarded(mod_320b) as (d, mock_os_system):
        monkeypatch.setattr(pygame.time, "delay", lambda ms: None)

        def on_event(n):
            if n == 2:
                d.display_command = "text"
                d.display_data = "225"
            elif n == 3:
                d.display_command = "network"
            elif n == 4:
                # menu idle-timeout branch, with display_active False so it
                # also re-arms display_command = "clear" for the next
                # iteration.
                d.display_timeout = None
                d.menu_active = True
                d.menu_time = time.time() - 10
                d.display_active = False
            elif n == 6:
                d.menu_active = False
                d.display_timeout = None
                d.display_active = True
                d.in_data = _in_data_320(1)
                d.status_data = _status_320()

        monkeypatch.setattr(pygame.event, "get", _stop_loop_after(7, on_event))
        try:
            with pytest.raises(RuntimeError, match="loop-guard-stop"):
                d._display_loop()
        finally:
            pygame.quit()
        _assert_no_reboot_or_shutdown(mock_os_system)


def _fake_control_pair(initial_mode=Mode.STOP):
    from common.defaults import default_control

    state = default_control()
    state["mode"] = initial_mode
    calls = []

    def read_control(flush=False):
        return dict(state)

    def write_control(control, kind=None, origin=None):
        calls.append((dict(control), kind, origin))
        state.update(control)

    return state, read_control, write_control, calls


def test_pygame_240x320b_event_detect_unknown_command_is_a_noop():
    with _fixed_driver_guarded(mod_320b) as (d, mock_os_system):
        d.input_event = "GARBAGE"
        d._event_detect()  # not UP/DOWN/ENTER -> early return; input_event is left as-is
        assert d.input_event == "GARBAGE"
        assert d.menu_active is False
        assert d.display_timeout is None
        _assert_no_reboot_or_shutdown(mock_os_system)


def test_pygame_240x320b_event_detect_opens_menu_and_renders_it(monkeypatch):
    import display.base_fixed as base_fixed_mod

    with _fixed_driver_guarded(mod_320b) as (d, mock_os_system):
        _state, read_control, write_control, _calls = _fake_control_pair(Mode.STOP)
        monkeypatch.setattr(base_fixed_mod, "read_control", read_control)
        monkeypatch.setattr(base_fixed_mod, "write_control", write_control)

        pygame.init()
        try:
            d.display_surface = pygame.display.set_mode(size=(d.WIDTH, d.HEIGHT), flags=pygame.SHOWN)
            d.input_event = "ENTER"
            d._event_detect()  # dispatches to base_fixed._menu_display("ENTER"), draws via our _display_canvas
            assert d.menu_active is True
            assert d.menu["current"]["mode"] == "inactive"
        finally:
            pygame.quit()

        # Defense-in-depth: base_fixed._menu_display's "Power_" branch calls
        # os.system(...reboot/shutdown...) UNCONDITIONALLY, no real_hardware
        # gate. This test only opens the menu (mode "none" -> "inactive"),
        # never navigates to and selects a "Power_" item, so os.system must
        # not have been touched at all -- not merely "not reboot-shaped".
        mock_os_system.assert_not_called()
        _assert_no_reboot_or_shutdown(mock_os_system)


# ---------------------------------------------------------------------------
# dsi_base.py -- display.base_flex.DisplayBase subclass ("flex" driver).
# Mirrors test_fixed_drivers_methods.py's ili9341f coverage pattern:
# construct with the multiprocessing display worker patched to a no-op, then
# drive the render/input methods directly against a real pygame offscreen
# surface rather than running the real _display_loop.
# ---------------------------------------------------------------------------

_DSI_PROBE_INFO = {
    "primary": {"name": "Grill"},
    "food": [{"label": "Probe1", "name": "Chicken"}],
}


def _dsi_status_data(**overrides):
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


def _dsi_config(**overrides):
    config = {
        "display_data_filename": "./display/dsi_800x480t.json",
        "input_types_supported": ["button", "encoder", "touch"],
        "buttonslevel": "HIGH",
        "rotation": 0,
        "probe_info": _DSI_PROBE_INFO,
    }
    config.update(overrides)
    return config


def _make_dsi(monkeypatch, **config_overrides):
    import display.base_flex as base_flex_mod

    # This dev box's /sys/class/backlight/ may or may not exist; forcing
    # is_real_hardware() False makes _init_display_device's DummyBacklight
    # fallback deterministic regardless of environment (mirrors
    # test_fixed_drivers_methods.py's ili9341f precedent).
    monkeypatch.setattr(base_flex_mod, "is_real_hardware", lambda: False)
    config = _dsi_config(**config_overrides)
    with (
        mock.patch.object(threading, "Thread") as mock_thread,
        mock.patch.object(multiprocessing, "Process") as mock_proc,
        mock.patch("os.system", side_effect=AssertionError("os.system blocked for dsi_800x480t")),
    ):
        mock_thread.return_value.start = lambda: None
        mock_proc.return_value.start = lambda: None
        d = dsi_mod.Display(
            dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=config["rotation"], units="F", config=config
        )
    return d


class _FakeFlexObject:
    """Minimal stand-in for a display.flexobject.FlexObject giving full
    control over button_list/button_selected/touch_areas/data, so
    _process_button/_process_touch's deeper branches can be driven directly
    (same technique as test_fixed_drivers_methods.py's helper of the same
    shape, for ili9341f -- dsi_800x480t's _process_button/_process_touch
    bodies are the same base_flex-era code duplicated into this module)."""

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


def test_dsi_800x480t_constructs_with_dummy_backlight(monkeypatch):
    d = _make_dsi(monkeypatch)
    assert isinstance(d.backlight, dsi_mod.DummyBacklight)
    assert d.backlight.power is True
    assert d.background is not None and d.display_canvas is not None
    assert d.display_profile == "profile_1"


def test_dsi_800x480t_rotation_90_uses_profile_2(monkeypatch):
    d = _make_dsi(monkeypatch, rotation=90)
    assert d.display_profile == "profile_2"


def test_dsi_800x480t_wake_sleep_toggle_backlight(monkeypatch):
    d = _make_dsi(monkeypatch)
    with mock.patch.object(pygame.time, "delay"):  # real delay(1000) would cost 1s wall-clock
        d._sleep_display()
    assert d.backlight.power is False
    d._wake_display()
    assert d.backlight.power is True and d.backlight.brightness == 100


def test_dsi_800x480t_canvas_render_path_against_real_surface(monkeypatch):
    d = _make_dsi(monkeypatch)
    pygame.init()
    try:
        d.display_surface = pygame.display.set_mode(size=(d.WIDTH, d.HEIGHT), flags=pygame.SHOWN)
        blank = pygame.image.tobytes(d.display_surface, "RGB")

        with mock.patch.object(pygame.time, "delay"):
            d._display_splash()  # base_flex generic splash -> our no-arg _display_canvas()
            after_splash = pygame.image.tobytes(d.display_surface, "RGB")
            d._display_clear()  # _sleep_display + fill + update + canvas reset
            after_clear = pygame.image.tobytes(d.display_surface, "RGB")

        d._display_background()
        d._capture_background()
        assert d.menu_background is not None
        d._display_menu_background()
        d._display_canvas()
        after_menu_background = pygame.image.tobytes(d.display_surface, "RGB")

        # Each step must have actually repainted the real surface, not just
        # avoided raising: splash blits the splash art, clear does a solid
        # black fill, and the background/menu-background/canvas sequence
        # blits the (blurred) background image -- three distinct real draws.
        assert after_splash != blank, "_display_splash did not draw anything onto the real surface"
        assert after_clear != after_splash, "_display_clear did not repaint the surface"
        assert after_menu_background != after_clear, "_display_menu_background/_display_canvas did not draw anything"
    finally:
        pygame.quit()


def test_dsi_800x480t_canvas_to_surface_rotates_for_non_zero_rotation(monkeypatch):
    d = _make_dsi(monkeypatch, rotation=180)
    pygame.init()
    try:
        d.display_surface = pygame.display.set_mode(size=(d.WIDTH, d.HEIGHT), flags=pygame.SHOWN)

        # Paint an asymmetric marker into the top-left corner of the source
        # canvas. A 180-degree rotation leaves DIMENSIONS unchanged (unlike
        # 90/270, already covered by the profile_2 test above), so the only
        # way to prove the rotate(expand=True) branch actually ran -- rather
        # than falling through to the `else: canvas.copy()` branch -- is to
        # check ORIENTATION: the marker must have moved to the bottom-right.
        marker_size = 20
        ImageDraw.Draw(d.display_canvas).rectangle([0, 0, marker_size - 1, marker_size - 1], fill=(255, 0, 0, 255))

        d._canvas_to_surface()  # self.ROTATION == 180 -> rotate(expand=True) branch

        assert d.transform_canvas.size == d.display_canvas.size  # 180 degrees: dimensions unchanged
        top_left = d.transform_canvas.getpixel((0, 0))
        bottom_right = d.transform_canvas.getpixel((d.WIDTH - 1, d.HEIGHT - 1))
        assert top_left != (255, 0, 0, 255), "marker should have rotated away from the top-left corner"
        assert bottom_right == (255, 0, 0, 255), "180-degree rotation should move the marker to the bottom-right"
    finally:
        pygame.quit()


def test_dsi_800x480t_event_detect_and_menu_touch_branches(monkeypatch):
    d = _make_dsi(monkeypatch)
    pygame.init()
    try:
        d.display_surface = pygame.display.set_mode(size=(d.WIDTH, d.HEIGHT), flags=pygame.SHOWN)

        d.status_data = _dsi_status_data()
        d.last_status_data = {"lid_open_detected": False}
        d.in_data = None
        d.last_in_data = {}
        d.display_active = "dash"
        d._init_dash()

        monkeypatch.setattr(d, "_command_handler", lambda: None)

        with mock.patch.object(pygame.time, "delay"):  # _debounce() inside every _process_button call
            # _event_detect dispatch: unknown command, then ENTER (button).
            d.input_event = "GARBAGE"
            d._event_detect()
            assert d.input_event is None

            d.input_event = "ENTER"
            d._event_detect()  # dash + ENTER, Stop mode -> _process_button -> menu_main
            assert d.display_active == "menu_main"
            assert d.input_event is None  # cleared after dispatch

            # Every other dash->menu mode branch (Stop was just exercised above).
            for mode, expected_active in [
                (Mode.STARTUP, "menu_main_active_normal"),
                (Mode.MONITOR, "menu_main_active_monitor"),
                (Mode.RECIPE, "menu_main_active_recipe"),
                (Mode.MANUAL, "menu_main"),  # falls through to the else branch
            ]:
                d.display_active = "dash"
                d.status_data = _dsi_status_data(mode=mode)
                d._process_button()
                assert d.display_active == expected_active
            d.status_data = _dsi_status_data()  # restore Stop for what follows

            # Deep _process_button branches via a fully-controlled fake menu object.
            obj = _FakeFlexObject(
                ["menu_close", "menu_prime", "menu_startup", "cmd_monitor", "menu_system"], button_selected=1
            )
            d.display_active = "menu_main"
            d.display_object_list = [obj]
            d.input_event = "UP"  # button_selected <= 1 -> wraps to len(button_list) - 1
            d._process_button()
            assert obj.get_object_data()["data"]["button_selected"] == 4
            d.input_event = "DOWN"
            d._process_button()  # len-1(4) not > 4 -> resets to 1
            assert obj.get_object_data()["data"]["button_selected"] == 1
            d.input_event = "DOWN"
            d._process_button()  # len-1(4) > 1 -> increments
            assert obj.get_object_data()["data"]["button_selected"] == 2
            d.input_event = "DOWN"
            d._process_button()
            assert obj.get_object_data()["data"]["button_selected"] == 3
            d.input_event = "ENTER"
            d._process_button()  # selects "cmd_monitor" -> self.command + _command_handler()
            assert d.command == "cmd_monitor"

            # ENTER on a cmd_ button that DOES carry button_value.
            obj_bv = _FakeFlexObject(["cmd_a", "cmd_b"], button_selected=1, button_value=["va", "vb"])
            d.display_active = "menu_main"
            d.display_object_list = [obj_bv]
            d.input_event = "ENTER"
            d._process_button()
            assert d.command == "cmd_b" and d.command_data == "vb"

            # menu_close closes back to dash.
            obj2 = _FakeFlexObject(["menu_close", "menu_prime"], button_selected=0)
            d.display_active = "menu_main"
            d.display_object_list = [obj2]
            d.input_event = "ENTER"
            d._process_button()
            assert d.display_active == "dash"

            # Nested menu_ selection from a non-dash menu.
            obj3 = _FakeFlexObject(["menu_close", "menu_system"], button_selected=1)
            d.display_active = "menu_main"
            d.display_object_list = [obj3]
            d.input_event = "ENTER"
            d._process_button()
            assert d.display_active == "menu_system"

            # button_selected is None -> ENTER closes back to dash.
            obj4 = _FakeFlexObject([], button_selected=None)
            d.display_active = "menu_system"
            d.display_object_list = [obj4]
            d.input_event = "ENTER"
            d._process_button()
            assert d.display_active == "dash"

            # "input_*" display_active branch: UP/DOWN edit, ENTER commits + closes.
            obj5 = _FakeFlexObject([], button_selected=None, extra_data={"input": ""}, command="cmd_hold")
            d.display_active = "input_hold"
            d.display_object_list = [obj5]
            d.input_event = "UP"
            d._process_button()
            assert obj5.get_object_data()["data"]["input"] == "up"
            d.input_event = "DOWN"
            d._process_button()
            assert obj5.get_object_data()["data"]["input"] == "down"
            d.input_event = "ENTER"
            d._process_button()
            assert d.command == "cmd_hold" and d.display_active == "dash"

            # _process_touch: cmd_ dispatch, menu_close, nested menu_, then the
            # "display currently inactive" wake branch.
            rect = pygame.Rect(0, 0, 50, 50)
            obj6 = _FakeFlexObject(["cmd_monitor"], touch_areas=[rect])
            d.display_active = "menu_main"
            d.display_object_list = [obj6]
            d.touch_pos = (10, 10)
            d._process_touch()
            assert d.command == "cmd_monitor"

            obj6b = _FakeFlexObject(["cmd_x"], touch_areas=[pygame.Rect(0, 0, 50, 50)], button_value=["vx"])
            d.display_active = "menu_main"
            d.display_object_list = [obj6b]
            d.touch_pos = (10, 10)
            d._process_touch()
            assert d.command == "cmd_x" and d.command_data == "vx"

            obj7 = _FakeFlexObject(["menu_close"], touch_areas=[pygame.Rect(0, 0, 50, 50)])
            d.display_active = "menu_main"
            d.display_object_list = [obj7]
            d.touch_pos = (5, 5)
            d._process_touch()
            assert d.display_active == "dash"

            obj8 = _FakeFlexObject(["menu_system"], touch_areas=[pygame.Rect(0, 0, 50, 50)])
            d.display_active = "menu_main"
            d.display_object_list = [obj8]
            d.touch_pos = (5, 5)
            d._process_touch()
            assert d.display_active == "menu_system"

            d.display_active = None
            d.touch_pos = (0, 0)
            d._process_touch()  # inactive -> _wake_display() + go to home/dash
            assert d.display_active in ("home", "dash")

            # dsi_base-specific: touch-coordinate rotation transforms
            # (90/180/270), not present in ili9341f's copy.
            d.display_object_list = []
            for rotation in (90, 180, 270):
                d.ROTATION = rotation
                d.display_active = "menu_main"
                d.touch_pos = (10, 20)
                d._process_touch()
    finally:
        pygame.quit()


def test_dsi_320x240t_layout_constructs_and_renders(monkeypatch):
    """display/dsi_320x240t.json is the compact 320x240 layout re-homed onto
    this same resolution-agnostic engine from a since-removed sibling
    module: max_food_probes=2, a 54-widget layout genuinely distinct from
    the 800x480 JSON exercised above (gauge_compact food-probe widgets,
    tighter menu/input geometry). This characterizes that the engine loads
    and renders that layout correctly; the deep input/menu branch coverage
    above already exercises the shared dispatch code independent of which
    JSON layout is loaded, so it is not repeated here."""
    d = _make_dsi(monkeypatch, display_data_filename="./display/dsi_320x240t.json")
    assert (d.WIDTH, d.HEIGHT) == (320, 240)
    assert isinstance(d.backlight, dsi_mod.DummyBacklight)
    assert d.backlight.power is True
    assert d.background is not None and d.display_canvas is not None
    assert d.display_profile == "profile_1"

    pygame.init()
    try:
        d.display_surface = pygame.display.set_mode(size=(d.WIDTH, d.HEIGHT), flags=pygame.SHOWN)
        blank = pygame.image.tobytes(d.display_surface, "RGB")
        with mock.patch.object(pygame.time, "delay"):
            d._display_splash()  # base_flex generic splash -> our no-arg _display_canvas()
        after_splash = pygame.image.tobytes(d.display_surface, "RGB")
        assert after_splash != blank, "_display_splash did not draw anything onto the real surface"
    finally:
        pygame.quit()


# ---------------------------------------------------------------------------
# qtapp.py -- Qt Quick display-process host. run_app() itself is NOT called
# (it blocks forever in QGuiApplication.exec()); every other entry point is
# driven directly under QT_QPA_PLATFORM=offscreen.
# ---------------------------------------------------------------------------


def _qapp():
    return QGuiApplication.instance() or QGuiApplication([])


def _stub_backend():
    return PiFireBackend(
        lambda: ({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Stop", "units": "F", "outpins": {}}),
        lambda command, data: None,
        {"primary": {"name": "Grill", "max_temp": 600}, "food": [], "aux": []},
    )


def test_qtapp_fetch_reads_current_and_status(monkeypatch):
    monkeypatch.setattr(
        qtapp_mod, "read_current", lambda: {"P": {"Grill": 200}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}
    )
    monkeypatch.setattr(qtapp_mod, "read_status", lambda: {"mode": "Hold", "units": "F", "outpins": {}})
    in_data, status = qtapp_mod._fetch()
    assert in_data["P"]["Grill"] == 200
    assert status["mode"] == "Hold"


def test_qtapp_build_engine_loads_main_qml():
    _qapp()
    backend = _stub_backend()
    config = {"display_data_filename": "./display/qtquick_dsi_1280x720t.json"}
    engine = qtapp_mod.build_engine(config, backend)
    assert engine.rootObjects(), "Main.qml failed to load"


def test_qtapp_build_engine_invalid_rotation_clamps_to_zero():
    _qapp()
    backend = _stub_backend()
    config = {"display_data_filename": "./display/qtquick_dsi_1280x720t.json", "rotation": 45}
    engine = qtapp_mod.build_engine(config, backend)
    assert engine.rootObjects()


def test_qtapp_build_backend_wires_accent_and_ip():
    _qapp()
    config = {
        "display_data_filename": "./display/qtquick_dsi_1280x720t.json",
        "probe_info": {"primary": {"name": "Grill", "max_temp": 600}, "food": [], "aux": []},
        "units": "F",
        "accent_theme": "Ice",
        "ip_address": "10.0.0.5",
    }
    backend = qtapp_mod.build_backend(config)
    assert backend._accent_theme == "Ice"
    assert backend._ip_address == "10.0.0.5"


def test_qtapp_build_backend_defaults_ip_when_not_configured():
    _qapp()
    config = {
        "display_data_filename": "./display/qtquick_dsi_1280x720t.json",
        "probe_info": {"primary": {"name": "Grill"}, "food": [], "aux": []},
    }
    backend = qtapp_mod.build_backend(config)
    assert backend._accent_theme == "Ember"  # config default
    assert backend._ip_address == backend.ipAddress  # falls back to the backend's own detection


def test_qtapp_dummy_backlight_defaults():
    bl = qtapp_mod.DummyBacklight()
    assert bl.brightness == 100
    assert bl.power is True
    assert bl.fade_duration == 1


def test_qtapp_make_backlight_returns_dummy_when_not_real_hardware(monkeypatch):
    monkeypatch.setattr("common.system.is_real_hardware", lambda: False)
    bl = qtapp_mod._make_backlight()
    assert isinstance(bl, qtapp_mod.DummyBacklight)


def test_qtapp_make_backlight_falls_back_to_dummy_when_rpi_backlight_missing(monkeypatch):
    # rpi_backlight is not installed in this dev/CI environment: even when
    # is_real_hardware() and the sysfs path both say "yes", the `import
    # rpi_backlight` inside _make_backlight raises and it falls back to
    # DummyBacklight (matches dsi_800x480t's own fallback behavior).
    monkeypatch.setattr("common.system.is_real_hardware", lambda: True)
    monkeypatch.setattr(Path, "exists", lambda self: True)
    bl = qtapp_mod._make_backlight()
    assert isinstance(bl, qtapp_mod.DummyBacklight)


class _FakeSignal:
    def __init__(self):
        self._cbs = []

    def connect(self, cb):
        self._cbs.append(cb)

    def emit(self):
        for cb in self._cbs:
            cb()


class _FakeBackend:
    def __init__(self):
        self.asleep = False
        self.asleepChanged = _FakeSignal()


class _FakeController:
    def __init__(self):
        self.calls = []

    def set_output_power(self, on):
        self.calls.append(on)


def test_qtapp_bind_backend_power_applies_once_and_toggles():
    b, c = _FakeBackend(), _FakeController()
    qtapp_mod.bind_backend_power(b, c)
    assert c.calls == [True]  # not asleep -> power on, applied immediately
    b.asleep = True
    b.asleepChanged.emit()
    b.asleep = False
    b.asleepChanged.emit()
    assert c.calls == [True, False, True]
