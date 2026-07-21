"""Shared "menu walk" driver for the dash -> menu_main -> deep-menu/input ->
touch state-machine coverage duplicated (near-identically) between
test_fixed_drivers_methods.py's ili9341f test
(test_ili9341f_event_detect_and_menu_touch_branches, via its
_drive_flex_input_and_menu_coverage helper) and test_pygame_qt_drivers.py's
_base_dsi test (test_dsi_800x480t_event_detect_and_menu_touch_branches).

Both walks drive the exact same production code path
(display._base_flex.DisplayBase's _event_detect/_process_button/
_process_touch, inherited unchanged by ili9341f and duplicated into
display/_base_dsi.py) against a live, already-constructed driver instance
(`d`) and a scripted sequence of _FakeFlexObject-backed menu/input states.
They differ only in:

  * the event name that drives the initial dash -> menu_main transition
    (ili9341f is a button/encoder device: "UP"; the pygame/_base_dsi family
    enters via "ENTER"),
  * the status-data factory used to build each Mode -> menu_main_* branch
    (`_flex_status_data` vs `_dsi_status_data` -- same shape, different
    module), and
  * the pygame family additionally needs `pygame.time.delay` patched for the
    duration, since `_debounce()` (called from every `_process_button`)
    invokes it -- the caller is expected to wrap its `run_menu_walk(...)`
    call in `with mock.patch.object(pygame.time, "delay"):` itself; this
    module does not do that patching, so it stays a no-op/cheap import for
    the non-pygame (ili9341f) family.

`build_dash_menu_input_touch_steps` returns the shared step table; the
_base_dsi test appends its own family-specific tail (touch-coordinate
rotation transforms) after calling this, since ili9341f has no equivalent.
"""

from dataclasses import dataclass, field
from typing import Callable

import pygame

from common.modes import Mode

# Sentinel distinguishing "leave this attribute alone" from "set it to None"
# -- the walk's final step legitimately sets d.display_active = None.
_UNSET = object()


@dataclass
class Step:
    """One step of the walk: apply any of the given attribute assignments to
    `d`, invoke `d.<call>()` (skipped if `call` is None), then run `checks`
    -- a list of zero-arg callables (built via check_eq/check_in below) that
    each make one assertion. Fields left at their _UNSET default are simply
    not touched, so a step can change only what actually changes at that
    point in the walk (matching the original inline code, which likewise
    only reassigns what's changing)."""

    call: str | None
    input_event: object = _UNSET
    touch_pos: object = _UNSET
    display_active: object = _UNSET
    status_data: object = _UNSET
    display_object_list: object = _UNSET
    checks: list[Callable[[], None]] = field(default_factory=list)


def check_eq(getter, expected, label=""):
    """Returns a zero-arg callable asserting getter() == expected."""

    def _check():
        actual = getter()
        assert actual == expected, f"expected {label or 'value'}={expected!r}, got {actual!r}"

    return _check


def check_in(getter, options, label=""):
    """Returns a zero-arg callable asserting getter() in options."""

    def _check():
        actual = getter()
        assert actual in options, f"expected {label or 'value'} in {options!r}, got {actual!r}"

    return _check


def run_menu_walk(d, steps):
    """Executes a list of Steps against driver instance `d`, in order."""
    for step in steps:
        if step.display_active is not _UNSET:
            d.display_active = step.display_active
        if step.status_data is not _UNSET:
            d.status_data = step.status_data
        if step.display_object_list is not _UNSET:
            d.display_object_list = step.display_object_list
        if step.input_event is not _UNSET:
            d.input_event = step.input_event
        if step.touch_pos is not _UNSET:
            d.touch_pos = step.touch_pos
        if step.call is not None:
            getattr(d, step.call)()
        for check in step.checks:
            check()


