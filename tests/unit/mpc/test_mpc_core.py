from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
import pytest
import controller.mpc_core as mpc_core_module

from controller.acados import GreyBoxMPCConfig, SolverDiagnostics, SolverError
from controller.applied_output import AppliedOutput, OutputSource
from controller.base import MpcFailureState
from controller.mpc_config import (
    DEFAULT_MPC_CONFIG,
    JsonValue,
    ModelMetadata,
    finite_float,
    model_is_identified,
    normalize_config,
    optional_float,
    sanitized_copy,
    to_celsius,
    warn_about_model,
)
from controller.mpc_core import MpcCore, MpcSolver, MpcStep


U_MAX = 0.9
CYCLE: dict[str, JsonValue] = {"u_min": 0.1, "u_max": U_MAX}
CONFIG = dict(
    DEFAULT_MPC_CONFIG,
    n_horizon=5,
    control_period=1.0,
    enable_fan_input=True,
)


class FakeEstimator:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float]] = []
        self.closed = 0
        self.state = np.array([0.1] * 8 + [72.0, 0.03], dtype=float)

    def update(
        self,
        normalized_combustion_load: float,
        y_measured: float,
    ) -> npt.NDArray[np.float64]:
        self.calls.append((normalized_combustion_load, y_measured))
        return self.state.copy()

    def close(self) -> None:
        self.closed += 1


class NonClosableEstimator:
    def update(
        self,
        normalized_combustion_load: float,
        y_measured: float,
    ) -> npt.NDArray[np.float64]:
        del normalized_combustion_load, y_measured
        return np.zeros(10, dtype=float)



class FakeEstimatorFactory:
    def __init__(self, estimator: FakeEstimator | None = None) -> None:
        self.estimator = FakeEstimator() if estimator is None else estimator
        self.calls: list[dict[str, float | int | None]] = []

    def __call__(
        self,
        *,
        C_c: float,
        h_amb: float,
        T_amb: float,
        t_step: float,
        q_temp: float,
        q_dist: float,
        r_meas: float,
        theta: float,
        n_delay: int,
        K_Q: float,
        sigma: float | None = None,
    ) -> FakeEstimator:
        self.calls.append(
            {
                "C_c": C_c,
                "h_amb": h_amb,
                "T_amb": T_amb,
                "t_step": t_step,
                "q_temp": q_temp,
                "q_dist": q_dist,
                "r_meas": r_meas,
                "theta": theta,
                "n_delay": n_delay,
                "K_Q": K_Q,
                "sigma": sigma,
            }
        )
        return self.estimator


@dataclass(frozen=True, slots=True)
class FakeDiagnostics:
    status: int | float | bool = 0
    backend_status: int | float | bool = 0
    iterations: int | float | bool = 2
    solve_time_s: int | float = 0.001
    objective: int | float = 3.0
    kkt_residual: int | float = 1e-7
    constraint_residual: int | float = 0.0
    warm_started: bool | int = True


@dataclass(frozen=True, slots=True)
class FakeSolve:
    sequence_q: npt.NDArray[np.float64]
    sequence_residual: npt.NDArray[np.float64]
    objective: int | float
    diagnostics: FakeDiagnostics


type FakeSolverResult = FakeSolve | BaseException


def solve_result(
    length: int,
    first: float,
    *,
    sequence_residual: npt.NDArray[np.float64] | None = None,
    objective: int | float = 3.0,
    diagnostics: FakeDiagnostics | None = None,
) -> FakeSolve:
    return FakeSolve(
        sequence_q=np.array(
            [first] + [first / 2.0] * (length - 1),
            dtype=float,
        ),
        sequence_residual=(
            np.zeros(length, dtype=float)
            if sequence_residual is None
            else sequence_residual
        ),
        objective=objective,
        diagnostics=FakeDiagnostics() if diagnostics is None else diagnostics,
    )


class FakeSolver:
    def __init__(
        self,
        config: GreyBoxMPCConfig,
        results: tuple[FakeSolverResult, ...] = (),
    ) -> None:
        self.config = config
        self.results = list(results)
        self.calls: list[
            tuple[npt.NDArray[np.float64], float | int, float | int, float | int]
        ] = []
        self.closed = 0

    def solve(
        self,
        state: npt.ArrayLike,
        *,
        setpoint_c: float | int,
        q_previous: float | int,
        equilibrium_q: float | int,
    ) -> FakeSolve:
        self.calls.append(
            (
                np.asarray(state, dtype=float).copy(),
                setpoint_c,
                q_previous,
                equilibrium_q,
            )
        )
        result = (
            self.results.pop(0)
            if self.results
            else solve_result(self.config.horizon_steps, 0.5)
        )
        if isinstance(result, BaseException):
            raise result
        return result

    def close(self) -> None:
        self.closed += 1


