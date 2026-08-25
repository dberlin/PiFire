import math

import pytest

import controller.mpc as mpc_module
import controller.mpc_core as mpc_core_module
from common import controller_deps
from controller.runtime.runner import SyncControllerRunner, _build_core, build_runner
from tests.characterization import harness  # noqa: F401
from tests.characterization.fixtures import base_settings

_REBUILD = "./rebuild-acados.sh --if-needed"


class _Logger:
    def __init__(self):
        self.exceptions = []
        self.errors = []

    def exception(self, message):
        self.exceptions.append(message)

    def error(self, message):
        self.errors.append(message)


def _settings():
    settings = base_settings()
    settings["controller"]["selected"] = "mpc"
    return settings


def _control():
    return {"primary_setpoint": 225}


@pytest.fixture(
    params=[
        "Native acados library is missing",
        "Native acados build manifest is malformed",
        "Native acados ABI mismatch: expected 2, found 1",
        "Native acados library could not be loaded",
    ]
)
def native_failure(monkeypatch, request):
    detail = f"{request.param}. Run `{_REBUILD}`."

    def fail(*_args, **_kwargs):
        raise RuntimeError(detail)

    monkeypatch.setattr(mpc_core_module, "AcadosGreyBoxMPC", fail)
    monkeypatch.setattr(controller_deps, "load_native", fail)
    return detail


def test_build_core_contains_every_native_construction_failure(native_failure, ds):
    logger = _Logger()
    assert _build_core(_settings(), _control(), logger=logger) == (None, "Inactive")
    assert any("[mpc] controller" in message for message in logger.exceptions)


def test_native_failure_falls_back_to_safe_pid_with_exact_rebuild_guidance(native_failure, ds):
    settings = _settings()
    logger = _Logger()

    runner, status = build_runner(settings, _control(), logger=logger)
    try:
        assert status == "Active"
        assert isinstance(runner, SyncControllerRunner)
        runner.submit(200.0)
        result = runner.latest()
        assert math.isfinite(result.cycle_ratio)
        assert runner.controller_type() == "pid"
        banner = " ".join(logger.errors)
        assert native_failure in banner
        assert _REBUILD in banner
        assert "running the [pid] controller instead" in banner
        assert "selection has not been changed" in banner
        assert settings["controller"]["selected"] == "mpc"
    finally:
        runner.stop()


def test_native_failure_during_reconfigure_keeps_previous_core(native_failure, ds):
    settings = base_settings()
    settings["controller"]["selected"] = "pid"
    core, status = _build_core(settings, _control())
    assert status == "Active"
    runner = SyncControllerRunner(core)
    previous = runner._core
    settings["controller"]["selected"] = "mpc"
    logger = _Logger()

    assert runner.reconfigure(settings, _control(), logger=logger) == "Inactive"
    assert runner._core is previous
    assert native_failure in " ".join(logger.errors)
    assert "previous controller is still running your cook" in " ".join(logger.errors)
    assert settings["controller"]["selected"] == "mpc"
    runner.stop()
