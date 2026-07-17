import pytest

from tests.ui.fixed_base_harness import (
    FONT_AVAILABLE,
    make_base,
    render,
    SAMPLE_IN_DATA,
    SAMPLE_STATUS_DATA,
)

pytestmark = pytest.mark.skipif(not FONT_AVAILABLE, reason="trebuc.ttf not installed")


def test_render_current_is_deterministic():
    b1 = make_base("display.base_320x480")
    b2 = make_base("display.base_320x480")
    h1 = render(b1, "_display_current", SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    h2 = render(b2, "_display_current", SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    assert h1 == h2  # same input -> same pixels -> same hash


def test_splash_and_text_render():
    b = make_base("display.base_320x480")
    assert len(render(b, "_display_splash")) == 64
    # _display_text renders self.display_data, which only exists once the
    # public display_text() setter has been called at least once.
    b.display_text("hello")
    assert len(render(b, "_display_text")) == 64


def test_no_hardware_no_reboot():
    # os.system is neutralized in make_base; constructing must not raise or shell out.
    make_base("display.base_240x240")
    make_base("display.base_240x320")