class FailingCloseSolver(FakeSolver):
    def close(self) -> None:
        self.closed += 1
        raise RuntimeError("solver close failed")



class FakeSolverFactory:
    def __init__(self, results: tuple[FakeSolverResult, ...] = ()) -> None:
        self.results = results
        self.solver: FakeSolver | None = None

    def __call__(self, config: GreyBoxMPCConfig) -> FakeSolver:
        self.solver = FakeSolver(config, self.results)
        return self.solver


def _authorized() -> bool:
    return True


def _identity_adjustment(load: float, _temperature_c: float) -> float:
    return load


def _configured_authority() -> tuple[int, ModelMetadata | None]:
    return 0, None


def _ignore_failure(_error: BaseException) -> None:
    return None


def make_core(
    *,
    results: tuple[FakeSolverResult, ...] = (),
    config: dict[str, JsonValue] | None = None,
    units: str = "C",
    authorized: Callable[[], bool] = _authorized,
    adjust_load: Callable[[float, float], float] = _identity_adjustment,
    authority: Callable[[], tuple[int, ModelMetadata | None]] = _configured_authority,
    on_failure: Callable[[BaseException], None] = _ignore_failure,
) -> tuple[MpcCore, FakeEstimator, FakeSolver]:
    estimator_factory = FakeEstimatorFactory()
    solver_factory = FakeSolverFactory(results)
    core = MpcCore(
        dict(CONFIG if config is None else config),
        units,
        dict(CYCLE),
        output_authorized=authorized,
        adjust_load=adjust_load,
        model_authority=authority,
        on_policy_failure=on_failure,
        ekf_factory=estimator_factory,
        kf_factory=estimator_factory,
        solver_factory=solver_factory,
    )
    core.set_target(110.0)
    assert solver_factory.solver is not None
    return core, estimator_factory.estimator, solver_factory.solver


def test_config_helpers_cover_absent_invalid_nonfinite_and_warning_branches(capsys):
    supplied: dict[str, JsonValue] = {
        "control_period": 2.0,
        "feed_forward": 9,
        "extra": "kept",
    }
    normalized = normalize_config(supplied)

    assert normalized is not supplied
    assert normalized["control_period"] == 2.0
    assert normalized["extra"] == "kept"
    assert "feed_forward" not in normalized
    assert normalize_config(None) == DEFAULT_MPC_CONFIG
    assert to_celsius(212.0, "F") == pytest.approx(100.0)
    assert to_celsius(100.0, "C") == 100.0
    assert finite_float(float("nan")) is None
    assert finite_float(2) == 2.0
    assert optional_float(None) is None
    assert optional_float("bad") is None
    assert optional_float(float("inf")) is None
    assert optional_float("2.5") == 2.5
    assert optional_float([]) is None
    assert optional_float({"not": "numeric"}) is None
    assert sanitized_copy(
        {"finite": 1.5, "bad": float("inf"), "integer": 2}
    ) == {"finite": 1.5, "bad": None, "integer": 2}

    assert model_is_identified(DEFAULT_MPC_CONFIG) is False
    assert model_is_identified(dict(DEFAULT_MPC_CONFIG, C_c=321.0)) is True
    assert model_is_identified(DEFAULT_MPC_CONFIG, {"rmse": 1.0}) is True
    warn_about_model(dict(DEFAULT_MPC_CONFIG, C_f=1.0))
    warning = capsys.readouterr().out
    assert "ignoring C_f" in warning
    assert "model is uncalibrated" in warning

    warn_about_model(dict(DEFAULT_MPC_CONFIG, C_c=321.0))
    assert capsys.readouterr().out == ""


def test_construction_selects_estimators_validates_delay_and_closes_partial_build():
    factory = FakeEstimatorFactory()
    for kind in ("ekf", "kf"):
        core = MpcCore(
            dict(CONFIG, estimator=kind),
            "C",
            dict(CYCLE),
            ekf_factory=factory,
            kf_factory=factory,
            solver_factory=FakeSolver,
        )
        core.close()
    assert factory.calls[0]["sigma"] == CONFIG["sigma"]
    assert factory.calls[1]["sigma"] is None

    with pytest.raises(ValueError, match="exactly eight"):
        MpcCore(dict(CONFIG, n_delay=7), "C", dict(CYCLE))
    with pytest.raises(ValueError, match="estimator must"):
        MpcCore(
            dict(CONFIG, estimator="mhe"),
            "C",
            dict(CYCLE),
            ekf_factory=factory,
            kf_factory=factory,
            solver_factory=FakeSolver,
        )

    estimator = FakeEstimator()
    failing_factory = FakeEstimatorFactory(estimator)

    def fail_solver(config: GreyBoxMPCConfig) -> MpcSolver:
        del config
        raise RuntimeError("native unavailable")

    with pytest.raises(RuntimeError, match="native unavailable"):
        MpcCore(
            dict(CONFIG),
            "C",
            dict(CYCLE),
            ekf_factory=failing_factory,
            solver_factory=fail_solver,
        )
    assert estimator.closed == 1


