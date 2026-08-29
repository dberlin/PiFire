import math
from collections.abc import Callable
from types import SimpleNamespace

import pytest

import controller.mpc as mpc_module
import controller.mpc_core as mpc_core_module
import controller.runtime.runner as runner_module
from common import controller_deps
from controller.base import ControllerBase, ControllerLearningDiagnostics
from controller.model_learning.contracts import CandidateOrigin, FrameObservation
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


def test_selected_mpc_core_missing_learning_capability_is_inactive(
    monkeypatch,
) -> None:
    class IncompleteMpcCore(ControllerBase):
        def __init__(
            self,
            config,
            units,
            cycle_data,
            *,
            activation_persistence=None,
            trajectory_repository=None,
            fit_partition_digest=None,
            grey_learning_process=None,
            logger=None,
        ):
            del (
                activation_persistence,
                trajectory_repository,
                fit_partition_digest,
                grey_learning_process,
            )
            super().__init__(config, units, cycle_data, logger=logger)

        def wants_async(self):
            return True

    monkeypatch.setattr(
        runner_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(Controller=IncompleteMpcCore),
    )
    logger = _Logger()

    core, status = _build_core(
        _settings(),
        _control(),
        logger=logger,
    )

    assert core is None
    assert status == "Inactive"
    assert any("missing required MPC learning capability" in message for message in logger.exceptions)


def test_complete_mpc_learning_capability_builds_active(monkeypatch) -> None:
    calls = []

    class CompleteMpcCore(ControllerBase):
        def __init__(
            self,
            config,
            units,
            cycle_data,
            *,
            activation_persistence=None,
            trajectory_repository=None,
            fit_partition_digest=None,
            grey_learning_process=None,
            logger=None,
        ):
            del (
                activation_persistence,
                trajectory_repository,
                fit_partition_digest,
                grey_learning_process,
            )
            super().__init__(config, units, cycle_data, logger=logger)

        def wants_async(self):
            return True

        def estimator_seed_requirements(self) -> tuple[float, int]:
            calls.append(("estimator_seed_requirements",))
            return 60.0, 8

        def bind_estimator_seed_source(
            self,
            source: Callable[[float, int], object] | None,
        ) -> None:
            calls.append(("bind_estimator_seed_source", source))

        def bind_learning_identity(
            self,
            session_id: str,
            cook_id: str | None,
            role_generation: int,
        ) -> None:
            calls.append(
                (
                    "bind_learning_identity",
                    session_id,
                    cook_id,
                    role_generation,
                )
            )

        def observe_frame(self, observation: FrameObservation) -> object:
            calls.append(("observe_frame", observation))
            return object()

        def observation_failure(
            self,
            observation: FrameObservation,
            error: BaseException,
        ) -> object:
            calls.append(("observation_failure", observation, error))
            return object()

        def poll_learning_off_path(
            self,
            *,
            live_origin: CandidateOrigin | None = None,
        ) -> object:
            calls.append(("poll_learning_off_path", live_origin))
            return object()

        def schedule_corpus_fit(self, origin: CandidateOrigin) -> bool:
            calls.append(("schedule_corpus_fit", origin))
            return True

        def _schedule_corpus_fit_ticket(
            self,
            origin: CandidateOrigin,
        ) -> str | None:
            calls.append(("_schedule_corpus_fit_ticket", origin))
            return "fit-ticket"

        def _consume_terminal_corpus_fit_ticket(
            self,
            ticket: str,
            origin: CandidateOrigin,
        ) -> bool:
            calls.append(
                (
                    "_consume_terminal_corpus_fit_ticket",
                    ticket,
                    origin,
                )
            )
            return True

        def fail_corpus_fit(
            self,
            ticket: str,
            error: BaseException | str,
        ) -> None:
            calls.append(("fail_corpus_fit", ticket, error))

        def get_learning_diagnostics(self) -> ControllerLearningDiagnostics:
            calls.append(("get_learning_diagnostics",))
            return ControllerLearningDiagnostics(schema_version=1, state={})

    monkeypatch.setattr(
        runner_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(Controller=CompleteMpcCore),
    )

    core, status = _build_core(_settings(), _control())

    assert core is not None
    assert status == "Active"
    assert calls == []


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
