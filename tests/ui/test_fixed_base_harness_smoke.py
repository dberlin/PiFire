import pytest

from tests.ui.fixed_base_harness import (
    FONT_AVAILABLE,
    SAMPLE_IN_DATA,
    SAMPLE_STATUS_DATA,
    make_base,
    render,
)

pytestmark = pytest.mark.skipif(not FONT_AVAILABLE, reason="trebuc.ttf not installed")


def test_render_current_is_deterministic():
    b1 = make_base("display._base_320x480")
    b2 = make_base("display._base_320x480")
    h1 = render(b1, "_display_current", SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    h2 = render(b2, "_display_current", SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    assert h1 == h2  # same input -> same pixels -> same hash


def test_splash_and_text_render():
    b = make_base("display._base_320x480")
    assert len(render(b, "_display_splash")) == 64
    # _display_text renders self.display_data, which only exists once the
    # public display_text() setter has been called at least once.
    b.display_text("hello")
    assert len(render(b, "_display_text")) == 64


@pytest.mark.parametrize(
    ("module", "rotation"),
    [
        ("display._base_240x240", 0),
        ("display._base_240x320", 0),
        ("display._base_240x320", 90),
        ("display._base_320x480", 0),
        ("display._base_320x480", 90),
    ],
)
def test_long_text_fits_inside_every_fixed_viewport(module, rotation):
    base = make_base(module, rotation=rotation)
    base.display_text("Network Error")
    render(base, "_display_text")

    bounds = base._captured.convert("RGB").getbbox()
    assert bounds is not None
    left, top, right, bottom = bounds
    margin = 4
    assert left >= margin
    assert top >= margin
    assert right <= base.WIDTH - margin
    assert bottom <= base.HEIGHT - margin


def test_no_hardware_no_reboot():
    # os.system is neutralized in make_base; constructing must not raise or shell out.
    make_base("display._base_240x240")
    make_base("display._base_240x320")
