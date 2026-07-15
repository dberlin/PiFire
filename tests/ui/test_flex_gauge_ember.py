from PIL import Image
from display.flexobject import GaugeEmber, resolve_accent


def _obj(**kw):
    base = {
        "name": "primary_gauge",
        "type": "gauge_ember",
        "position": [0, 0],
        "size": [560, 520],
        "animation_enabled": False,
        "glow": True,
        "accent": resolve_accent("Ember"),
        "temps": [182, 0, 180],
        "max_temp": 600,
        "units": "F",
        "label": "Grill",
        "data": {"mode_label": "SMOKE"},
        "button_list": ["input_notify"],
        "button_value": [],
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_gauge_ember_renders_to_size():
    obj = GaugeEmber("gauge_ember", _obj(), Image.new("RGBA", (1280, 720)))
    assert obj.get_object_canvas().size == (560, 520)


def test_gauge_ember_no_setpoint():
    obj = GaugeEmber("gauge_ember", _obj(temps=[300, 0, 0]), Image.new("RGBA", (1280, 720)))
    assert obj.get_object_canvas().size == (560, 520)
