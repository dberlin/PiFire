"""Tests for controller/runtime/devices.py -- build_devices()/build_display()'s
prototype-fallback logic for the grill platform, probe complex, distance
(hopper-level) sensor, and display.

Mocks the hardware-factory boundary (importlib.import_module, and
probes.main.ProbesMain for the probe-setup-failure path) and uses the `ds`
fixture (a throwaway sqlite datastore, see tests/conftest.py) so that
read_control/write_control/read_pellet_db/write_pellet_db/write_errors/
write_generic_key calls never touch the repo's real pifire.db.

Two genuinely broken code paths were found while writing these tests and are
now FIXED -- see the docstrings on
test_build_display_import_failure_falls_back_to_none_module and
test_build_display_import_failure_uses_default_display_config_and_rotation,
which used to pin the crashes via `pytest.raises` and now assert the
corrected fallback behavior.
"""

import importlib
import json

import pytest

from common import datastore
from common.datastore_accessors import read_control, read_pellet_db


def _settings(**overrides):
    settings = {
        "modules": {"grillplat": "prototype", "dist": "prototype", "display": "none"},
        "platform": {
            "devices": {},
            "buttonslevel": "HIGH",
            "outputs": {"auger": 14, "dc_fan": 26, "fan": 15, "igniter": 18, "power": 4, "pwm": 13},
            "inputs": {"selector": 17, "shutdown": 17},
            "dc_fan": False,
            "standalone": True,
        },
        "pelletlevel": {"empty": 22, "full": 4},
        "globals": {"units": "F", "debug_mode": False},
        "pwm": {"frequency": 100},
        "probe_settings": {"probe_map": {"probe_info": [], "probe_devices": []}},
        "display": {"config": {"none": {}}},
    }
    settings.update(overrides)
    return settings


class _RecordingLogger:
    """Fake event/control logger that records what was logged, instead of a
    silent no-op, so tests can assert *which* failure path actually ran."""

    def __init__(self):
        self.infos = []
        self.errors = []
        self.exceptions = []

    def info(self, msg, *a, **k):
        self.infos.append(msg)

    def error(self, msg, *a, **k):
        self.errors.append(msg)

    def exception(self, msg, *a, **k):
        self.exceptions.append(msg)


class _FakeModule:
    """Minimal stand-in for an imported device module: attributes passed as
    kwargs become the module's classes (e.g. GrillPlatform=, Display=)."""

    def __init__(self, **attrs):
        for name, value in attrs.items():
            setattr(self, name, value)


class _RaisingGrillPlatform:
    def __init__(self, *a, **k):
        raise RuntimeError("boom: bad grillplat config")


class _RaisingHopperLevel:
    def __init__(self, *a, **k):
        raise RuntimeError("boom: bad distance config")


class _RaisingDisplay:
    def __init__(self, *a, **k):
        raise RuntimeError("boom: bad display config")


def _selective_import(overrides):
    """A drop-in for importlib.import_module: for names in `overrides`, either
    raise (if the value is an exception type) or return it directly (if it's
    a fake module); every other name is forwarded to the real
    importlib.import_module so unrelated imports keep working normally."""
    real_import_module = importlib.import_module

    def _fake(name, *a, **k):
        if name in overrides:
            target = overrides[name]
            if isinstance(target, type) and issubclass(target, BaseException):
                raise target(f"forced failure importing {name}")
            return target
        return real_import_module(name, *a, **k)

    return _fake


# ---------------------------------------------------------------------------
# build_devices(): happy path
# ---------------------------------------------------------------------------


def test_build_devices_prototype_success_random_hopper_and_probe_info_written(ds):
    from controller.runtime.devices import build_devices

    settings = _settings()  # grillplat=prototype, dist=prototype -> "if" branch (random=True)
    devices, errors = build_devices(settings, errors=[], event_log=_RecordingLogger(), control_log=_RecordingLogger())

    assert errors == []
    assert type(devices.grill_platform).__module__ == "grillplat.prototype"
    assert devices.probe_complex.disable is False
    # Both modules == "prototype" selects the random-reading branch (lines
    # 241-249): distance.prototype.HopperLevel records the `random` flag and
    # returns a random level in [10, 100] rather than a fixed value.
    assert devices.dist_device.random is True
    hopper_level = read_pellet_db()["current"]["hopper_level"]
    assert 10 <= hopper_level <= 100
    # Probe device info was forwarded to the frontend via write_generic_key.
    assert json.loads(datastore.get_blob("probe_device_info")) == []
    assert not read_control().get("critical_error")


