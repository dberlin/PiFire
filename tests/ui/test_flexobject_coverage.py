"""
Coverage-floor tests for display/flexobject.py.

Follows the construction/render style of test_flex_gauge_ember.py,
test_flex_status_icon_smokeplus.py and test_flexobject_accent.py: build a
minimal objectData dict for the widget under test, instantiate the flex
widget class directly against an offscreen PIL background, and assert on
the produced canvas / object state / touch areas. No new harness is
invented - these are the same construction/assert idioms as the existing
tests, extended to the widget types and draw branches those files did not
yet exercise (gauge, gauge_compact, mode_bar, control_panel, menu,
qrcode, input_number(_simple), button, p_mode_control, splus_control,
hopper_status, plus animation/edge branches on already-partially-covered
widgets).

Where a test drives an internal helper (_animate_object,
_draw_letterspaced_text, _process_input) directly rather than through the
public update_object_data() entry point, that mirrors the spy-on-internals
approach already used by test_flex_status_icon_smokeplus.py - it targets a
specific branch without looping the real (infinite) display thread.
"""

import pytest
from PIL import Image

from display.flexobject import (
    AlertMessage,
    ButtonRow,
    ControlPanel,
    FlexButton,
    GaugeCircle,
    GaugeCompact,
    HeaderBar,
    HopperStatus,
    HopperVertical,
    InputNumber,
    InputNumberSimple,
    MenuGeneric,
    MenuIcon,
    MenuQRCode,
    ModeBar,
    PModeStatus,
    ProbeCard,
    SPlusStatus,
    StatusIcon,
    SystemCard,
    TimerStatus,
    resolve_accent,
)

BG = lambda: Image.new("RGBA", (1280, 720))  # noqa: E731


# ---------------------------------------------------------------------------
# GaugeCircle ("gauge")
# ---------------------------------------------------------------------------


