"""Pins the SmokePlus '+' overlay glyph rendering in StatusIcon.

F841 latent bug: the SmokePlus branch computed the '+' glyph into ``text`` but
only the cloud icon was ever drawn. This spies on ``_create_icon`` (the draw
primitive StatusIcon composes icons with) to assert both the cloud AND the '+'
glyph are rendered for the SmokePlus icon.
"""

from unittest.mock import MagicMock

from PIL import Image

from display.flexobject import StatusIcon

CLOUD_GLYPH = "\uf0c2"  # Font Awesome cloud (smoke)
PLUS_GLYPH = "\uf067"  # Font Awesome plus


def _obj(icon):
    return {
        "name": f"status_{icon}",
        "type": "status_icon",
        "icon": icon,
        "position": [0, 0],
        "size": [64, 64],
        "active": True,
        "active_color": (255, 255, 255),
        "inactive_color": (80, 80, 80),
        "animation_enabled": False,
    }


def _create_icon_glyphs(icon_type):
    """Render a StatusIcon and return the list of glyphs passed to _create_icon."""
    obj = StatusIcon("status_icon", _obj(icon_type), Image.new("RGBA", (128, 128)))
    spy = MagicMock(wraps=obj._create_icon)
    obj._create_icon = spy
    obj._draw_object()
    return [call.args[0] for call in spy.call_args_list]


def test_smokeplus_draws_both_cloud_and_plus():
    glyphs = _create_icon_glyphs("SmokePlus")
    assert CLOUD_GLYPH in glyphs, "cloud icon should be drawn"
    assert PLUS_GLYPH in glyphs, "the '+' overlay glyph should be drawn"


def test_non_smokeplus_icon_does_not_draw_plus():
    glyphs = _create_icon_glyphs("Fan")
    assert PLUS_GLYPH not in glyphs


def test_smokeplus_renders_to_output_size():
    obj = StatusIcon("status_icon", _obj("SmokePlus"), Image.new("RGBA", (128, 128)))
    assert obj.get_object_canvas().size == (64, 64)