def test_build_devices_distance_else_branch_when_not_both_prototype(ds):
    """When grillplat and dist aren't *both* "prototype", the non-random
    branch (lines 250-256) runs instead -- covered here via dist="none",
    which (unlike distance.prototype) has no `random` attribute at all."""
    from controller.runtime.devices import build_devices

    settings = _settings()
    settings["modules"]["dist"] = "none"

    devices, errors = build_devices(settings, errors=[], event_log=_RecordingLogger(), control_log=_RecordingLogger())

    assert errors == []
    assert type(devices.dist_device).__module__ == "distance.none"
    assert not hasattr(devices.dist_device, "random")
    assert devices.dist_device.get_level() == 100


# ---------------------------------------------------------------------------
# build_devices(): grillplat fallback
# ---------------------------------------------------------------------------


def test_build_devices_grillplat_import_failure_falls_back_to_prototype(ds, monkeypatch):
    from controller.runtime.devices import build_devices

    settings = _settings()
    settings["modules"]["grillplat"] = "nonexistent_grillplat_xyz"
    monkeypatch.setattr(
        "controller.runtime.devices.importlib.import_module",
        _selective_import({"grillplat.nonexistent_grillplat_xyz": ModuleNotFoundError}),
    )
    control_log = _RecordingLogger()

    devices, errors = build_devices(settings, errors=[], event_log=_RecordingLogger(), control_log=control_log)

    assert len(errors) == 1
    assert "nonexistent_grillplat_xyz" in errors[0]
    assert control_log.exceptions and control_log.errors
    assert read_control()["critical_error"] is True
    assert type(devices.grill_platform).__module__ == "grillplat.prototype"


def test_build_devices_grillplat_import_failure_debug_mode_reraises(ds, monkeypatch):
    from controller.runtime.devices import build_devices

    settings = _settings()
    settings["modules"]["grillplat"] = "nonexistent_grillplat_xyz"
    settings["globals"]["debug_mode"] = True
    monkeypatch.setattr(
        "controller.runtime.devices.importlib.import_module",
        _selective_import({"grillplat.nonexistent_grillplat_xyz": ModuleNotFoundError}),
    )

    with pytest.raises(ModuleNotFoundError):
        build_devices(settings, errors=[], event_log=_RecordingLogger(), control_log=_RecordingLogger())

    # The critical_error flag was still set before the re-raise.
    assert read_control()["critical_error"] is True


def test_build_devices_grillplat_configure_failure_falls_back_to_prototype(ds, monkeypatch):
    from controller.runtime.devices import build_devices

    settings = _settings()
    settings["modules"]["grillplat"] = "broken_grillplat"
    monkeypatch.setattr(
        "controller.runtime.devices.importlib.import_module",
        _selective_import({"grillplat.broken_grillplat": _FakeModule(GrillPlatform=_RaisingGrillPlatform)}),
    )

    devices, errors = build_devices(settings, errors=[], event_log=_RecordingLogger(), control_log=_RecordingLogger())

    assert len(errors) == 1
    assert read_control()["critical_error"] is True
    assert type(devices.grill_platform).__module__ == "grillplat.prototype"


def test_build_devices_grillplat_configure_failure_debug_mode_reraises(ds, monkeypatch):
    from controller.runtime.devices import build_devices

    settings = _settings()
    settings["modules"]["grillplat"] = "broken_grillplat"
    settings["globals"]["debug_mode"] = True
    monkeypatch.setattr(
        "controller.runtime.devices.importlib.import_module",
        _selective_import({"grillplat.broken_grillplat": _FakeModule(GrillPlatform=_RaisingGrillPlatform)}),
    )

    with pytest.raises(RuntimeError, match="boom: bad grillplat config"):
        build_devices(settings, errors=[], event_log=_RecordingLogger(), control_log=_RecordingLogger())


