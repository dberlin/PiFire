import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Ensure the repository root is importable so `grillplat`, `common`, etc. resolve.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Must be set before any test module imports Qt/PySide6.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Must be set before `common.datastore` is imported: it resolves DB_PATH at
# IMPORT time (`os.environ.get("PIFIRE_DB_PATH", <repo>/pifire.db)`) and caches
# it as _ORIGINAL_DB_PATH, which `_reset_for_tests(None)` restores to.
#
# Without this, merely COLLECTING the suite creates a real pifire.db in the
# repo root: many test modules do `from app import app` at module scope, and
# app.py's boot wiring initialises the datastore on import -- before any
# fixture can redirect it. (Proven: `pytest --collect-only` alone created it.)
# The per-test `ds` / `live_server` fixtures still redirect as before; this
# only moves the *default* off the repo so an import-time touch is harmless.
os.environ.setdefault(
    "PIFIRE_DB_PATH",
    os.path.join(tempfile.mkdtemp(prefix="pifire-test-db-"), "pifire.db"),
)

from common import datastore  # noqa: E402

# Repo root, used by tests that need to locate files (e.g. wizard/, display/)
# relative to the project rather than relative to the test file.
REPO_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True, scope="session")
def _os_info_cache_off_repo(tmp_path_factory):
    """Keep tests from dropping an os_info.json in the repo root.

    `common.system.get_os_info()` defaults to `filepath="os_info.json"` and
    WRITES that cache relative to the cwd. `get_display_os_info()` (admin page /
    mobile system-info panel) reads the cache and, on a miss, falls back to
    `get_os_info()` -- so every test touching those paths writes the artifact.
    That is a production wart (a read helper with a cwd write side effect), but
    fixing it properly is a behaviour change; this just redirects the default
    filepath for the test session so the suite stays hermetic.

    `from common.system import get_os_info` binds at import time, so rebinding
    only `common.system` would miss those callers. LSP findReferences puts the
    production bindings at exactly two sites -- common/system.py (used by
    get_display_os_info) and grillplat/system_commands.py -- so both are
    rebound here.
    """
    import common.system as _system
    import grillplat.system_commands as _syscmds

    real = _system.get_os_info
    cache_path = str(tmp_path_factory.mktemp("os_info_cache") / "os_info.json")

    def _redirected(filepath=cache_path, *args, **kwargs):
        return real(filepath, *args, **kwargs)

    originals = {}
    for mod in (_system, _syscmds):
        if getattr(mod, "get_os_info", None) is not None:
            originals[mod] = mod.get_os_info
            mod.get_os_info = _redirected
    yield
    for mod, orig in originals.items():
        mod.get_os_info = orig


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
