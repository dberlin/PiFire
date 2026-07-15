from PIL import Image

from display.flexobject import SystemCard, resolve_accent


def _obj(**kw):
    base = {
        "name": "system_card",
        "type": "system_card",
        "position": [0, 0],
        "size": [300, 260],
        "animation_enabled": False,
        "glow": False,
        "accent": resolve_accent("Ember"),
        "data": {"fan": True, "auger": True, "igniter": False},
        "button_list": ["cmd_fan_toggle", "cmd_auger_toggle", "cmd_igniter_toggle"],
        "button_value": [],
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_system_card_renders_to_size():
    obj = SystemCard("system_card", _obj(), Image.new("RGBA", (1280, 720)))
    assert obj.get_object_canvas().size == (300, 260)


def test_system_card_has_three_touch_rows():
    obj = SystemCard("system_card", _obj(), Image.new("RGBA", (1280, 720)))
    assert len(obj.get_object_data()["touch_areas"]) == 3


def test_system_card_touch_rows_are_stacked_and_map_to_button_list():
    obj = SystemCard("system_card", _obj(position=[20, 40], size=[300, 300]), Image.new("RGBA", (1280, 720)))
    areas = obj.get_object_data()["touch_areas"]
    # All rows share the card's x origin and width
    for area in areas:
        assert area.left == 20
        assert area.width == 300
    # Rows are stacked top-to-bottom without overlap, starting at the card's y origin
    assert areas[0].top == 40
    assert areas[0].top < areas[1].top < areas[2].top
    for a, b in zip(areas, areas[1:]):
        assert a.top + a.height <= b.top


def test_system_card_inactive_igniter_all_false():
    obj = SystemCard(
        "system_card", _obj(data={"fan": False, "auger": False, "igniter": False}), Image.new("RGBA", (1280, 720))
    )
    assert obj.get_object_canvas().size == (300, 260)


def test_system_card_igniter_active():
    obj = SystemCard(
        "system_card", _obj(data={"fan": False, "auger": False, "igniter": True}), Image.new("RGBA", (1280, 720))
    )
    assert obj.get_object_canvas().size == (300, 260)