# ---------------------------------------------------------------------------
# build_devices(): probe complex fallback
# ---------------------------------------------------------------------------


class _FakeProbesMain:
    """Stands in for probes.main.ProbesMain: raises unless constructed with
    disable=True, mirroring the "primary construction failed" / "disabled
    fallback succeeded" shape devices.py depends on."""

    def __init__(self, probe_map, units, disable=False):
        if not disable:
            raise RuntimeError("boom: probe setup failed")
        self.probe_map = probe_map
        self.units = units
        self.disable = disable

    def get_errors(self):
        return []

    def get_device_info(self):
        return []


def test_build_devices_probes_setup_failure_falls_back_to_disabled(ds, monkeypatch):
    from controller.runtime.devices import build_devices

    monkeypatch.setattr("probes.main.ProbesMain", _FakeProbesMain)
    control_log = _RecordingLogger()

    devices, errors = build_devices(_settings(), errors=[], event_log=_RecordingLogger(), control_log=control_log)

    assert len(errors) == 1
    assert control_log.exceptions
    assert devices.probe_complex.disable is True


def test_build_devices_probes_setup_failure_debug_mode_reraises(ds, monkeypatch):
    from controller.runtime.devices import build_devices

    monkeypatch.setattr("probes.main.ProbesMain", _FakeProbesMain)
    settings = _settings()
    settings["globals"]["debug_mode"] = True

    with pytest.raises(RuntimeError, match="boom: probe setup failed"):
        build_devices(settings, errors=[], event_log=_RecordingLogger(), control_log=_RecordingLogger())


def test_build_devices_probe_errors_forwarded_to_frontend(ds):
    """No mocking: a probe device naming a nonexistent module is handled
    entirely inside probes.main.ProbesMain (it falls back to probes.disabled
    per-device and records the failure in its own .errors list) without
    raising, so build_devices() takes the "probe_errors" branch (lines
    208-217) to relay that per-device failure into the shared errors list."""
    from controller.runtime.devices import build_devices

    settings = _settings()
    settings["probe_settings"]["probe_map"] = {
        "probe_info": [],
        "probe_devices": [{"device": "bad_probe", "module": "nonexistent_probe_module_xyz", "config": {}, "ports": []}],
    }
    event_log = _RecordingLogger()

    devices, errors = build_devices(settings, errors=[], event_log=event_log, control_log=_RecordingLogger())

    probe_errors = devices.probe_complex.get_errors()
    assert len(probe_errors) == 1
    assert "nonexistent_probe_module_xyz" in probe_errors[0]
    assert errors == probe_errors
    assert event_log.errors == probe_errors


# ---------------------------------------------------------------------------
# build_devices(): distance/hopper-level fallback
# ---------------------------------------------------------------------------


def test_build_devices_distance_import_failure_falls_back_to_none(ds, monkeypatch):
    from controller.runtime.devices import build_devices

    settings = _settings()
    settings["modules"]["dist"] = "nonexistent_dist_xyz"
    monkeypatch.setattr(
        "controller.runtime.devices.importlib.import_module",
        _selective_import({"distance.nonexistent_dist_xyz": ModuleNotFoundError}),
    )

    devices, errors = build_devices(settings, errors=[], event_log=_RecordingLogger(), control_log=_RecordingLogger())

    assert len(errors) == 1
    assert "nonexistent_dist_xyz" in errors[0]
    assert type(devices.dist_device).__module__ == "distance.none"
    assert devices.dist_device.get_level() == 100


def test_build_devices_distance_configure_failure_falls_back_to_none(ds, monkeypatch):
    from controller.runtime.devices import build_devices

    settings = _settings()
    settings["modules"]["dist"] = "broken_dist"
    monkeypatch.setattr(
        "controller.runtime.devices.importlib.import_module",
        _selective_import({"distance.broken_dist": _FakeModule(HopperLevel=_RaisingHopperLevel)}),
    )

    devices, errors = build_devices(settings, errors=[], event_log=_RecordingLogger(), control_log=_RecordingLogger())

    assert len(errors) == 1
    assert type(devices.dist_device).__module__ == "distance.none"
    assert devices.dist_device.get_level() == 100


