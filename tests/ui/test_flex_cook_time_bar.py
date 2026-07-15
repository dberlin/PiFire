from PIL import Image
from display.flexobject import CookTimeBar, resolve_accent


def _obj(**kw):
    base = {
        "name": "cook_time",
        "type": "cook_time_bar",
        "position": [0, 0],
        "size": [490, 40],
        "animation_enabled": False,
        "glow": False,
        "accent": resolve_accent("Ember"),
        "data": {"label": "COOK TIME", "value": "1:30:15", "highlight": False},
        "button_list": [],
        "button_value": [],
        "touch_areas": [],
    }
    base.update(kw)
    return base


def test_cook_time_bar_renders_to_size():
    obj = CookTimeBar("cook_time_bar", _obj(), Image.new("RGBA", (1024, 600)))
    assert obj.get_object_canvas().size == (490, 40)


def test_cook_time_bar_empty_value_still_renders():
    obj = CookTimeBar(
        "cook_time_bar",
        _obj(data={"label": "COOK TIME", "value": "", "highlight": False}),
        Image.new("RGBA", (1024, 600)),
    )
    assert obj.get_object_canvas().size == (490, 40)


def _min_dist_to(canvas, target):
    """Smallest euclidean RGB distance to `target` over opaque pixels."""
    best = 1e9
    for _count, px in canvas.getcolors(maxcolors=100000) or []:
        if px[3] < 120:  # ignore (near-)transparent pixels
            continue
        best = min(best, sum((px[i] - target[i]) ** 2 for i in range(3)) ** 0.5)
    return best


def test_cook_time_bar_lid_pause_alert_renders_red():
    # base_flex feeds label='Lid Pause' + a mm:ss countdown while the lid is open;
    # the bar recolors to a red alert (border #ff5a4d). Discriminate that red from
    # the normal bar's ember orange via color distance.
    red = (255, 90, 77)
    alert = CookTimeBar(
        "cook_time_bar",
        _obj(data={"label": "Lid Pause", "value": "00:58", "highlight": False}),
        Image.new("RGBA", (1024, 600)),
    ).get_object_canvas()
    normal = CookTimeBar("cook_time_bar", _obj(), Image.new("RGBA", (1024, 600))).get_object_canvas()
    assert alert.size == (490, 40)
    assert _min_dist_to(alert, red) < 30, "lid-pause bar should contain the red alert color"
    assert _min_dist_to(normal, red) > _min_dist_to(alert, red), "normal bar should be less red than the alert bar"