def _gauge_obj(**kw):
    base = {
        "name": "gauge1",
        "type": "gauge",
        "position": [0, 0],
        "size": [300, 300],
        "animation_enabled": False,
        "temps": [225, 150, 250],
        "max_temp": 600,
        "units": "F",
        "label": "Grill",
        "font": "trebuc.ttf",
        "fg_color": (255, 255, 255, 255),
        "bg_color": (0, 0, 0, 255),
        "np_color": (255, 165, 0, 255),
        "sp_color": (0, 255, 0, 255),
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_gauge_circle_dual_setpoints_renders():
    obj = GaugeCircle("gauge", _gauge_obj(), BG())
    assert obj.get_object_canvas().size == (300, 300)


def test_gauge_circle_no_setpoints_renders():
    obj = GaugeCircle("gauge", _gauge_obj(temps=[300, 0, 0]), BG())
    assert obj.get_object_canvas().size == (300, 300)


def test_gauge_circle_long_label_truncated():
    obj = GaugeCircle("gauge", _gauge_obj(label="SuperLongLabelName"), BG())
    assert obj.get_object_canvas().size == (300, 300)


def _run_gauge_circle_animation(last_temp, target_temp, max_iters=15):
    obj = GaugeCircle("gauge", _gauge_obj(temps=[target_temp, 150, 250]), BG())
    obj.objectState["animation_active"] = True
    obj.objectState["animation_start"] = True
    obj.objectState["animation_lastData"] = {"temps": [last_temp, 150, 250]}
    for _ in range(max_iters):
        obj._animate_object()
        if not obj.objectState["animation_active"]:
            break
    return obj


def test_gauge_circle_animation_zero_delta_stops_immediately():
    obj = _run_gauge_circle_animation(last_temp=200, target_temp=200)
    assert obj.objectState["animation_active"] is False


def test_gauge_circle_animation_positive_delta_converges():
    obj = _run_gauge_circle_animation(last_temp=100, target_temp=109)
    assert obj.objectState["animation_temps"][0] == 109
    assert obj.objectState["animation_active"] is False


def test_gauge_circle_animation_negative_delta_clamps_to_zero():
    """Pins current behavior: stepping down to a target of 0 crosses the
    <= 0 clamp branch (objectData temps[0]=0), not the delta<=0 'reached
    target' branch."""
    obj = _run_gauge_circle_animation(last_temp=5, target_temp=0)
    assert obj.objectState["animation_temps"][0] == 0
    assert obj.objectState["animation_active"] is False


# ---------------------------------------------------------------------------
# GaugeCompact ("gauge_compact")
# ---------------------------------------------------------------------------


def _gauge_compact_obj(**kw):
    base = {
        "name": "gauge_compact1",
        "type": "gauge_compact",
        "position": [0, 0],
        "size": [300, 150],
        "animation_enabled": False,
        "temps": [225, 150, 250],
        "max_temp": 600,
        "units": "F",
        "label": "Grill",
        "font": "trebuc.ttf",
        "fg_color": (255, 255, 255, 255),
        "np_color": (255, 165, 0, 255),
        "sp_color": (0, 255, 0, 255),
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_gauge_compact_dual_temp_fahrenheit():
    obj = GaugeCompact("gauge_compact", _gauge_compact_obj(), BG())
    assert obj.get_object_canvas().size == (300, 150)


def test_gauge_compact_single_temp_celsius_long_label_clamped():
    obj = GaugeCompact(
        "gauge_compact",
        _gauge_compact_obj(
            label="A Very Long Gauge Label",
            units="C",
            temps=[900, 0, 400],  # temps[0] over max_temp -> bar clamp branch
        ),
        BG(),
    )
    assert obj.get_object_canvas().size == (300, 150)


def test_gauge_compact_zero_current_temp():
    obj = GaugeCompact("gauge_compact", _gauge_compact_obj(temps=[0, 0, 0]), BG())
    assert obj.get_object_canvas().size == (300, 150)


# ---------------------------------------------------------------------------
# ProbeCard - pin the "fraction rounds to 0" branch (fill bar not drawn)
# ---------------------------------------------------------------------------


def test_probe_card_tiny_fraction_skips_fill_draw():
    obj = ProbeCard(
        "probe_card",
        {
            "name": "probe_card_x",
            "type": "probe_card",
            "position": [0, 0],
            "size": [300, 165],
            "animation_enabled": False,
            "accent": resolve_accent("Ember"),
            "units": "F",
            "data": {"name": "Chicken", "temp": 1, "target": 400},
            "touch_areas": [],
        },
        BG(),
    )
    assert obj.get_object_canvas().size == (300, 165)


# ---------------------------------------------------------------------------
# SystemCard - rotation branch + _animate_object
# ---------------------------------------------------------------------------


def _system_card_obj(**kw):
    base = {
        "name": "system_card1",
        "type": "system_card",
        "position": [0, 0],
        "size": [300, 300],
        "animation_enabled": False,
        "accent": resolve_accent("Ember"),
        "data": {"fan": True, "auger": True, "igniter": False},
        "button_list": ["fan_toggle", "auger_toggle", "igniter_toggle"],
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_system_card_animate_fan_active_rotates_icon():
    obj = SystemCard("system_card", _system_card_obj(), BG())
    obj.objectState["animation_active"] = True
    obj.objectState["animation_start"] = True
    canvas = obj._animate_object()
    assert canvas.size == (300, 300)
    assert obj.objectState["animation_rotation"] == 15


def test_system_card_animate_fan_inactive_resets_rotation():
    obj = SystemCard("system_card", _system_card_obj(data={"fan": False, "auger": False, "igniter": True}), BG())
    obj.objectState["animation_active"] = True
    obj.objectState["animation_start"] = True
    obj._animate_object()
    obj.objectState["animation_rotation"] = 30  # simulate stale rotation
    obj._animate_object()
    assert obj.objectState["animation_rotation"] == 0


# ---------------------------------------------------------------------------
# ModeBar ("mode_bar")
# ---------------------------------------------------------------------------


def _mode_bar_obj(**kw):
    base = {
        "name": "mode_bar1",
        "type": "mode_bar",
        "position": [0, 0],
        "size": [400, 60],
        "animation_enabled": False,
        "text": "Hold",
        "font": "trebuc.ttf",
        "fg_color": (255, 255, 255, 255),
        "bg_color": (0, 0, 0, 255),
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_mode_bar_renders_short_text():
    obj = ModeBar("mode_bar", _mode_bar_obj(), BG())
    assert obj.get_object_canvas().size == (400, 60)


def test_mode_bar_long_text_truncated():
    obj = ModeBar("mode_bar", _mode_bar_obj(text="A Very Long Mode Bar Text String"), BG())
    assert obj.get_object_canvas().size == (400, 60)
    # ModeBar overrides _define_touch_areas to a no-op.
    assert obj.get_object_data()["touch_areas"] == []


# ---------------------------------------------------------------------------
# ControlPanel ("control_panel") - exercise every char_id branch
# ---------------------------------------------------------------------------

_CONTROL_PANEL_TYPES = [
    "Startup",
    "Prime",
    "Monitor",
    "Stop",
    "Smoke",
    "Hold",
    "Shutdown",
    "Next",
    "None",
    "SomeUnknownType",
]


def test_control_panel_all_icon_branches_and_active_highlight():
    button_list = [f"cmd_{t.lower()}" for t in _CONTROL_PANEL_TYPES]
    obj = ControlPanel(
        "control_panel",
        {
            "name": "control_panel1",
            "type": "control_panel",
            "position": [0, 0],
            "size": [400, 100],
            "animation_enabled": False,
            "button_type": _CONTROL_PANEL_TYPES,
            "button_active": "Stop",
            "button_list": button_list,
            "touch_areas": [],
        },
        BG(),
    )
    assert obj.get_object_canvas().size == (400, 100)
    assert len(obj.get_object_data()["touch_areas"]) == len(button_list)


# ---------------------------------------------------------------------------
# StatusIcon - remaining icon branches, breathe reset, rotation
# ---------------------------------------------------------------------------


def _status_icon_obj(icon, **kw):
    base = {
        "name": f"status_{icon}",
        "type": "status_icon",
        "icon": icon,
        "position": [0, 0],
        "size": [64, 64],
        "active": True,
        "active_color": (255, 255, 255),
        "inactive_color": (80, 80, 80),
        "animation_enabled": False,
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_status_icon_remaining_char_id_branches():
    for icon in ("Auger", "Igniter", "Notify", "Recipe", "Pause", "SomethingElse"):
        obj = StatusIcon("status_icon", _status_icon_obj(icon), Image.new("RGBA", (128, 128)))
        assert obj.get_object_canvas().size == (64, 64)


def test_status_icon_fan_animation_hits_rotation_branch():
    obj = StatusIcon("status_icon", _status_icon_obj("Fan"), Image.new("RGBA", (128, 128)))
    obj.objectState["animation_active"] = True
    obj.objectState["animation_start"] = True
    canvas = obj._animate_object()
    assert canvas.size == (64, 64)
    assert obj.objectState["animation_rotation"] == 15


def test_status_icon_breathe_step_resets_after_full_cycle():
    """The breathe counter resets to 0 as soon as it reaches
    len(animation_breath_steps) == 9 (checked/reset inside _draw_object,
    which runs synchronously within the 9th _animate_object() call)."""
    obj = StatusIcon("status_icon", _status_icon_obj("Auger"), Image.new("RGBA", (128, 128)))
    obj.objectState["animation_active"] = True
    obj.objectState["animation_start"] = True
    for _ in range(9):
        obj._animate_object()
    assert obj.objectState["animation_breathe"] == 0


# ---------------------------------------------------------------------------
# MenuIcon ("menu_icon")
# ---------------------------------------------------------------------------


def _menu_icon_obj(icon, **kw):
    base = {
        "name": f"menu_icon_{icon}",
        "type": "menu_icon",
        "icon": icon,
        "position": [0, 0],
        "size": [40, 40],
        "animation_enabled": False,
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_menu_icon_hamburger():
    obj = MenuIcon("menu_icon", _menu_icon_obj("Hamburger"), BG())
    assert obj.get_object_canvas().size == (40, 40)


def test_menu_icon_default_close_glyph():
    obj = MenuIcon("menu_icon", _menu_icon_obj("Close"), BG())
    assert obj.get_object_canvas().size == (40, 40)


# ---------------------------------------------------------------------------
# MenuGeneric ("menu")
# ---------------------------------------------------------------------------


def _menu_generic_obj(button_list, button_text, selected=None, **kw):
    base = {
        "name": "menu1",
        "type": "menu",
        "position": [0, 0],
        "size": [600, 400],
        "animation_enabled": False,
        "color": (255, 255, 255, 255),
        "title_text": "Menu",
        "font": "trebuc.ttf",
        "data": {"button_selected": selected},
        "button_list": button_list,
        "button_text": button_text,
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_menu_generic_single_column_default_selection():
    buttons = ["cmd_a", "cmd_b", "cmd_c"]
    obj = MenuGeneric("menu", _menu_generic_obj(buttons, ["A", "B", "C"]), BG())
    assert obj.get_object_canvas().size == (600, 400)
    # No selection provided with >1 buttons -> auto-selects index 1.
    assert obj.get_object_data()["data"]["button_selected"] == 1
    assert len(obj.get_object_data()["touch_areas"]) == 3


def test_menu_generic_two_column_mode_with_explicit_selection():
    buttons = [f"cmd_{i}" for i in range(8)]
    texts = [f"Item {i}" for i in range(8)]
    obj = MenuGeneric("menu", _menu_generic_obj(buttons, texts, selected=3), BG())
    assert obj.get_object_canvas().size == (600, 400)
    assert len(obj.get_object_data()["touch_areas"]) == 8


def test_menu_generic_close_button_branch():
    buttons = ["menu_close", "cmd_a", "cmd_b"]
    texts = ["Close Menu", "A", "B"]
    obj = MenuGeneric("menu", _menu_generic_obj(buttons, texts), BG())
    assert obj.get_object_canvas().size == (600, 400)
    # Close button + 2 regular buttons = 3 touch areas.
    assert len(obj.get_object_data()["touch_areas"]) == 3


def test_menu_generic_long_button_text_truncated():
    buttons = ["cmd_a", "cmd_b"]
    texts = ["A" * 40, "Short"]
    obj = MenuGeneric("menu", _menu_generic_obj(buttons, texts), BG())
    assert obj.get_object_canvas().size == (600, 400)


def test_menu_generic_more_than_eleven_buttons_breaks_loop():
    buttons = [f"cmd_{i}" for i in range(13)]
    texts = [f"Item {i}" for i in range(13)]
    obj = MenuGeneric("menu", _menu_generic_obj(buttons, texts), BG())
    assert obj.get_object_canvas().size == (600, 400)
    # Loop breaks once button_count > 10, so only 11 touch areas are created.
    assert len(obj.get_object_data()["touch_areas"]) == 11


# ---------------------------------------------------------------------------
# MenuQRCode ("qrcode")
# ---------------------------------------------------------------------------


def test_menu_qrcode_renders():
    obj = MenuQRCode(
        "qrcode",
        {
            "name": "qr1",
            "type": "qrcode",
            "position": [0, 0],
            "size": [300, 300],
            "animation_enabled": False,
            "color": (255, 255, 255, 255),
            "ip_address": "192.168.1.50",
            "touch_areas": [],
        },
        BG(),
    )
    assert obj.get_object_canvas().size == (300, 300)


# ---------------------------------------------------------------------------
# InputNumber ("input_number") and InputNumberSimple ("input_number_simple")
# ---------------------------------------------------------------------------


def _input_number_obj(value=0, input_="", **kw):
    base = {
        "name": "input1",
        "type": "input_number",
        "position": [0, 0],
        "size": [300, 200],
        "animation_enabled": False,
        "title_text": "Set Temp",
        "font": "trebuc.ttf",
        "color": (255, 255, 255, 255),
        "data": {"value": value, "input": input_},
        "step": 5,
        "command": "input_hold",
        "touch_areas": [],
        "button_list": [],
    }
    base.update(kw)
    return base


def _drive_input(cls, obj_type, value, input_, step=5):
    """Constructs the widget, which runs _process_input once via __init__.

    Returns the resulting data['value'].
    """
    obj = cls(obj_type, _input_number_obj(value=value, input_=input_, step=step), BG())
    return obj.get_object_data()["data"]["value"]


def test_input_number_renders_and_builds_full_button_list():
    obj = InputNumber("input_number", _input_number_obj(value=225), BG())
    assert obj.get_object_canvas().size == (300, 200)
    button_list = obj.get_object_data()["button_list"]
    # menu_close, button_up, button_down, <command>, then 12 keypad buttons (4 rows x 3 cols).
    assert len(button_list) == 16
    assert "menu_close" in button_list
    assert "button_up" in button_list
    assert "button_down" in button_list
    assert "input_hold" in button_list


def test_input_number_process_input_up_and_down_clamped():
    assert _drive_input(InputNumber, "input_number", 225, "up") == 230
    assert _drive_input(InputNumber, "input_number", 2, "down") == 0  # clamps at 0


def test_input_number_process_input_digit_and_del():
    assert _drive_input(InputNumber, "input_number", 0, "5") == 5
    assert _drive_input(InputNumber, "input_number", 25, "DEL") == 2


def test_input_number_process_input_decimal_branches():
    # "." pressed while a "." is already present -> no-op (pass branch).
    assert _drive_input(InputNumber, "input_number", 1.5, ".") == 1.5
    # digit appended after an existing decimal point.
    assert _drive_input(InputNumber, "input_number", 1.5, "7") == 1.7
    # 3-digit value + "." appends the decimal point itself.
    assert _drive_input(InputNumber, "input_number", 100, ".") == 100.0


def test_input_number_highlighted_button_pushed_branches():
    obj = InputNumber("input_number", _input_number_obj(value=100), BG())
    for pushed in ("up", "down", "5", "DEL"):
        obj.objectState["animation_input"] = pushed
        canvas = obj._draw_object()
        assert canvas.size == (300, 200)


def test_input_number_animate_cycle_clears_input_after_two_frames():
    obj = InputNumber("input_number", _input_number_obj(value=100, input_="up"), BG())
    obj.objectState["animation_active"] = True
    obj.objectState["animation_start"] = True
    for _ in range(3):
        obj._animate_object()
    assert obj.objectState["animation_active"] is False
    assert obj.objectState["animation_input"] == ""


def _input_number_simple_obj(value=0, input_="", **kw):
    base = {
        "name": "input_simple1",
        "type": "input_number_simple",
        "position": [0, 0],
        "size": [600, 400],
        "animation_enabled": False,
        "title_text": "Set Temp",
        "font": "trebuc.ttf",
        "color": (255, 255, 255, 255),
        "data": {"value": value, "input": input_},
        "step": 5,
        "command": "input_hold",
        "touch_areas": [],
        "button_list": [],
    }
    base.update(kw)
    return base


def test_input_number_simple_renders_and_builds_button_list():
    obj = InputNumberSimple("input_number_simple", _input_number_simple_obj(value=225), BG())
    assert obj.get_object_canvas().size == (600, 400)
    button_list = obj.get_object_data()["button_list"]
    assert len(button_list) == 4  # menu_close, button_up, button_down, <command>
    assert "input_hold" in button_list


def test_input_number_simple_process_input_up_down_digit_del():
    assert _drive_input(InputNumberSimple, "input_number_simple", 225, "up") == 230
    assert _drive_input(InputNumberSimple, "input_number_simple", 2, "down") == 0
    assert _drive_input(InputNumberSimple, "input_number_simple", 0, "5") == 5
    assert _drive_input(InputNumberSimple, "input_number_simple", 25, "DEL") == 2


def test_input_number_simple_process_input_decimal_branches():
    assert _drive_input(InputNumberSimple, "input_number_simple", 1.5, ".") == 1.5
    assert _drive_input(InputNumberSimple, "input_number_simple", 1.5, "7") == 1.7
    assert _drive_input(InputNumberSimple, "input_number_simple", 100, ".") == 100.0


def test_input_number_simple_highlighted_button_pushed_branches():
    obj = InputNumberSimple("input_number_simple", _input_number_simple_obj(value=100), BG())
    for pushed in ("up", "down"):
        obj.objectState["animation_input"] = pushed
        canvas = obj._draw_object()
        assert canvas.size == (600, 400)


def test_input_number_simple_animate_cycle_clears_input_after_two_frames():
    obj = InputNumberSimple("input_number_simple", _input_number_simple_obj(value=100, input_="down"), BG())
    obj.objectState["animation_active"] = True
    obj.objectState["animation_start"] = True
    for _ in range(3):
        obj._animate_object()
    assert obj.objectState["animation_active"] is False
    assert obj.objectState["animation_input"] == ""


# ---------------------------------------------------------------------------
# TimerStatus ("timer") - active (seconds > 0) branch
# ---------------------------------------------------------------------------


def test_timer_status_active_countdown_long_label_truncated():
    obj = TimerStatus(
        "timer",
        {
            "name": "timer1",
            "type": "timer",
            "position": [0, 0],
            "size": [400, 200],
            "animation_enabled": False,
            "fg_color": (255, 255, 255, 255),
            "bg_color": (0, 0, 0, 255),
            "label": "A Very Long Timer Label",
            "data": {"seconds": 90},
            "touch_areas": [],
        },
        BG(),
    )
    assert obj.get_object_canvas().size == (400, 200)


# ---------------------------------------------------------------------------
# AlertMessage ("alert") - active branch with multi-line, truncated text
# ---------------------------------------------------------------------------


def test_alert_message_active_multiline_truncated_text():
    obj = AlertMessage(
        "alert",
        {
            "name": "alert1",
            "type": "alert",
            "position": [0, 0],
            "size": [400, 200],
            "animation_enabled": False,
            "fg_color": (255, 255, 255, 255),
            "bg_color": (0, 0, 0, 255),
            "active": True,
            "data": {"text": ["This line is long enough to truncate", "Second line"]},
            "touch_areas": [],
        },
        BG(),
    )
    assert obj.get_object_canvas().size == (400, 200)


# ---------------------------------------------------------------------------
# FlexButton ("button")
# ---------------------------------------------------------------------------


def _flex_button_obj(active, **kw):
    base = {
        "name": "button1",
        "type": "button",
        "position": [0, 0],
        "size": [100, 100],
        "animation_enabled": False,
        "active": active,
        "active_color": (255, 255, 255, 255),
        "inactive_color": (80, 80, 80, 255),
        "data": {},
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_flex_button_active_with_custom_icon():
    obj = FlexButton("button", _flex_button_obj(True, data={"active_icon": ""}), BG())
    assert obj.get_object_canvas().size == (100, 100)


def test_flex_button_inactive_default_icon():
    obj = FlexButton("button", _flex_button_obj(False), BG())
    assert obj.get_object_canvas().size == (100, 100)


# ---------------------------------------------------------------------------
# PModeStatus ("p_mode_control")
# ---------------------------------------------------------------------------


def _p_mode_obj(active, **kw):
    base = {
        "name": "pmode1",
        "type": "p_mode_control",
        "position": [0, 0],
        "size": [400, 200],
        "animation_enabled": False,
        "active": active,
        "fg_color": (255, 255, 255, 255),
        "bg_color": (0, 0, 0, 255),
        "label": "P-Mode",
        "data": {"pmode": 3},
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_p_mode_status_active_renders_pmode():
    obj = PModeStatus("p_mode_control", _p_mode_obj(True), BG())
    assert obj.get_object_canvas().size == (400, 200)


def test_p_mode_status_inactive_empty_canvas():
    obj = PModeStatus("p_mode_control", _p_mode_obj(False), BG())
    assert obj.get_object_canvas().size == (400, 200)


# ---------------------------------------------------------------------------
# SPlusStatus ("splus_control")
# ---------------------------------------------------------------------------


def test_splus_status_active_and_inactive_render():
    for active in (True, False):
        obj = SPlusStatus(
            "splus_control",
            {
                "name": "splus1",
                "type": "splus_control",
                "position": [0, 0],
                "size": [100, 100],
                "animation_enabled": False,
                "active": active,
                "active_color": (255, 255, 255, 255),
                "inactive_color": (80, 80, 80, 255),
                "touch_areas": [],
            },
            BG(),
        )
        assert obj.get_object_canvas().size == (100, 100)


# ---------------------------------------------------------------------------
# HopperStatus ("hopper_status")
# ---------------------------------------------------------------------------


def _hopper_status_obj(level, **kw):
    base = {
        "name": "hopper1",
        "type": "hopper_status",
        "position": [0, 0],
        "size": [400, 200],
        "animation_enabled": False,
        "label": "Hopper",
        "color_levels": [
            (255, 0, 0, 255),
            (255, 165, 0, 255),
            (255, 255, 0, 255),
            (0, 255, 0, 255),
        ],
        "data": {"level": level},
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_hopper_status_zero_level_uses_fg_as_bg():
    obj = HopperStatus("hopper_status", _hopper_status_obj(0), BG())
    assert obj.get_object_canvas().size == (400, 200)


def test_hopper_status_mid_level():
    obj = HopperStatus("hopper_status", _hopper_status_obj(50), BG())
    assert obj.get_object_canvas().size == (400, 200)


def test_hopper_status_full_level():
    # Note: the bar-clamp branch (`current_level_adjusted > 360`) is
    # unreachable for any level within the documented 0-100 range, since
    # int((level/100)*320)+40 tops out at exactly 360 when level == 100.
    obj = HopperStatus("hopper_status", _hopper_status_obj(100), BG())
    assert obj.get_object_canvas().size == (400, 200)


def test_hopper_status_out_of_range_level_clamps_to_top_color():
    """FIXED: HopperStatus did not clamp data['level'] to 0-100.

    `color_index = int(level // (100/len(color_levels)))` then indexes
    `color_levels[max(color_index-1, 0)]`. With the 4-entry color_levels
    used by `_hopper_status_obj` (valid indices 0-3), level=150 gives
    color_index = int(150 // 25) = 6, so the lookup was
    color_levels[max(6-1, 0)] == color_levels[5], out of range, and raised
    IndexError instead of clamping/drawing.

    The index is now also clamped from above
    (`min(..., len(color_levels) - 1)`), so an out-of-range level renders
    using the nearest valid (topmost) color level instead of crashing. In-
    range behavior (test_hopper_status_zero/mid/full_level above) is
    unchanged.
    """
    obj = HopperStatus("hopper_status", _hopper_status_obj(150), BG())
    assert obj.get_object_canvas().size == (400, 200)


# ---------------------------------------------------------------------------
# HopperVertical / HeaderBar - _draw_letterspaced_text edge branches
# (space-separated glyphs, and the empty-text 1x1 fallback)
# ---------------------------------------------------------------------------


def _hopper_vertical():
    return HopperVertical(
        "hopper_vertical",
        {
            "name": "hopper_vertical1",
            "type": "hopper_vertical",
            "position": [0, 0],
            "size": [200, 200],
            "animation_enabled": False,
            "accent": resolve_accent("Ember"),
            "data": {"level": 40},
            "touch_areas": [],
        },
        BG(),
    )


def test_hopper_vertical_letterspaced_text_space_and_empty_branches():
    obj = _hopper_vertical()
    with_space = obj._draw_letterspaced_text("LOW LEVEL", "./static/font/Barlow-SemiBold.ttf", 15, (255, 0, 0, 255), 3)
    assert with_space.size[0] > 0
    empty = obj._draw_letterspaced_text("", "./static/font/Barlow-SemiBold.ttf", 15, (255, 0, 0, 255), 3)
    assert empty.size == (1, 1)


def test_hopper_vertical_threshold_bands():
    obj = _hopper_vertical()
    assert obj._threshold(5)[1] == "REFILL PELLETS"
    assert obj._threshold(25)[1] == "RUNNING LOW"
    assert obj._threshold(80)[1] == "LEVEL OK"


def _header_bar():
    return HeaderBar(
        "header_bar",
        {
            "name": "header_bar1",
            "type": "header_bar",
            "position": [0, 0],
            "size": [1280, 58],
            "animation_enabled": False,
            "accent": resolve_accent("Ember"),
            "data": {"ip": "192.168.1.1", "clock": "12:00", "cooking": True},
            "touch_areas": [],
        },
        BG(),
    )


def test_header_bar_letterspaced_text_space_and_empty_branches():
    obj = _header_bar()
    with_space = obj._draw_letterspaced_text(
        "CONTROL PANEL", "./static/font/Barlow-SemiBold.ttf", 12, (255, 255, 255, 255), 2
    )
    assert with_space.size[0] > 0
    empty = obj._draw_letterspaced_text("", "./static/font/Barlow-SemiBold.ttf", 12, (255, 255, 255, 255), 2)
    assert empty.size == (1, 1)


# ---------------------------------------------------------------------------
# ButtonRow - active-label "ok" border branch, empty button_type/list
# ---------------------------------------------------------------------------


def _button_row_obj(**kw):
    base = {
        "name": "button_row1",
        "type": "button_row",
        "position": [0, 0],
        "size": [1200, 164],
        "animation_enabled": False,
        "accent": resolve_accent("Ember"),
        "button_type": ["Set Temp", "Hold", "Stop", "Shutdown"],
        "button_list": ["input_hold", "cmd_smoke", "cmd_stop", "cmd_shutdown"],
        "button_active": "",
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_button_row_active_non_danger_label_hits_ok_border():
    obj = ButtonRow("button_row", _button_row_obj(button_active="Hold"), BG())
    assert obj.get_object_canvas().size == (1200, 164)


def test_button_row_empty_buttons_skips_render_and_touch_areas():
    obj = ButtonRow("button_row", _button_row_obj(button_type=[], button_list=[]), BG())
    assert obj.get_object_canvas().size == (1200, 164)
    assert obj.get_object_data()["touch_areas"] == []