def test_build_devices_distance_failures_do_not_set_critical_error_or_reraise_in_debug_mode(ds, monkeypatch):
    """Unlike grillplat/probes/display, neither distance except block reads
    settings['globals']['debug_mode'] -- a hopper-level sensor failure never
    re-raises even in debug mode, and never touches control['critical_error'].
    This is a real (and, unlike the display bugs below, apparently
    intentional) asymmetry: distance/hopper-level is treated as
    non-critical. Pinned here so a future edit that "fixes" the asymmetry is
    a deliberate choice, not an accident."""
    from controller.runtime.devices import build_devices

    settings = _settings()
    settings["modules"]["dist"] = "nonexistent_dist_xyz"
    settings["globals"]["debug_mode"] = True
    monkeypatch.setattr(
        "controller.runtime.devices.importlib.import_module",
        _selective_import({"distance.nonexistent_dist_xyz": ModuleNotFoundError}),
    )

    devices, errors = build_devices(settings, errors=[], event_log=_RecordingLogger(), control_log=_RecordingLogger())

    assert len(errors) == 1
    assert not read_control().get("critical_error")


# ---------------------------------------------------------------------------
# build_display(): happy path + construction-failure fallback
# ---------------------------------------------------------------------------


def test_build_display_success_real_none_module(ds):
    from controller.runtime.devices import build_display

    settings = _settings()
    display, errors = build_display(settings, errors=[], event_log=_RecordingLogger(), control_log=_RecordingLogger())

    assert errors == []
    assert type(display).__module__ == "display.none"


def test_build_display_configure_failure_falls_back_to_none(ds, monkeypatch):
    """First try succeeds (import + config lookup), so display_config/
    disp_rotation are set correctly; only DisplayModule.Display(...)
    construction fails. Unlike the import-failure path below, the fallback
    here (`from display.none import Display`) is a real, working module."""
    from controller.runtime.devices import build_display

    settings = _settings()
    settings["modules"]["display"] = "broken_display"
    settings["display"] = {"config": {"broken_display": {}}}
    monkeypatch.setattr(
        "controller.runtime.devices.importlib.import_module",
        _selective_import({"display.broken_display": _FakeModule(Display=_RaisingDisplay)}),
    )
    control_log = _RecordingLogger()

    display, errors = build_display(settings, errors=[], event_log=_RecordingLogger(), control_log=control_log)

    assert len(errors) == 1
    assert control_log.exceptions
    assert type(display).__module__ == "display.none"


def test_build_display_configure_failure_debug_mode_reraises(ds, monkeypatch):
    from controller.runtime.devices import build_display

    settings = _settings()
    settings["modules"]["display"] = "broken_display"
    settings["display"] = {"config": {"broken_display": {}}}
    settings["globals"]["debug_mode"] = True
    monkeypatch.setattr(
        "controller.runtime.devices.importlib.import_module",
        _selective_import({"display.broken_display": _FakeModule(Display=_RaisingDisplay)}),
    )

    with pytest.raises(RuntimeError, match="boom: bad display config"):
        build_display(settings, errors=[], event_log=_RecordingLogger(), control_log=_RecordingLogger())


# ---------------------------------------------------------------------------
# build_display(): import-failure fallback -- LATENT BUGS
# ---------------------------------------------------------------------------