def test_success_uses_applied_feedback_first_native_command_and_allocates_fan():
    core, estimator, solver = make_core(results=(solve_result(5, 0.625),))
    core.set_output(AppliedOutput(0.45, OutputSource.CONTROLLER, 1.0))

    step = core.update(74.0)

    assert isinstance(step, MpcStep)
    assert estimator.calls == [(0.5, 74.0)]
    state, setpoint, previous, equilibrium = solver.calls[0]
    assert state == pytest.approx(estimator.state)
    assert setpoint == 110.0
    assert previous == 0.5
    assert equilibrium == 0.0
    assert step.cycle_ratio == pytest.approx(0.625 * U_MAX)
    assert step.fan["duty"] is not None
    assert step.diagnostics.failure_state is MpcFailureState.SUCCESS
    assert step.diagnostics.applied_combustion_load == 0.5
    assert step.baseline_allocation is step.allocation


def test_units_authority_equilibrium_adjustment_and_snapshot_are_explicit_inputs():
    adjusted: list[tuple[float, float]] = []

    def authority() -> tuple[int, ModelMetadata | None]:
        return 7, {"rmse": 0.5}

    def adjust(load: float, temperature_c: float) -> float:
        adjusted.append((load, temperature_c))
        return load + 0.2

    core, _estimator, solver = make_core(
        results=(solve_result(5, 0.4),),
        config=dict(CONFIG, C_c=321.0, enable_fan_input=False),
        units="F",
        adjust_load=adjust,
        authority=authority,
    )
    core.set_target(212.0)

    step = core.update(194.0)

    assert solver.calls[0][1] == pytest.approx(100.0)
    assert float(solver.calls[0][3]) > 0.0
    assert adjusted == [(0.4, pytest.approx(90.0))]
    assert step.baseline_allocation.normalized_combustion_load == pytest.approx(0.4)
    assert step.allocation.normalized_combustion_load == pytest.approx(0.6)
    assert step.fan == {"duty": None}
    assert step.diagnostics.model_revision == 7
    assert step.diagnostics.model_provenance == "adopted"
    assert core.snapshot_parameters() == {
        key: core.config[key]
        for key in ("C_c", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")
    }
    assert core.snapshot_parameters() is not core.config


@pytest.mark.parametrize(
    "invalid",
    [
        solve_result(4, 0.5),
        solve_result(5, 0.5, sequence_residual=np.zeros(4)),
        solve_result(5, float("nan")),
        solve_result(
            5,
            0.5,
            sequence_residual=np.array([0.0, 0.0, np.inf, 0.0, 0.0]),
        ),
        solve_result(5, 1.01),
        solve_result(5, 0.5, objective=float("inf")),
        solve_result(5, 0.5, diagnostics=FakeDiagnostics(status=1)),
        solve_result(5, 0.5, diagnostics=FakeDiagnostics(backend_status=1)),
        solve_result(5, 0.5, diagnostics=FakeDiagnostics(iterations=True)),
        solve_result(5, 0.5, diagnostics=FakeDiagnostics(iterations=1.5)),
        solve_result(5, 0.5, diagnostics=FakeDiagnostics(iterations=-1)),
        solve_result(5, 0.5, diagnostics=FakeDiagnostics(solve_time_s=-1.0)),
        solve_result(
            5,
            0.5,
            diagnostics=FakeDiagnostics(objective=float("nan")),
        ),
        solve_result(
            5,
            0.5,
            diagnostics=FakeDiagnostics(kkt_residual=float("inf")),
        ),
        solve_result(
            5,
            0.5,
            diagnostics=FakeDiagnostics(constraint_residual=-1.0),
        ),
        solve_result(5, 0.5, diagnostics=FakeDiagnostics(warm_started=1)),
    ],
)
def test_every_malformed_native_result_holds_the_last_safe_command(
    invalid: FakeSolve,
):
    core, _estimator, _solver = make_core(
        results=(solve_result(5, 0.6), invalid)
    )
    core.update(72.0)

    step = core.update(73.0)

    assert step.diagnostics.bounded_firing_load == pytest.approx(0.6)
    assert step.diagnostics.raw_policy_firing_load is None
    assert step.diagnostics.equilibrium_feed_forward is None
    assert step.diagnostics.residual_move is None
    assert step.diagnostics.failure_state is MpcFailureState.POLICY_EXCEPTION
    assert step.diagnostics.consecutive_policy_failures == 1


def test_solver_exception_holds_reports_diagnostics_counts_and_recovers(capsys):
    native_diagnostics = SolverDiagnostics(
        status=4,
        backend_status=7,
        iterations=10,
        solve_time_s=0.02,
        objective=1.0,
        kkt_residual=0.2,
        constraint_residual=0.1,
        warm_started=True,
    )
    failures: list[BaseException] = []
    core, _estimator, _solver = make_core(
        results=(
            solve_result(5, 0.7),
            SolverError("native solve failed", native_diagnostics),
            solve_result(5, 0.4),
        ),
        on_failure=failures.append,
    )
    core.update(70.0)

    failed = core.update(71.0)
    recovered = core.update(72.0)

    assert failed.diagnostics.bounded_firing_load == pytest.approx(0.7)
    assert failed.diagnostics.consecutive_policy_failures == 1
    assert core.native_failure_diagnostics is None
    assert recovered.diagnostics.consecutive_policy_failures == 0
    assert failures and isinstance(failures[0], SolverError)
    output = capsys.readouterr().out
    assert "failed 1 consecutive" in output
    assert "recovered after 1 failed" in output


def test_authorization_denial_precedes_estimation_and_closed_core_rejects_rebind():
    def unauthorized() -> bool:
        return False

    core, estimator, solver = make_core(authorized=unauthorized)
    with pytest.raises(RuntimeError, match="not durably authorized"):
        core.update(72.0)
    assert estimator.calls == []
    assert solver.calls == []

    core.close()
    with pytest.raises(RuntimeError, match="closed MPC core"):
        core.bind_resources(estimator, solver, lambda: None)


def test_roundoff_clips_adjustment_bounds_and_resource_close_is_idempotent():
    result = solve_result(
        5,
        -1e-12,
        sequence_residual=np.array(
            [-1e-12, 0.0, 0.5, 1.0, 1.0 + 1e-12]
        ),
        diagnostics=replace(FakeDiagnostics(), constraint_residual=1e-12),
    )

    def excessive_adjustment(_load: float, _temperature_c: float) -> float:
        return 2.0

    core, estimator, solver = make_core(
        results=(result,),
        adjust_load=excessive_adjustment,
    )

    step = core.update(72.0)
    core.close()
    core.close()

    assert step.baseline_allocation.normalized_combustion_load == 0.0
    assert step.allocation.normalized_combustion_load == 1.0
    assert estimator.closed == 1
    assert solver.closed == 1


def test_close_attempts_every_owned_handle_once_when_one_close_fails():
    estimator_factory = FakeEstimatorFactory()
    solver_box: list[FailingCloseSolver] = []

    def build_solver(config: GreyBoxMPCConfig) -> FailingCloseSolver:
        solver = FailingCloseSolver(config)
        solver_box.append(solver)
        return solver

    core = MpcCore(
        dict(CONFIG),
        "C",
        dict(CYCLE),
        ekf_factory=estimator_factory,
        solver_factory=build_solver,
    )

    with pytest.raises(RuntimeError, match="complete grey numerical pair"):
        core.close()
    core.close()

    assert solver_box[0].closed == 1
    assert estimator_factory.estimator.closed == 1


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (dict(CONFIG, C_c=True), "C_c must be a finite number"),
        (dict(CONFIG, C_c=float("inf")), "C_c must be a finite number"),
        (dict(CONFIG, n_delay=8.0), "n_delay must be an integer"),
        (dict(CONFIG, estimator=5), "estimator must be a string"),
        (dict(CONFIG, enable_fan_input=2), "enable_fan_input must be a boolean"),
        (
            dict(CONFIG, enable_online_adaptation="yes"),
            "enable_online_adaptation must be a boolean",
        ),
    ],
)
def test_normalization_rejects_unsound_runtime_setting_types(
    config: dict[str, JsonValue],
    message: str,
):
    with pytest.raises(ValueError, match=message):
        normalize_config(config)


