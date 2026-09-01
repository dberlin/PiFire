import hashlib
import os

import pytest

from tests.ui import fixed_base_harness as harness
from tests.ui.fixed_base_harness import (
    FONT_AVAILABLE,
    SAMPLE_IN_DATA,
    SAMPLE_STATUS_DATA,
    make_base,
    render,
)

requires_font = pytest.mark.skipif(not FONT_AVAILABLE, reason="trebuc.ttf not installed")


def test_reference_renderer_requires_exact_font_and_library_versions(tmp_path, monkeypatch):
    font_path = tmp_path / "trebuc.ttf"
    font_path.write_bytes(b"reference-font")
    monkeypatch.setattr(
        harness,
        "_REFERENCE_TREBUCHET_SHA256",
        hashlib.sha256(font_path.read_bytes()).hexdigest(),
    )

    assert harness._is_reference_renderer(
        font_path,
        pillow_version=harness._REFERENCE_PILLOW_VERSION,
        freetype_version=harness._REFERENCE_FREETYPE_VERSION,
    )
    assert not harness._is_reference_renderer(
        font_path,
        pillow_version="different",
        freetype_version=harness._REFERENCE_FREETYPE_VERSION,
    )
    assert not harness._is_reference_renderer(
        font_path,
        pillow_version=harness._REFERENCE_PILLOW_VERSION,
        freetype_version="different",
    )
    font_path.write_bytes(b"different-font")
    assert not harness._is_reference_renderer(
        font_path,
        pillow_version=harness._REFERENCE_PILLOW_VERSION,
        freetype_version=harness._REFERENCE_FREETYPE_VERSION,
    )


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="CI renderer requirement")
def test_ci_runs_fixed_base_goldens_on_the_reference_renderer():
    assert harness.GOLDEN_ENVIRONMENT_AVAILABLE, (
        "CI must provide the fixed-base golden renderer: "
        f"font={harness._trebuchet_path}, "
        f"Pillow={harness.PILLOW_VERSION}, "
        f"FreeType={harness.features.version_module('freetype2')}"
    )


@requires_font
def test_render_current_is_deterministic():
    b1 = make_base("display._base_320x480")
    b2 = make_base("display._base_320x480")
    h1 = render(b1, "_display_current", SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    h2 = render(b2, "_display_current", SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    assert h1 == h2  # same input -> same pixels -> same hash


@requires_font
def test_splash_and_text_render():
    b = make_base("display._base_320x480")
    assert len(render(b, "_display_splash")) == 64
    # _display_text renders self.display_data, which only exists once the
    # public display_text() setter has been called at least once.
    b.display_text("hello")
    assert len(render(b, "_display_text")) == 64


@requires_font
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


@requires_font
def test_no_hardware_no_reboot():
    # os.system is neutralized in make_base; constructing must not raise or shell out.
    make_base("display._base_240x240")
    make_base("display._base_240x320")
