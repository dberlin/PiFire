from PIL import Image

from display.flexobject import ProbeCard, resolve_accent


def _obj(**data):
    base = {
        "name": "probe_card_0",
        "type": "probe_card",
        "position": [0, 0],
        "size": [298, 180],
        "animation_enabled": False,
        "glow": False,
        "accent": resolve_accent("Ember"),
        "units": "F",
        "data": {"name": "Brisket", "temp": 175, "target": 203},
        "button_list": ["input_notify"],
        "button_value": [],
        "touch_areas": [],
    }
    base.update(data)
    return base


def test_probe_card_renders_to_size():
    obj = ProbeCard("probe_card", _obj(), Image.new("RGBA", (1280, 720)))
    canvas = obj.get_object_canvas()
    assert canvas.size == (298, 180)


def test_probe_card_ambient_when_no_target():
    obj = ProbeCard(
        "probe_card", _obj(data={"name": "Ambient", "temp": 76, "target": 0}), Image.new("RGBA", (1280, 720))
    )
    assert obj.get_object_canvas().size == (298, 180)


def test_probe_card_done_state_no_exception():
    obj = ProbeCard(
        "probe_card", _obj(data={"name": "Brisket", "temp": 203, "target": 203}), Image.new("RGBA", (1280, 720))
    )
    assert obj.get_object_canvas().size == (298, 180)


def test_probe_card_touch_area_maps_to_input_notify():
    obj = ProbeCard("probe_card", _obj(), Image.new("RGBA", (1280, 720)))
    current_data = obj.get_object_data()
    assert current_data["button_list"] == ["input_notify"]
    assert len(current_data["touch_areas"]) == 1


def test_probe_card_defaults_accent_when_missing():
    object_data = _obj()
    del object_data["accent"]
    obj = ProbeCard("probe_card", object_data, Image.new("RGBA", (1280, 720)))
    assert obj.get_object_canvas().size == (298, 180)