def test_normalization_preserves_legacy_zero_one_booleans_as_actual_booleans():
    enabled = normalize_config(
        dict(CONFIG, enable_fan_input=1, enable_online_adaptation=1)
    )
    disabled = normalize_config(
        dict(CONFIG, enable_fan_input=0, enable_online_adaptation=0)
    )

    assert enabled["enable_fan_input"] is True
    assert enabled["enable_online_adaptation"] is True
    assert disabled["enable_fan_input"] is False
    assert disabled["enable_online_adaptation"] is False

    core, _estimator, _solver = make_core(
        config=dict(CONFIG, enable_fan_input=1),
        results=(solve_result(5, 0.5),),
    )
    assert core.update(72.0).fan["duty"] is not None




def test_public_factories_reject_unvalidated_direct_caller_settings():
    estimator_factory = FakeEstimatorFactory()

    with pytest.raises(ValueError, match="C_c must be numeric"):
        MpcCore.build_estimator(
            dict(CONFIG, C_c=True),
            8,
            ekf_factory=estimator_factory,
        )
    with pytest.raises(ValueError, match="n_delay must be an integer"):
        MpcCore.build_components(
            dict(CONFIG, n_delay=8.0),
            ekf_factory=estimator_factory,
            solver_factory=FakeSolver,
        )
    with pytest.raises(ValueError, match="estimator must be a string"):
        MpcCore.build_estimator(
            dict(CONFIG, estimator=5),
            8,
            ekf_factory=estimator_factory,
        )


