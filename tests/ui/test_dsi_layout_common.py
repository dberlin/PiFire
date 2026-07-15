"""Assertions that are genuinely identical across every DSI display layout
(1024x600t, 1024x768t, 1280x720t). Resolution-specific behavior (e.g. the
bespoke ember dash present on some resolutions but not others) stays in each
resolution's own test_dsi_<res>_layout.py file rather than being forced in
here.
"""

import pytest

from tests.conftest import dsi_layout_out_path, iter_dsi_layout_objects, load_json

RESOLUTIONS = [("dsi_1024x600t", 1024, 600), ("dsi_1024x768t", 1024, 768), ("dsi_1280x720t", 1280, 720)]

SCREENS = {
    "dsi_1024x600t": {"profile_1": (1024, 600), "profile_2": (600, 1024)},
    "dsi_1024x768t": {"profile_1": (1024, 768), "profile_2": (768, 1024)},
    "dsi_1280x720t": {"profile_1": (1280, 720), "profile_2": (720, 1280)},
}


@pytest.mark.parametrize("resolution,width,height", RESOLUTIONS)
def test_metadata(resolution, width, height):
    d = load_json(dsi_layout_out_path(resolution))
    assert d["metadata"]["name"] == resolution
    assert d["metadata"]["screen_width"] == width
    assert d["metadata"]["screen_height"] == height


@pytest.mark.parametrize("resolution", [r[0] for r in RESOLUTIONS])
def test_all_elements_on_screen(resolution):
    d = load_json(dsi_layout_out_path(resolution))
    for profile, (W, H) in SCREENS[resolution].items():
        for obj in iter_dsi_layout_objects(d[profile]):
            if "position" not in obj or "size" not in obj:
                continue
            x, y = obj["position"]
            w, h = obj["size"]
            assert 0 <= x and x + w <= W, f"{profile}:{obj.get('name')} x out of bounds"
            assert 0 <= y and y + h <= H, f"{profile}:{obj.get('name')} y out of bounds"
