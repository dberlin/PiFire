"""Task 1 (Phase C): characterize the rotary-encoder and button INPUT
behavior of the fixed-base display drivers before it is extracted into
shared mixins.

No test exercises `_inc_callback`/`_dec_callback`/`_click_callback`/
`_up_callback`/`_down_callback`/`_enter_callback`/`_event_detect` today --
Phase B's `test_fixed_base_drivers_load.py` only proves the drivers
construct; it never drives a single callback. This file is the
characterization net: it pins CURRENT behavior (quirks included, not
"fixed") for the four input shapes found across the 16 drivers:

  * Group A: debounced rotary encoder (6 of the 7 `*e`/`*em` drivers),
    represented here by `ili9341e`.
  * Group A with a divergence: `st7789e`'s `_event_detect` additionally
    nulls `in_data`/`status_data` and sets `monitor_display = False` --
    a 3-line difference from the other 6 encoder drivers that the Phase C
    mixin extraction must preserve, not silently unify away.
  * Group B: trivial rotary encoder (no debounce state at all), represented
    here by `st7789_240x320e`.
  * Button input (`gpiozero.Button`), represented here by `ili9341b`,
    whose `_event_detect` also differs from the encoder variants (no
    `input_counter == 0` early-return gate).

Construction hazards, hardware stubs, and the `threading.Thread`/
`os.system` neutralization mirror `test_fixed_base_drivers_load.py`
exactly (same ordering hazard around pre-warming `display.base_fixed`
before installing the hardware-stub overlay; same real-reboot-incident
history behind blocking `os.system`). See that file's module docstring
for the full rationale. This file adds one deviation: the `gpiozero.Button`
stub returns a *fresh* MagicMock per call (`side_effect`) rather than a
single shared `MagicMock.return_value`, because `ili9341b._init_input`
constructs three independent buttons (`up_button`/`down_button`/
`enter_button`) and assigns different callbacks to each one's
`when_pressed`/`when_held`; a shared mock instance would make the last
assignment clobber the earlier ones and silently break the test's ability
to recover which callback is wired to which button.
"""

import importlib
import sys
import threading
import types
from unittest import mock

import pytest

import display.base_fixed  # noqa: F401  pre-warm real PIL/qrcode/common imports; see test_fixed_base_drivers_load.py

FULL_DEV_PINS = {
    "display": {"dc": 24, "led": 5, "rst": 25},
    "input": {"up_clk": 16, "down_dt": 20, "enter_sw": 21},
}


def _hardware_stubs(*, luma=False, gpiozero=False, pyky040=False, st7789_pimoroni=False):
    """Same stub overlay as test_fixed_base_drivers_load.py's
    `_hardware_stubs`, trimmed to the three libraries this file's drivers
    need (luma, gpiozero, pyky040) -- st7789_pimoroni/spidev aren't
    exercised by any of the four representative drivers here.

    Deviation from that file: `gpiozero.Button` uses `side_effect` to hand
    back a distinct MagicMock per call instead of a single shared
    `.return_value`, so ili9341b's three independently-constructed Button
    objects don't alias each other (see module docstring)."""
    overlay = {}
    if luma:
        overlay["luma"] = types.ModuleType("luma")
        overlay["luma.core"] = types.ModuleType("luma.core")
        overlay["luma.core.interface"] = types.ModuleType("luma.core.interface")
        serial_mod = types.ModuleType("luma.core.interface.serial")
        serial_mod.spi = mock.MagicMock(name="spi")
        overlay["luma.core.interface.serial"] = serial_mod
        overlay["luma.lcd"] = types.ModuleType("luma.lcd")
        device_mod = types.ModuleType("luma.lcd.device")
        device_mod.ili9341 = mock.MagicMock(name="ili9341_device")
        device_mod.ili9488 = mock.MagicMock(name="ili9488_device")
        device_mod.st7789 = mock.MagicMock(name="st7789_device")
        overlay["luma.lcd.device"] = device_mod
    if st7789_pimoroni:
        st7789_mod = types.ModuleType("ST7789")
        st7789_cls = mock.MagicMock(name="ST7789_class")
        st7789_cls.return_value.width = 320
        st7789_cls.return_value.height = 240
        st7789_mod.ST7789 = st7789_cls
        overlay["ST7789"] = st7789_mod
    if gpiozero:
        gz_mod = types.ModuleType("gpiozero")
        gz_mod.Button = mock.MagicMock(
            name="Button",
            side_effect=lambda *a, **kw: mock.MagicMock(name=f"Button(pin={kw.get('pin')})"),
        )
        overlay["gpiozero"] = gz_mod
    if pyky040:
        inner = types.ModuleType("pyky040.pyky040")
        inner.Encoder = mock.MagicMock(name="Encoder")
        outer = types.ModuleType("pyky040")
        outer.pyky040 = inner
        overlay["pyky040"] = outer
        overlay["pyky040.pyky040"] = inner
    return overlay