def build_dash_menu_input_touch_steps(d, entry_event, status_data_factory, fake_flex_object_cls):
    """Builds the shared dash->menu_main->deep-menu/input->touch step table.

    entry_event: the input_event that drives dash -> menu_main ("UP" for
        ili9341f's button/encoder family, "ENTER" for the pygame/_base_dsi
        family).
    status_data_factory: callable(**overrides) -> status_data dict, used to
        build the Mode -> menu_main_* branch data (_flex_status_data /
        _dsi_status_data).
    fake_flex_object_cls: the caller's _FakeFlexObject class (identical
        implementations in both test files; passed in rather than imported,
        so this module stays independent of either file).
    """
    steps = [
        # _event_detect dispatch: unknown command is a no-op that still
        # clears input_event.
        Step(
            call="_event_detect",
            input_event="GARBAGE",
            checks=[check_eq(lambda: d.input_event, None, "input_event")],
        ),
        # dash + entry_event -> _process_button -> dash to menu_main.
        Step(
            call="_event_detect",
            input_event=entry_event,
            checks=[
                check_eq(lambda: d.display_active, "menu_main", "display_active"),
                check_eq(lambda: d.input_event, None, "input_event"),  # cleared after dispatch
            ],
        ),
    ]

    # Every other dash->menu mode branch (Stop was just exercised above).
    for mode, expected_active in [
        (Mode.STARTUP, "menu_main_active_normal"),
        (Mode.MONITOR, "menu_main_active_monitor"),
        (Mode.RECIPE, "menu_main_active_recipe"),
        (Mode.MANUAL, "menu_main"),  # falls through to the else branch
    ]:
        steps.append(
            Step(
                call="_process_button",
                display_active="dash",
                status_data=status_data_factory(mode=mode),
                checks=[check_eq(lambda: d.display_active, expected_active, "display_active")],
            )
        )
    steps.append(Step(call=None, status_data=status_data_factory()))  # restore Stop for what follows

    # Deep _process_button branches via a fake, fully-controlled menu object.
    obj = fake_flex_object_cls(
        ["menu_close", "menu_prime", "menu_startup", "cmd_monitor", "menu_system"], button_selected=1
    )
    steps += [
        Step(
            call="_process_button",
            display_active="menu_main",
            display_object_list=[obj],
            input_event="UP",  # button_selected <= 1 -> wraps to len(button_list) - 1
            checks=[check_eq(lambda: obj.get_object_data()["data"]["button_selected"], 4, "button_selected")],
        ),
        Step(
            call="_process_button",
            input_event="DOWN",  # len-1(4) not > 4 -> resets to 1
            checks=[check_eq(lambda: obj.get_object_data()["data"]["button_selected"], 1, "button_selected")],
        ),
        Step(
            call="_process_button",
            input_event="DOWN",  # len-1(4) > 1 -> increments
            checks=[check_eq(lambda: obj.get_object_data()["data"]["button_selected"], 2, "button_selected")],
        ),
        Step(
            call="_process_button",
            input_event="DOWN",
            checks=[check_eq(lambda: obj.get_object_data()["data"]["button_selected"], 3, "button_selected")],
        ),
        Step(
            call="_process_button",
            input_event="ENTER",  # selects "cmd_monitor" -> self.command + _command_handler()
            checks=[check_eq(lambda: d.command, "cmd_monitor", "command")],
        ),
    ]

    # ENTER on a cmd_ button that DOES carry button_value -> command_data set
    # from it (as opposed to the None-out branch just exercised above).
    obj_bv = fake_flex_object_cls(["cmd_a", "cmd_b"], button_selected=1, button_value=["va", "vb"])
    steps.append(
        Step(
            call="_process_button",
            display_active="menu_main",
            display_object_list=[obj_bv],
            input_event="ENTER",
            checks=[
                check_eq(lambda: d.command, "cmd_b", "command"),
                check_eq(lambda: d.command_data, "vb", "command_data"),
            ],
        )
    )

    # menu_close closes back to dash.
    obj2 = fake_flex_object_cls(["menu_close", "menu_prime"], button_selected=0)
    steps.append(
        Step(
            call="_process_button",
            display_active="menu_main",
            display_object_list=[obj2],
            input_event="ENTER",
            checks=[check_eq(lambda: d.display_active, "dash", "display_active")],
        )
    )

    # Nested menu_ selection from a non-dash menu.
    obj3 = fake_flex_object_cls(["menu_close", "menu_system"], button_selected=1)
    steps.append(
        Step(
            call="_process_button",
            display_active="menu_main",
            display_object_list=[obj3],
            input_event="ENTER",
            checks=[check_eq(lambda: d.display_active, "menu_system", "display_active")],
        )
    )

    # button_selected is None -> ENTER closes back to dash.
    obj4 = fake_flex_object_cls([], button_selected=None)
    steps.append(
        Step(
            call="_process_button",
            display_active="menu_system",
            display_object_list=[obj4],
            input_event="ENTER",
            checks=[check_eq(lambda: d.display_active, "dash", "display_active")],
        )
    )

    # "input_*" display_active branch: UP/DOWN edit, ENTER commits + closes.
    obj5 = fake_flex_object_cls([], button_selected=None, extra_data={"input": ""}, command="cmd_hold")
    steps += [
        Step(
            call="_process_button",
            display_active="input_hold",
            display_object_list=[obj5],
            input_event="UP",
            checks=[check_eq(lambda: obj5.get_object_data()["data"]["input"], "up", "input")],
        ),
        Step(
            call="_process_button",
            input_event="DOWN",
            checks=[check_eq(lambda: obj5.get_object_data()["data"]["input"], "down", "input")],
        ),
        Step(
            call="_process_button",
            input_event="ENTER",
            checks=[
                check_eq(lambda: d.command, "cmd_hold", "command"),
                check_eq(lambda: d.display_active, "dash", "display_active"),
            ],
        ),
    ]

    # _process_touch: cmd_ dispatch, menu_close, nested menu_, then the
    # "display currently inactive" wake branch.
    rect = pygame.Rect(0, 0, 50, 50)
    obj6 = fake_flex_object_cls(["cmd_monitor"], touch_areas=[rect])
    steps.append(
        Step(
            call="_process_touch",
            display_active="menu_main",
            display_object_list=[obj6],
            touch_pos=(10, 10),
            checks=[check_eq(lambda: d.command, "cmd_monitor", "command")],
        )
    )

    obj6b = fake_flex_object_cls(["cmd_x"], touch_areas=[pygame.Rect(0, 0, 50, 50)], button_value=["vx"])
    steps.append(
        Step(
            call="_process_touch",
            display_active="menu_main",
            display_object_list=[obj6b],
            touch_pos=(10, 10),
            checks=[
                check_eq(lambda: d.command, "cmd_x", "command"),
                check_eq(lambda: d.command_data, "vx", "command_data"),
            ],
        )
    )

    obj7 = fake_flex_object_cls(["menu_close"], touch_areas=[pygame.Rect(0, 0, 50, 50)])
    steps.append(
        Step(
            call="_process_touch",
            display_active="menu_main",
            display_object_list=[obj7],
            touch_pos=(5, 5),
            checks=[check_eq(lambda: d.display_active, "dash", "display_active")],
        )
    )

    obj8 = fake_flex_object_cls(["menu_system"], touch_areas=[pygame.Rect(0, 0, 50, 50)])
    steps.append(
        Step(
            call="_process_touch",
            display_active="menu_main",
            display_object_list=[obj8],
            touch_pos=(5, 5),
            checks=[check_eq(lambda: d.display_active, "menu_system", "display_active")],
        )
    )

    steps.append(
        Step(
            call="_process_touch",  # inactive -> _wake_display() + go to home/dash
            display_active=None,
            touch_pos=(0, 0),
            checks=[check_in(lambda: d.display_active, ("home", "dash"), "display_active")],
        )
    )

    return steps