def test_build_display_import_failure_falls_back_to_none_module(ds):
    """FIXED (was LATENT BUG, controller/runtime/devices.py:54, MEDIUM
    severity).

    When the *configured* display module fails to import, the except
    handler's own fallback used to be:

        DisplayModule = importlib.import_module("display_none")

    But no top-level "display_none" module exists anywhere in the repo --
    only "display.none" (dotted, in the display/ package) does. So the
    "safe" fallback itself raised ModuleNotFoundError, uncaught, escaping
    build_display() entirely. (Coverage on this except body was 0% before
    this test suite; nothing in the existing test/production code path had
    ever exercised it.) FIX: the fallback now imports "display.none" (the
    real module, same dotted-name convention as the primary import path).
    This test confirms the display process now degrades gracefully to the
    null/none display instead of crashing; see the companion test below for
    the second, compounding bug in the same block (also fixed).
    """
    from controller.runtime.devices import build_display

    settings = _settings()
    settings["modules"]["display"] = "nonexistent_display_xyz"
    control_log = _RecordingLogger()

    display, errors = build_display(settings, errors=[], event_log=_RecordingLogger(), control_log=control_log)

    assert len(errors) == 1
    assert "nonexistent_display_xyz" in errors[0]
    assert control_log.exceptions
    assert type(display).__module__ == "display.none"


def test_build_display_import_failure_debug_mode_reraises_original_error(ds, monkeypatch):
    """Covers the debug_mode branch (line 65-66) in isolation: with
    "display_none" mocked to resolve (so bug #1 above doesn't mask it), a
    bare `raise` inside the except block re-raises the *original*
    exception -- the primary display module's import failure -- not
    whatever "display_none" did. Since the re-raise happens before control
    ever reaches the second try block, this path escapes before latent bug
    #2 (disp_rotation) would otherwise fire, and the caller sees the true
    root cause (the configured display module doesn't exist).
    """
    from controller.runtime.devices import build_display

    monkeypatch.setattr(
        "controller.runtime.devices.importlib.import_module",
        _selective_import(
            {
                "display.nonexistent_display_xyz": ModuleNotFoundError,
                "display_none": _FakeModule(Display=_RaisingDisplay),
            }
        ),
    )
    settings = _settings()
    settings["modules"]["display"] = "nonexistent_display_xyz"
    settings["globals"]["debug_mode"] = True
    errors = []

    with pytest.raises(ModuleNotFoundError, match="nonexistent_display_xyz"):
        build_display(settings, errors=errors, event_log=_RecordingLogger(), control_log=_RecordingLogger())

    assert len(errors) == 1


def test_build_display_import_failure_uses_default_display_config_and_rotation(ds, monkeypatch):
    """FIXED (was LATENT BUG #2, controller/runtime/devices.py:71 and :85,
    MEDIUM severity, compounded the bug above).

    Isolates the failure that used to surface once bug #1 (the
    "display_none" name) was fixed, by monkeypatching that import to
    resolve (now via the real "display.none" name). Previously: the except
    block never assigned `display_config` / `disp_rotation` as fallback
    values before falling through to the code below it that reads them, so
    execution crashed with UnboundLocalError on `disp_rotation` while
    evaluating the primary Display(...) call -- caught by the *next* bare
    except -- then crashed AGAIN on the same unbound name while evaluating
    the fallback Display(...) call, this time escaping uncaught.

    FIX: the except block now assigns `display_config = {}` and
    `disp_rotation = 0` as sensible defaults. This test captures the exact
    kwargs passed into the (mocked) display module's `Display(...)`
    constructor -- `rotation=0`, `config={}` -- instead of crashing, and
    confirms the error was still recorded into `errors` along the way.
    """
    from controller.runtime.devices import build_display

    captured = {}

    class _CapturingDisplay:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "controller.runtime.devices.importlib.import_module",
        _selective_import(
            {
                "display.nonexistent_display_xyz": ModuleNotFoundError,
                "display.none": _FakeModule(Display=_CapturingDisplay),
            }
        ),
    )
    settings = _settings()
    settings["modules"]["display"] = "nonexistent_display_xyz"
    errors = []

    display, errors = build_display(
        settings, errors=errors, event_log=_RecordingLogger(), control_log=_RecordingLogger()
    )

    # The except block's own error-recording (lines 55-64) ran.
    assert len(errors) == 1
    assert "nonexistent_display_xyz" in errors[0]
    # display_config/disp_rotation got sensible defaults instead of being
    # unbound.
    assert captured["rotation"] == 0
    assert captured["config"] == {}
