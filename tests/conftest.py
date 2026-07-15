import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure the repository root is importable so `grillplat`, `common`, etc. resolve.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Must be set before any test module imports Qt/PySide6.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from common import datastore  # noqa: E402

# Repo root, used by tests that need to locate files (e.g. wizard/, display/)
# relative to the project rather than relative to the test file.
REPO_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def load_wizard_manifest():
    """Load and parse wizard/wizard_manifest.json from the repo root."""
    with open(os.path.join(REPO_BASE, "wizard", "wizard_manifest.json")) as f:
        return json.load(f)


def manifest_config_default(entry, option_name):
    """Return the default value of `option_name` within a manifest entry's config list."""
    for opt in entry["config"]:
        if opt["option_name"] == option_name:
            return opt["default"]
    raise AssertionError(f"{option_name} not in config")


# The resolution-agnostic source layout that every generated DSI display
# layout (display/dsi_<res>.json) is derived from.
DSI_LAYOUT_SRC = os.path.join(REPO_BASE, "display", "dsi_800x480t.json")


def load_json(path):
    """Load and parse a JSON file at `path`."""
    with open(path) as f:
        return json.load(f)


def dsi_layout_out_path(resolution):
    """Path to the generated layout JSON for a DSI resolution, e.g. 'dsi_1024x600t'."""
    return os.path.join(REPO_BASE, "display", f"{resolution}.json")


def iter_dsi_layout_objects(profile):
    """Yield every flexobject dict in a DSI layout profile (home/dash/menus/input)."""
    for section in ("home", "dash"):
        for obj in profile.get(section, []):
            yield obj
    for section in ("menus", "input"):
        for obj in profile.get(section, {}).values():
            yield obj


# Repo root (as a Path) and the QtQuick QML source tree, shared by the
# QtQuick component/screen tests below.
REPO = Path(__file__).resolve().parents[1]
QML_DIR = REPO / "display" / "qml"


@pytest.fixture
def qml_engine():
    """A QQmlApplicationEngine with the display/qml import path already added.

    Ensures a QGuiApplication exists first (QQmlApplicationEngine requires
    one), then returns a fresh engine ready for loading QML components.
    """
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    return engine


@pytest.fixture
def ds(tmp_path):
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    yield datastore
    datastore._reset_for_tests(None)


@pytest.fixture
def oracle():
    """Loader for tests/oracle/fixtures/<name>.json oracle files."""

    def _load(name):
        path = os.path.join(os.path.dirname(__file__), "oracle", "fixtures", f"{name}.json")
        return json.load(open(path))

    return _load


@pytest.fixture
def x86_platform():
    """A GrillPlatform (x86_numato) with all hardware mocked out.

    No `frequency` key is set in config, so GrillPlatform falls back to its
    class default -- tests relying on that default behavior depend on this.
    """
    import grillplat.x86_numato as mod

    with (
        mock.patch.object(mod, "NumatoUSBRelay"),
        mock.patch.object(mod, "EMC2101_LUT"),
        mock.patch.object(mod, "EMC2301"),
        mock.patch.object(mod, "open_i2c_bus"),
    ):
        config = {"outputs": {"power": 0, "igniter": 1, "auger": 2, "fan": 3}}
        yield mod.GrillPlatform(config)
