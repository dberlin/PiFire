import builtins
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


_PROVIDER_MODULES = {
    "mpc": "controller.model_learning.report",
    "pid_sp": "controller.pid_sp_learning",
}


def _dispatcher():
    from controller.learning_report import controller_learning_report_revision

    return controller_learning_report_revision


@pytest.fixture
def isolated_providers(monkeypatch):
    calls: list[str] = []
    mpc_provider = ModuleType(_PROVIDER_MODULES["mpc"])
    pid_sp_provider = ModuleType(_PROVIDER_MODULES["pid_sp"])

    def mpc_revision():
        calls.append("mpc")
        return "mpc-revision"

    def pid_sp_report():
        calls.append("pid_sp")
        return SimpleNamespace(revision="pid-sp-revision")

    mpc_provider.learning_report_revision = mpc_revision
    pid_sp_provider.backend_pid_sp_learning_report = pid_sp_report
    monkeypatch.setitem(sys.modules, _PROVIDER_MODULES["mpc"], mpc_provider)
    monkeypatch.setitem(sys.modules, _PROVIDER_MODULES["pid_sp"], pid_sp_provider)
    return calls, mpc_provider, pid_sp_provider


def test_importing_dispatcher_does_not_import_either_provider(monkeypatch):
    monkeypatch.delitem(sys.modules, "controller.learning_report", raising=False)
    imported_providers: list[str] = []
    real_import = builtins.__import__

    def tracked_import(name, *args, **kwargs):
        if name in _PROVIDER_MODULES.values():
            imported_providers.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracked_import)

    importlib.import_module("controller.learning_report")

    assert imported_providers == []


@pytest.mark.parametrize(
    ("controller_name", "expected_revision", "expected_calls"),
    [
        ("mpc", "mpc-revision", ["mpc"]),
        ("pid_sp", "pid-sp-revision", ["pid_sp"]),
    ],
)
def test_supported_controller_delegates_only_to_its_provider(
    isolated_providers, controller_name, expected_revision, expected_calls
):
    calls, _mpc_provider, _pid_sp_provider = isolated_providers

    assert _dispatcher()(controller_name) == expected_revision
    assert calls == expected_calls


@pytest.mark.parametrize("controller_name", ["pid", "unknown", "", None])
def test_unsupported_controller_returns_none_without_touching_providers(isolated_providers, controller_name):
    calls, _mpc_provider, _pid_sp_provider = isolated_providers

    assert _dispatcher()(controller_name) is None
    assert calls == []


@pytest.mark.parametrize("controller_name", ["mpc", "pid_sp"])
def test_provider_failure_does_not_fall_through_to_the_other_provider(monkeypatch, isolated_providers, controller_name):
    calls, mpc_provider, pid_sp_provider = isolated_providers

    def fail_mpc():
        calls.append("mpc")
        raise RuntimeError("mpc report unavailable")

    def fail_pid_sp():
        calls.append("pid_sp")
        raise RuntimeError("pid-sp report unavailable")

    mpc_provider.learning_report_revision = fail_mpc
    pid_sp_provider.backend_pid_sp_learning_report = fail_pid_sp

    with pytest.raises(RuntimeError, match="report unavailable"):
        _dispatcher()(controller_name)

    assert calls == [controller_name]
