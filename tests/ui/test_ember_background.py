import os
from PIL import Image

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PATH = os.path.join(BASE, "static", "img", "display", "background_ember_1280x720.png")


def test_ember_background_dimensions():
    assert os.path.exists(PATH)
    with Image.open(PATH) as im:
        assert im.size == (1280, 720)
