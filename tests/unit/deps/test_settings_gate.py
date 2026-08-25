"""Settings selection uses acados native readiness; runtime fallback stays non-persistent."""

from types import SimpleNamespace

import pytest

from common import controller_deps as cd
from common.control_trace import ControllerType
from controller.runtime import runner as runner_module

REBUILD = "./rebuild-acados.sh --if-needed"


def _settings(selected, config=None):
    return {"controller": {"selected": selected, "config": {selected: config if config is not None else {}}}}


def _native_failure(monkeypatch, detail):
    def fail():
        raise RuntimeError(f"{detail}. Run {REBUILD}")

    monkeypatch.setattr(cd, "load_native", fail, raising=False)


@pytest.mark.parametrize("detail", ("native publication is missing", "native ABI mismatch: expected 2, got 1"))
def test_selecting_mpc_without_a_ready_native_release_is_refused_with_rebuild_guidance(monkeypatch, detail):
    _native_failure(monkeypatch, detail)

    message = cd.guard_controller_selection(_settings("mpc", {"n_horizon": 12}))

    assert message is not None
    assert detail in message
    assert REBUILD in message
    assert "controller is unchanged" in message.lower()


def test_selecting_mpc_succeeds_only_after_the_native_loader_is_ready(monkeypatch):
    loaded = object()
    calls = []
    monkeypatch.setattr(cd, "load_native", lambda: calls.append(loaded) or loaded, raising=False)

    assert cd.guard_controller_selection(_settings("mpc", {"n_horizon": 12})) is None
    assert calls == [loaded]


def test_non_mpc_selection_does_not_probe_the_native_loader(monkeypatch):
    def unexpected():
        raise AssertionError("PID selection must not probe acados")

    monkeypatch.setattr(cd, "load_native", unexpected, raising=False)

    assert cd.guard_controller_selection(_settings("pid", {"PB": 60.0})) is None


def test_a_malformed_settings_tree_is_not_the_availability_gates_problem(monkeypatch):
    def unexpected():
        raise AssertionError("malformed unrelated saves must not probe acados")

    monkeypatch.setattr(cd, "load_native", unexpected, raising=False)

    assert cd.guard_controller_selection({}) is None
    assert cd.guard_controller_selection({"controller": None}) is None


class _PidCore:
    def __init__(self, *_args, logger=None):
        self.target = None

    def set_target(self, target):
        self.target = target

    def wants_async(self):
        return False

    def get_control_period(self):
        return 5.0


@pytest.mark.parametrize("detail", ("native publication is missing", "native ABI mismatch: expected 2, got 1"))
def test_native_construction_failure_falls_back_to_pid_with_guidance_without_rewriting_selection(monkeypatch, detail):
    settings = {
        "controller": {
            "selected": "mpc",
            "config": {"mpc": {"n_horizon": 12}, "pid": {"PB": 60.0}},
        },
        "globals": {"units": "F"},
        "cycle_data": {"u_max": 0.9},
    }
    original_selection = settings["controller"]["selected"]
    _native_failure(monkeypatch, detail)
    banners = []

    class BrokenMpc:
        def __init__(self, *_args, logger=None):
            raise RuntimeError(f"native ABI mismatch. Run {REBUILD}")

    modules = {
        "controller.mpc": SimpleNamespace(Controller=BrokenMpc),
        "controller.pid": SimpleNamespace(Controller=_PidCore),
    }
    monkeypatch.setattr(runner_module.importlib, "import_module", modules.__getitem__)
    monkeypatch.setattr(runner_module, "_raise_banner", lambda text, logger=None: banners.append(text))

    built, status = runner_module.build_runner(settings, {"primary_setpoint": 225})

    assert status == "Active"
    assert built is not None
    assert built.controller_type() is ControllerType.PID
    assert settings["controller"]["selected"] == original_selection == "mpc"
    assert "selection has not been changed" in banners[-1]
    assert detail in banners[-1]
    assert REBUILD in banners[-1]