def test_partial_build_accepts_nonclosable_estimator_without_masking_solver_error():
    estimator = NonClosableEstimator()

    def build_estimator(
        *,
        C_c: float,
        h_amb: float,
        T_amb: float,
        t_step: float,
        q_temp: float,
        q_dist: float,
        r_meas: float,
        theta: float,
        n_delay: int,
        K_Q: float,
        sigma: float,
    ) -> NonClosableEstimator:
        del C_c, h_amb, T_amb, t_step, q_temp, q_dist
        del r_meas, theta, n_delay, K_Q, sigma
        return estimator

    def fail_solver(config: GreyBoxMPCConfig) -> MpcSolver:
        del config
        raise RuntimeError("native unavailable")

    with pytest.raises(RuntimeError, match="native unavailable"):
        MpcCore(
            dict(CONFIG),
            "C",
            dict(CYCLE),
            ekf_factory=build_estimator,
            solver_factory=fail_solver,
        )


def test_default_callbacks_repeat_failure_without_repeat_log_and_expose_state(capsys):
    estimator_factory = FakeEstimatorFactory()
    solver_factory = FakeSolverFactory(
        (RuntimeError("first"), RuntimeError("second"))
    )
    core = MpcCore(
        dict(CONFIG),
        "C",
        dict(CYCLE),
        ekf_factory=estimator_factory,
        solver_factory=solver_factory,
    )
    core.set_target(100.0)

    first = core.update(72.0)
    second = core.update(73.0)

    assert first.diagnostics.consecutive_policy_failures == 1
    assert second.diagnostics.consecutive_policy_failures == 2
    assert capsys.readouterr().out.count("native solver has failed") == 1
    assert core.estimator is estimator_factory.estimator
    assert core.solver is solver_factory.solver
    assert core.set_point_c == 100.0
    assert core.applied_combustion_load == 0.0
    assert core.last_combustion_load == 0.0
    assert core.last_raw_combustion_load is None
    assert core.last_equilibrium_load is None
    assert core.last_residual_load is None
    assert core.last_feasibility is not None
    assert core.estimate is not None
    assert core.consecutive_policy_failures == 2
    assert core.native_failure_diagnostics is None
    assert len(core.history) == 2
    core.clear_estimate()
    assert core.estimate is None


def test_rebinding_preserves_or_resets_estimate_and_closes_transferred_pair_once():
    core, estimator, solver = make_core(results=(solve_result(5, 0.4),))
    core.update(72.0)
    assert core.estimate is not None
    closed: list[str] = []

    def close_pair() -> None:
        closed.append("pair")
        estimator.close()
        solver.close()

    core.bind_resources(estimator, solver, close_pair)
    assert core.estimate is not None
    core.bind_resources(estimator, solver, close_pair, reset_estimate=True)
    assert core.estimate is None
    core.close()
    core.close()
    assert closed == ["pair"]


def test_default_numerical_factories_use_real_construction_symbols(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(mpc_core_module, "AcadosGreyBoxMPC", FakeSolver)
    solver = MpcCore.build_solver(GreyBoxMPCConfig())
    assert isinstance(solver, FakeSolver)

    ekf = MpcCore.build_estimator(dict(CONFIG, estimator="ekf"), 8)
    kf = MpcCore.build_estimator(dict(CONFIG, estimator="kf"), 8)
    assert ekf is not None
    assert kf is not None