def _load_driver(module_path, **stub_kwargs):
    overlay = _hardware_stubs(**stub_kwargs)
    with mock.patch.dict(sys.modules, overlay):
        return importlib.import_module(module_path)


def _instantiate(mod, **overrides):
    """Construct mod.Display with the display/encoder thread(s) and
    os.system blocked -- identical safety net to
    test_fixed_base_drivers_load.py's `_instantiate`. Patches the shared
    `threading` module's `Thread` attribute directly (not `mod.threading`):
    every module's `import threading` binds the same singleton object, and
    some drivers here (e.g. ili9341e, ili9341b) no longer keep their own
    `import threading` around now that thread-starting lives in the shared
    mixins (`display._encoder_input`, `display._luma_panel`)."""
    kwargs = dict(dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=0, units="F", config={})
    kwargs.update(overrides)
    with (
        mock.patch.object(threading, "Thread") as mock_thread,
        mock.patch("os.system", side_effect=AssertionError(f"os.system blocked for {mod.__name__}")),
    ):
        mock_thread.return_value.start = lambda: None
        return mod.Display(**kwargs)


# ---------------------------------------------------------------------------
# Group A: debounced rotary encoder (ili9341e representative)
# ---------------------------------------------------------------------------


@pytest.fixture
def group_a_driver():
    mod = _load_driver("display.ili9341e", luma=True, pyky040=True)
    return _instantiate(mod)


def test_group_a_inc_callback_sets_up_event_and_bumps_counter(group_a_driver):
    d = group_a_driver
    assert d.input_event is None
    assert d.input_counter == 0

    d._inc_callback(1)

    assert d.input_event == "UP"
    assert d.input_counter == 1
    assert d.last_direction == "UP"


def test_group_a_dec_callback_sets_down_event_and_bumps_counter(group_a_driver):
    d = group_a_driver

    d._dec_callback(1)

    assert d.input_event == "DOWN"
    assert d.input_counter == 1
    assert d.last_direction == "DOWN"


def test_group_a_repeated_same_direction_keeps_bumping_counter(group_a_driver):
    # last_direction == "UP" satisfies _inc_callback's own re-entry guard
    # (`self.last_direction is None or self.last_direction == "UP" or ...`),
    # so consecutive same-direction calls keep incrementing input_counter --
    # pin this as current behavior (not a "one event per debounce window"
    # design, despite the debounce-looking bookkeeping).
    d = group_a_driver

    d._inc_callback(1)
    d._inc_callback(1)
    d._inc_callback(1)

    assert d.input_event == "UP"
    assert d.input_counter == 3


def test_group_a_click_callback_sets_enter_event_and_flag(group_a_driver):
    d = group_a_driver

    d._click_callback()

    assert d.input_event == "ENTER"
    assert d.enter_received is True
    # _click_callback does not touch input_counter.
    assert d.input_counter == 0


def test_group_a_enter_received_cancels_a_pending_up(group_a_driver):
    """Pin the quirk described in the Phase C brief: once enter_received is
    True, a subsequent _inc_callback does NOT set input_event/input_counter
    (the `if not self.enter_received:` guard suppresses it) -- and the
    callback consumes/clears enter_received on its way out, because the
    `time.time() - self.last_movement_time < 0.3` branch reads as
    near-always-true immediately after `self.last_movement_time` is just
    set to `current_time` a few lines above (near-zero elapsed wall time).
    This is characterized verbatim, not "fixed": the < 0.3 check does not
    meaningfully gate anything at this call site."""
    d = group_a_driver
    d._click_callback()
    assert d.enter_received is True

    d._inc_callback(1)

    # The UP event was suppressed by the pending ENTER ...
    assert d.input_event == "ENTER"
    assert d.input_counter == 0
    # ... and enter_received was cleared by the near-always-true < 0.3 branch.
    assert d.enter_received is False


