from PIL import Image

from display.flexobject import HeaderBar, resolve_accent


def _obj(**kw):
    base = {
        "name": "header_bar",
        "type": "header_bar",
        "position": [0, 0],
        "size": [1280, 58],
        "animation_enabled": False,
        "glow": False,
        "accent": resolve_accent("Ember"),
        "data": {"ip": "192.168.1.42", "clock": "14:52", "cooking": True},
        "button_list": ["menu_main"],
        "button_value": [],
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_header_bar_renders_to_size():
    obj = HeaderBar("header_bar", _obj(), Image.new("RGBA", (1280, 720)))
    assert obj.get_object_canvas().size == (1280, 58)


def test_header_bar_renders_to_size_when_scaled():
    obj = HeaderBar("header_bar", _obj(size=[640, 29]), Image.new("RGBA", (1280, 720)))
    assert obj.get_object_canvas().size == (640, 29)


def test_header_bar_not_cooking_renders():
    obj = HeaderBar(
        "header_bar",
        _obj(data={"ip": "192.168.1.42", "clock": "14:52", "cooking": False}),
        Image.new("RGBA", (1280, 720)),
    )
    assert obj.get_object_canvas().size == (1280, 58)


def test_header_bar_touch_is_hamburger_only():
    obj = HeaderBar("header_bar", _obj(), Image.new("RGBA", (1280, 720)))
    areas = obj.get_object_data()["touch_areas"]
    assert len(areas) == 1
    # the single touch area should be near the right edge (hamburger), not the full width
    r = areas[0]
    assert r.left > 1280 // 2
    assert r.width < 1280 // 2
    # fully contained within the bar's bounds
    assert r.left >= 0
    assert r.left + r.width <= 1280
    assert r.top >= 0
    assert r.top + r.height <= 58


def test_header_bar_touch_area_scales_with_size():
    obj = HeaderBar("header_bar", _obj(size=[640, 29]), Image.new("RGBA", (1280, 720)))
    areas = obj.get_object_data()["touch_areas"]
    assert len(areas) == 1
    r = areas[0]
    assert r.left > 640 // 2
    assert r.left + r.width <= 640
    assert r.top + r.height <= 29


def test_header_bar_touch_area_translates_with_position():
    obj = HeaderBar("header_bar", _obj(position=[100, 200]), Image.new("RGBA", (1280, 720)))
    areas = obj.get_object_data()["touch_areas"]
    r = areas[0]
    assert r.left >= 100
    assert r.top >= 200
    assert r.top + r.height <= 200 + 58
