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

# Must be set before `common.common` is imported, for the same reason as
# PIFIRE_DB_PATH above: it resolves LOG_DIR at IMPORT time, and test modules
# that do `from app import app` at module scope build their loggers during
# COLLECTION -- before any fixture could redirect them.
#
# Without this the suite appended to the operator's real ./logs/events.log.
# That is why that file carried fixture strings ("Admin: Shutdown failed: boom",
# "[nonexistent_probe_module_xyz]", a WLED connection to 127.0.0.1:1) in the
# content the log viewer shows a user, and why the log files disagreed with the
# `logs` table -- tests already used a temporary database, but not temporary
# log files.
os.environ.setdefault("PIFIRE_LOG_DIR", tempfile.mkdtemp(prefix="pifire-test-logs-"))

from common import datastore  # noqa: E402

# Repo root, used by tests that need to locate files (e.g. wizard/, display/)
# relative to the project rather than relative to the test file.
REPO_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# Removed: the session-scoped `_os_info_cache_off_repo` fixture. It rebound
# `get_os_info` on common.system and grillplat.system_commands to redirect a
# `filepath` default, so the suite would stop dropping an os_info.json in the
# repo root. That parameter is long gone -- the cache moved into the datastore
# -- so the shim was not redirecting anything; it was passing a tmp file path
# as `loggername` to every module-attribute call in the session. A harness that
# silently reshapes production behaviour is worse than the artifact it was
# guarding against, and there is no artifact any more.
# Pinned by tests/unit/common/test_os_info_read_path_is_pure.py.


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