def test_group_a_enter_received_cancels_a_pending_down(group_a_driver):
    d = group_a_driver
    d._click_callback()
    assert d.enter_received is True

    d._dec_callback(1)

    assert d.input_event == "ENTER"
    assert d.input_counter == 0
    assert d.enter_received is False


def test_group_a_direction_reversal_within_debounce_window_is_a_no_op(group_a_driver):
    """Pin a second debounce quirk distinct from the enter-cancels-pending
    path: _dec_callback's own guard is
    `self.last_direction is None or self.last_direction == "DOWN" or
    current_time - self.last_movement_time > 0.5`. Immediately after an
    _inc_callback, last_direction == "UP" (fails the first two clauses)
    and under 0.5s has elapsed (fails the third) -- so the ENTIRE guard is
    False and _dec_callback's body never runs at all: input_event,
    input_counter, last_direction and last_movement_time are all left
    completely untouched. A same-burst direction reversal is silently
    swallowed, not just "debounced" -- characterized verbatim."""
    d = group_a_driver

    d._inc_callback(1)
    assert d.input_event == "UP"
    assert d.input_counter == 1

    d._dec_callback(1)

    assert d.input_event == "UP"  # unchanged -- dec_callback's body never ran
    assert d.input_counter == 1  # unchanged
    assert d.last_direction == "UP"  # unchanged


def test_group_a_event_detect_invokes_menu_display_and_resets_counter(group_a_driver):
    d = group_a_driver
    d.input_event = "UP"
    d.input_counter = 1
    with mock.patch.object(d, "_menu_display") as mock_menu_display:
        d._event_detect()

    mock_menu_display.assert_called_once_with("UP")
    assert d.input_counter == 0
    assert d.input_event is None
    assert d.menu_active is True


def test_group_a_event_detect_ignores_up_down_with_zero_counter(group_a_driver):
    # Group A's _event_detect early-returns for UP/DOWN when input_counter
    # is 0 (it does not for ENTER) -- pin that ENTER is exempt from the
    # counter gate that UP/DOWN are subject to.
    d = group_a_driver
    d.input_event = "UP"
    d.input_counter = 0
    with mock.patch.object(d, "_menu_display") as mock_menu_display:
        d._event_detect()

    mock_menu_display.assert_not_called()
    # input_event is left untouched by the early return.
    assert d.input_event == "UP"


def test_group_a_event_detect_enter_bypasses_the_counter_gate(group_a_driver):
    d = group_a_driver
    d.input_event = "ENTER"
    d.input_counter = 0
    with mock.patch.object(d, "_menu_display") as mock_menu_display:
        d._event_detect()

    mock_menu_display.assert_called_once_with("ENTER")
    assert d.input_counter == 0
    assert d.input_event is None


def test_group_a_setup_wires_callbacks_via_encoder_setup(group_a_driver):
    # Confirm _init_input actually wires _inc_callback/_dec_callback/
    # _click_callback into pyky040.Encoder.setup(...), and that invoking
    # the *recorded* callback reference behaves identically to calling the
    # bound method directly -- proving the wiring, not just the method body.
    d = group_a_driver
    setup_kwargs = d.encoder.setup.call_args.kwargs

    setup_kwargs["inc_callback"](1)
    assert d.input_event == "UP"
    assert d.input_counter == 1

    # Reset the debounce bookkeeping directly before exercising dec_callback:
    # calling dec_callback back-to-back with inc_callback (last_direction ==
    # "UP", < 0.5s elapsed) hits the OUTER guard's "wrong direction and too
    # recent" case and is a no-op entirely (see
    # test_group_a_direction_reversal_within_debounce_window_is_a_no_op) --
    # this test is about proving the *wiring*, not re-testing that quirk.
    d.last_direction = None
    d.last_movement_time = 0

    setup_kwargs["dec_callback"](1)
    assert d.input_event == "DOWN"
    assert d.input_counter == 2

    setup_kwargs["sw_callback"]()
    assert d.input_event == "ENTER"
    assert d.enter_received is True


# ---------------------------------------------------------------------------
# Group B: trivial rotary encoder (st7789_240x320e representative)
# ---------------------------------------------------------------------------


@pytest.fixture
def group_b_driver():
    mod = _load_driver("display.st7789_240x320e", st7789_pimoroni=True, pyky040=True)
    return _instantiate(mod)


def test_group_b_inc_callback_has_no_debounce_state(group_b_driver):
    # Group B's _inc_callback is exactly two lines: set input_event, bump
    # input_counter. No last_direction/last_movement_time/enter_received
    # bookkeeping exists at all -- this is the behavioral gap that
    # justifies splitting Group A and Group B into separate mixins rather
    # than one "encoder mixin with optional debounce".
    d = group_b_driver
    assert not hasattr(d, "last_direction")
    assert not hasattr(d, "enter_received")

    d._inc_callback(1)

    assert d.input_event == "UP"
    assert d.input_counter == 1


def test_group_b_dec_callback_has_no_debounce_state(group_b_driver):
    d = group_b_driver

    d._dec_callback(1)

    assert d.input_event == "DOWN"
    assert d.input_counter == 1


def test_group_b_click_callback_sets_enter_with_no_flag(group_b_driver):
    # Unlike Group A, Group B's _click_callback does not set an
    # enter_received flag (there is nothing to cancel).
    d = group_b_driver

    d._click_callback()

    assert d.input_event == "ENTER"
    assert not hasattr(d, "enter_received")


def test_group_b_repeated_calls_keep_bumping_counter_unconditionally(group_b_driver):
    # No re-entry guard at all (unlike Group A's last_direction check):
    # every call bumps the counter regardless of direction history.
    d = group_b_driver

    d._inc_callback(1)
    d._dec_callback(1)
    d._inc_callback(1)

    assert d.input_event == "UP"
    assert d.input_counter == 3


def test_group_b_event_detect_invokes_menu_display_and_resets_counter(group_b_driver):
    # Group B's _event_detect body is identical to Group A's (same
    # counter-gate-except-ENTER shape) -- only the callbacks that feed it
    # differ. Confirm it still behaves the same way through this driver.
    d = group_b_driver
    d.input_event = "DOWN"
    d.input_counter = 2
    with mock.patch.object(d, "_menu_display") as mock_menu_display:
        d._event_detect()

    mock_menu_display.assert_called_once_with("DOWN")
    assert d.input_counter == 0
    assert d.input_event is None


# ---------------------------------------------------------------------------
# Button input (ili9341b representative)
# ---------------------------------------------------------------------------


@pytest.fixture
def button_driver():
    mod = _load_driver("display.ili9341b", luma=True, gpiozero=True)
    return _instantiate(mod)


def test_button_up_down_enter_callbacks_just_set_input_event(button_driver):
    # Each callback is a one-liner: set input_event, no counter bump, no
    # debounce state at all (a third, distinct shape from both encoder
    # groups).
    d = button_driver

    d._up_callback()
    assert d.input_event == "UP"
    assert d.input_counter == 0

    d._down_callback()
    assert d.input_event == "DOWN"
    assert d.input_counter == 0

    d._enter_callback()
    assert d.input_event == "ENTER"
    assert d.input_counter == 0


def test_button_up_callback_accepts_held_flag_from_when_held(button_driver):
    # _up_callback/_down_callback take an optional `held` positional arg
    # because they're wired to both when_pressed (no arg) and when_held
    # (gpiozero passes the Button instance as `held` in real hardware, but
    # nothing in the callback body reads it) -- pin that calling with a
    # truthy arg does not change behavior.
    d = button_driver

    d._up_callback(held=True)

    assert d.input_event == "UP"


def test_button_init_input_wires_distinct_buttons_to_distinct_callbacks(button_driver):
    # Confirm _init_input actually assigns up_button/down_button/
    # enter_button to three DISTINCT Button objects (this test's Button
    # stub deliberately avoids the shared-MagicMock-instance trap so this
    # is a meaningful assertion, not a tautology) and wires when_pressed/
    # when_held correctly per button.
    d = button_driver

    assert d.up_button is not d.down_button
    assert d.down_button is not d.enter_button
    assert d.up_button is not d.enter_button

    assert d.up_button.when_pressed == d._up_callback
    assert d.up_button.when_held == d._up_callback
    assert d.down_button.when_pressed == d._down_callback
    assert d.down_button.when_held == d._down_callback
    assert d.enter_button.when_pressed == d._enter_callback
    # enter_button has no when_held wiring (no hold-to-repeat for ENTER):
    # _init_input never assigns it, so it's still the stub's untouched
    # auto-attribute, not one of the three driver callbacks.
    assert d.enter_button.when_held not in (d._up_callback, d._down_callback, d._enter_callback)


def test_button_event_detect_ignores_the_input_counter_entirely(button_driver):
    # Divergence from BOTH encoder groups: the button driver's
    # _event_detect has no `input_counter == 0` early-return branch at all
    # -- it fires _menu_display for any of UP/DOWN/ENTER regardless of
    # input_counter (which button callbacks never even increment). Pin
    # this as a third distinct _event_detect shape.
    d = button_driver
    d.input_event = "UP"
    assert d.input_counter == 0  # never touched by any button callback
    with mock.patch.object(d, "_menu_display") as mock_menu_display:
        d._event_detect()

    mock_menu_display.assert_called_once_with("UP")
    assert d.input_event is None
    assert d.menu_active is True
    # _event_detect resets input_counter to 0 unconditionally on the
    # success path, same as the encoder variants (even though nothing
    # populated it).
    assert d.input_counter == 0


def test_button_event_detect_ignores_unknown_command(button_driver):
    d = button_driver
    d.input_event = "SOMETHING_ELSE"
    with mock.patch.object(d, "_menu_display") as mock_menu_display:
        d._event_detect()

    mock_menu_display.assert_not_called()


# ---------------------------------------------------------------------------
# st7789e's _event_detect divergence (Group A encoder + extra teardown)
# ---------------------------------------------------------------------------


@pytest.fixture
def st7789e_driver():
    mod = _load_driver("display.st7789e", luma=True, pyky040=True)
    return _instantiate(mod)


def test_st7789e_is_otherwise_a_group_a_debounced_encoder(st7789e_driver):
    # Confirm st7789e's callbacks are byte-for-byte the same Group A
    # debounce shape as ili9341e (last_direction/last_movement_time/
    # enter_received all present) before pinning its _event_detect
    # divergence below.
    d = st7789e_driver

    d._inc_callback(1)

    assert d.input_event == "UP"
    assert d.input_counter == 1
    assert d.last_direction == "UP"
    assert hasattr(d, "enter_received")


def test_st7789e_event_detect_also_nulls_in_data_and_status_data(st7789e_driver):
    # The 3-line divergence from the other 6 encoder drivers (ili9341e,
    # ili9341em, ili9488e, ili9488em, st7789_240x320e/st7789v_240x320e's
    # shared shape): st7789e's _event_detect additionally sets in_data and
    # status_data to None and monitor_display to False. Task 2's mixin
    # extraction must preserve this as a driver-specific override, not
    # silently drop it during unification.
    d = st7789e_driver
    d.in_data = {"probe": 225}
    d.status_data = {"mode": "Startup"}
    d.monitor_display = True
    d.input_event = "ENTER"
    d.input_counter = 0  # ENTER bypasses the counter gate, same as Group A

    with mock.patch.object(d, "_menu_display") as mock_menu_display:
        d._event_detect()

    mock_menu_display.assert_called_once_with("ENTER")
    assert d.in_data is None
    assert d.status_data is None
    assert d.monitor_display is False
    assert d.input_counter == 0
    assert d.input_event is None


def test_st7789e_event_detect_leaves_in_data_alone_when_no_event(st7789e_driver):
    # Sanity check on the divergence test above: with no pending
    # input_event, _event_detect must not touch in_data/status_data/
    # monitor_display at all (the whole block sits behind `if command:`).
    d = st7789e_driver
    d.in_data = {"probe": 225}
    d.status_data = {"mode": "Startup"}
    d.monitor_display = True
    d.input_event = None

    with mock.patch.object(d, "_menu_display") as mock_menu_display:
        d._event_detect()

    mock_menu_display.assert_not_called()
    assert d.in_data == {"probe": 225}
    assert d.status_data == {"mode": "Startup"}
    assert d.monitor_display is True
