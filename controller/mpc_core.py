"""Single numerical owner for grey-box MPC estimation, solve, and allocation."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

from controller.acados import (
    AcadosGreyBoxMPC,
    GreyBoxMPCConfig,
    SolverDiagnostics,
    SolverError,
)
from controller.applied_output import AppliedOutput
from controller.base import MpcFailureState, MpcTraceDiagnostics
from controller.model_learning.calibration import CalibrationDecision, CalibrationProgress
from controller.model_promotion import FeasibilityReport, feasibility_report
from controller.mpc_allocator import AllocationResult, allocate, normalized_load_from_auger_duty
from controller.mpc_calibration import TemperatureForecast
from controller.mpc_config import (
    MODEL_PARAMETER_KEYS,
    JsonValue,
    ModelMetadata,
    model_is_identified,
    normalize_config,
    to_celsius,
)
from controller.mpc_model import (
    EstimatorSeed,
    GreyBoxEKF,
    GreyBoxKF,
    steady_combustion_load,
)
from controller.runtime.context import EVENT_LOG_NAME

_NATIVE_BOUND_TOLERANCE = 1e-6
_LEARNED_RESIDUAL_WEIGHT = 1_000.0


class NativeDiagnostics(Protocol):
    @property
    def status(self) -> int | float | bool: ...

    @property
    def backend_status(self) -> int | float | bool: ...

    @property
    def iterations(self) -> int | float | bool: ...

    @property
    def solve_time_s(self) -> int | float: ...

    @property
    def objective(self) -> int | float: ...

    @property
    def kkt_residual(self) -> int | float: ...

    @property
    def constraint_residual(self) -> int | float: ...

    @property
    def warm_started(self) -> bool | int: ...


class NativeSolve(Protocol):
    @property
    def sequence_q(self) -> npt.NDArray[np.float64]: ...

    @property
    def sequence_residual(self) -> npt.NDArray[np.float64]: ...

    @property
    def objective(self) -> int | float: ...

    @property
    def diagnostics(self) -> NativeDiagnostics: ...


@runtime_checkable
class MpcEstimator(Protocol):
    def update(
        self,
        normalized_combustion_load: float,
        y_measured: float,
    ) -> npt.NDArray[np.float64]: ...

    def reset(
        self,
        normalized_combustion_load: float,
        measured_temperature: float | None,
        *,
        delay_states: tuple[float, ...] | None = None,
        disturbance: float = 0.0,
    ) -> npt.NDArray[np.float64] | None: ...


@runtime_checkable
class MpcSolver(Protocol):
    config: GreyBoxMPCConfig

    def solve(
        self,
        state: npt.ArrayLike,
        *,
        setpoint_c: float,
        q_previous: float,
        equilibrium_q: float,
    ) -> NativeSolve: ...

    def reset(self) -> None: ...


@runtime_checkable
class Closable(Protocol):
    def close(self) -> None: ...


class EkfFactory(Protocol):
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
        sigma: float,
    ) -> MpcEstimator: ...


class KfFactory(Protocol):
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
    ) -> MpcEstimator: ...


class SolverFactory(Protocol):
    def __call__(self, config: GreyBoxMPCConfig) -> MpcSolver: ...


ModelAuthority = Callable[[], tuple[int, ModelMetadata | None]]
CalibrationAdvance = Callable[[float, float, TemperatureForecast], CalibrationDecision]
PolicyFailureHandler = Callable[[BaseException], None]

_INACTIVE_CALIBRATION = CalibrationDecision(False, 0.0, None, CalibrationProgress())


@dataclass(frozen=True, slots=True)
class MpcStep:
    """Complete immutable numerical result for one controller update."""

    cycle_ratio: float
    fan: dict[str, float | None]
    diagnostics: MpcTraceDiagnostics
    allocation: AllocationResult
    baseline_allocation: AllocationResult


@dataclass(frozen=True, slots=True)
class MpcOperatingState:
    """Complete estimator-bearing state for same-model capture and restore."""

    set_point_c: float
    applied_combustion_load: float
    last_safe_combustion_load: float
    measured_temperature_c: float | None
    delay_states: tuple[float, ...] | None
    disturbance: float


@dataclass(frozen=True, slots=True)
class MpcModelIndependentState:
    """Physical/control state transferable without assuming a model structure."""

    set_point_c: float
    applied_combustion_load: float
    last_safe_combustion_load: float
    measured_temperature_c: float | None


def _authorized() -> bool:
    return True


def _inactive_calibration(
    _load: float,
    _temperature_c: float,
    _forecast: TemperatureForecast,
) -> CalibrationDecision:
    return _INACTIVE_CALIBRATION


def _default_authority() -> tuple[int, ModelMetadata | None]:
    return 0, None


def _ignore_failure(_error: BaseException) -> None:
    return None


def _float_setting(config: Mapping[str, JsonValue], key: str) -> float:
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key} must be numeric")
    return float(value)


def _int_setting(config: Mapping[str, JsonValue], key: str) -> int:
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _str_setting(config: Mapping[str, JsonValue], key: str) -> str:
    value = config[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _close_component(component: MpcEstimator | MpcSolver | None) -> None:
    if isinstance(component, Closable):
        component.close()


class _RetryableResourceClose:
    """Retry only resource callbacks that have not completed successfully."""

    def __init__(self, callbacks: tuple[Callable[[], None], ...]) -> None:
        self._pending = list(callbacks)

    @property
    def complete(self) -> bool:
        return not self._pending

    def close(self) -> None:
        pending: list[Callable[[], None]] = []
        errors: list[BaseException] = []
        for callback in self._pending:
            try:
                callback()
            except BaseException as error:
                pending.append(callback)
                errors.append(error)
        self._pending = pending
        if errors:
            raise RuntimeError("could not close complete grey numerical pair") from errors[0]


class MpcCore:
    """Own the one live estimator/solver path and all state derived from it."""

    def __init__(
        self,
        config: Mapping[str, JsonValue] | None,
        units: str,
        cycle_data: Mapping[str, JsonValue],
        *,
        output_authorized: Callable[[], bool] = _authorized,
        advance_calibration: CalibrationAdvance = _inactive_calibration,
        model_authority: ModelAuthority = _default_authority,
        on_policy_failure: PolicyFailureHandler = _ignore_failure,
        ekf_factory: EkfFactory | None = None,
        kf_factory: KfFactory | None = None,
        solver_factory: SolverFactory | None = None,
        components: tuple[MpcEstimator, MpcSolver] | None = None,
        model_identified: bool | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        #: No ControllerContext reaches the core; the context's logger is
        #: injected instead, defaulting to the name the context defaults to.
        self._logger = logging.getLogger(EVENT_LOG_NAME) if logger is None else logger
        self.config = normalize_config(config)
        self.units = units
        self.u_max = _float_setting(cycle_data, "u_max") if "u_max" in cycle_data else 0.9
        self._output_authorized = output_authorized
        self._advance_calibration = advance_calibration
        self._model_authority = model_authority
        self._on_policy_failure = on_policy_failure

        revision, metadata = model_authority()
        if components is None:
            identified = model_is_identified(self.config, metadata) if model_identified is None else model_identified
            self._estimator, self._solver = self.build_components(
                self.config,
                model_identified=identified,
                ekf_factory=ekf_factory,
                kf_factory=kf_factory,
                solver_factory=solver_factory,
            )
        else:
            self._estimator, self._solver = components
        self._close_resources: _RetryableResourceClose | None = _RetryableResourceClose(
            (
                lambda: _close_component(self._solver),
                lambda: _close_component(self._estimator),
            )
        )
        self._closed = False
        self._set_point_c = 0.0
        self._applied_combustion_load = 0.0
        self._last_combustion_load = 0.0
        self._last_raw_combustion_load: float | None = 0.0
        self._last_equilibrium_load: float | None = None
        self._last_residual_load: float | None = None
        self._last_feasibility: FeasibilityReport | None = None
        self._x_hat: npt.NDArray[np.float64] | None = None
        self._consecutive_policy_failures = 0
        self._native_failure_diagnostics: SolverDiagnostics | None = None
        self._last_measured_temperature_c: float | None = None
        self._trajectory_seed: EstimatorSeed | None = None
        self._seed_anchor_pending = False
        self._model_revision = revision

    @classmethod
    def native_configuration(
        cls,
        config: Mapping[str, JsonValue],
        *,
        model_identified: bool | None = None,
    ) -> GreyBoxMPCConfig:
        """Map normalized runtime settings to the exact generated native contract."""

        n_delay = _int_setting(config, "n_delay")
        if n_delay != 8:
            raise ValueError("the generated grey-box controller requires exactly eight delay states")
        identified = model_is_identified(config) if model_identified is None else model_identified
        return GreyBoxMPCConfig(
            C_c=_float_setting(config, "C_c"),
            h_amb=_float_setting(config, "h_amb"),
            T_amb=_float_setting(config, "T_amb"),
            theta=_float_setting(config, "theta"),
            K_Q=_float_setting(config, "K_Q"),
            sigma=_float_setting(config, "sigma"),
            horizon_steps=_int_setting(config, "n_horizon"),
            delay_states=8,
            state_size=10,
            timestep_s=25.0,
            temperature_weight=_float_setting(config, "Q_w"),
            terminal_weight=_float_setting(config, "Q_w"),
            move_weight=_float_setting(config, "R_dQ"),
            residual_weight=_LEARNED_RESIDUAL_WEIGHT if identified else 0.0,
            max_iterations=10,
        )

    @classmethod
    def build_components(
        cls,
        config: Mapping[str, JsonValue],
        *,
        model_identified: bool | None = None,
        ekf_factory: EkfFactory | None = None,
        kf_factory: KfFactory | None = None,
        solver_factory: SolverFactory | None = None,
    ) -> tuple[MpcEstimator, MpcSolver]:
        """Build a complete numerical pair, closing a partial build on failure."""

        native_config = cls.native_configuration(
            config,
            model_identified=model_identified,
        )
        estimator = cls.build_estimator(
            config,
            native_config.delay_states,
            ekf_factory=ekf_factory,
            kf_factory=kf_factory,
        )
        try:
            solver = MpcCore.build_solver(native_config) if solver_factory is None else solver_factory(native_config)
        except BaseException:
            _close_component(estimator)
            raise
        return estimator, solver

    @staticmethod
    def build_solver(config: GreyBoxMPCConfig) -> MpcSolver:
        """Build one native numerical solver for a governed candidate pair."""

        return AcadosGreyBoxMPC(config)

    @staticmethod
    def build_estimator(
        config: Mapping[str, JsonValue],
        n_delay: int,
        *,
        ekf_factory: EkfFactory | None = None,
        kf_factory: KfFactory | None = None,
    ) -> MpcEstimator:
        """Build the selected estimator at the runtime control cadence."""

        kind = _str_setting(config, "estimator").lower()
        C_c = _float_setting(config, "C_c")
        h_amb = _float_setting(config, "h_amb")
        T_amb = _float_setting(config, "T_amb")
        t_step = _float_setting(config, "control_period")
        q_temp = _float_setting(config, "est_q_temp")
        q_dist = _float_setting(config, "est_q_dist")
        r_meas = _float_setting(config, "est_r_meas")
        theta = _float_setting(config, "theta")
        K_Q = _float_setting(config, "K_Q")
        if kind == "kf":
            factory = GreyBoxKF if kf_factory is None else kf_factory
            return factory(
                C_c=C_c,
                h_amb=h_amb,
                T_amb=T_amb,
                t_step=t_step,
                q_temp=q_temp,
                q_dist=q_dist,
                r_meas=r_meas,
                theta=theta,
                n_delay=n_delay,
                K_Q=K_Q,
            )
        if kind == "ekf":
            factory = GreyBoxEKF if ekf_factory is None else ekf_factory
            return factory(
                C_c=C_c,
                h_amb=h_amb,
                T_amb=T_amb,
                t_step=t_step,
                q_temp=q_temp,
                q_dist=q_dist,
                r_meas=r_meas,
                theta=theta,
                n_delay=n_delay,
                K_Q=K_Q,
                sigma=_float_setting(config, "sigma"),
            )
        raise ValueError("estimator must be 'ekf' or 'kf'")

    def bind_resources(
        self,
        estimator: MpcEstimator,
        solver: MpcSolver,
        close_resources: Callable[[], None],
        *,
        reset_estimate: bool = False,
    ) -> None:
        """Adopt a governed live pair without closing its retained predecessor."""

        if self._closed:
            raise RuntimeError("cannot bind resources to a closed MPC core")
        self._estimator = estimator
        self._solver = solver
        self._close_resources = _RetryableResourceClose((close_resources,))
        if reset_estimate:
            self._x_hat = None
            self._trajectory_seed = None
            self._seed_anchor_pending = False

    def estimator_seed_requirements(self) -> tuple[float, int]:
        return (
            _float_setting(self.config, "theta"),
            _int_setting(self.config, "n_delay"),
        )

    def seed_from_trajectory(self, seed: EstimatorSeed) -> None:
        """Reset model-dependent estimate state before its first solve."""

        if not isinstance(seed, EstimatorSeed):
            raise TypeError("seed must be an EstimatorSeed")
        n_delay = _int_setting(self.config, "n_delay")
        replayed_states = seed.delay_states
        if seed.status in {"exact", "short"}:
            if len(replayed_states) != n_delay:
                raise ValueError("trajectory seed delay chain does not match configured MPC model")
            delay_states: tuple[float, ...] | None = replayed_states
        else:
            if replayed_states:
                raise ValueError("non-replayable trajectory seed cannot contain delay state")
            delay_states = None
        self._x_hat = self._estimator.reset(
            self._applied_combustion_load,
            seed.chamber_temperature_c,
            delay_states=delay_states,
            disturbance=seed.disturbance,
        )
        self._solver.reset()
        self._last_measured_temperature_c = seed.chamber_temperature_c
        self._consecutive_policy_failures = 0
        self._native_failure_diagnostics = None
        self._trajectory_seed = seed
        self._seed_anchor_pending = True

    @property
    def estimator_seed_status(self) -> str | None:
        return None if self._trajectory_seed is None else self._trajectory_seed.status

    @property
    def estimator_seed(self) -> EstimatorSeed | None:
        return self._trajectory_seed

    def set_target(self, set_point: float) -> None:
        self._set_point_c = to_celsius(set_point, self.units)

    def set_output(self, applied: AppliedOutput) -> None:
        self._applied_combustion_load = normalized_load_from_auger_duty(
            applied.ratio,
            u_max=self.u_max,
        )

    def _equilibrium_load(self, target: float, disturbance: float, identified: bool) -> float:
        if not identified:
            return 0.0
        return float(
            np.clip(
                steady_combustion_load(self.config, target, disturbance),
                0.0,
                1.0,
            )
        )

    @staticmethod
    def _validated_estimate(
        estimate: npt.ArrayLike,
        n_delay: int,
    ) -> npt.NDArray[np.float64]:
        state = np.asarray(estimate, dtype=float)
        if state.shape != (n_delay + 2,) or not np.isfinite(state).all():
            raise ValueError("MPC estimator state must have the configured shape and contain only finite values")
        delay_states = state[:n_delay]
        if np.any(delay_states < 0.0) or np.any(delay_states > 1.0):
            raise ValueError("MPC estimator normalized delay state must be between 0 and 1")
        return state

    def _validated_native_command(self, solve: NativeSolve) -> float:
        horizon = _int_setting(self.config, "n_horizon")
        sequence = np.asarray(solve.sequence_q, dtype=float)
        residual = np.asarray(solve.sequence_residual, dtype=float)
        objective = float(solve.objective)
        diagnostics = solve.diagnostics
        diagnostic_values = (
            diagnostics.solve_time_s,
            diagnostics.objective,
            diagnostics.kkt_residual,
            diagnostics.constraint_residual,
        )
        if (
            sequence.shape != (horizon,)
            or residual.shape != (horizon,)
            or not np.isfinite(sequence).all()
            or not np.isfinite(residual).all()
            or not np.all((-_NATIVE_BOUND_TOLERANCE <= sequence) & (sequence <= 1.0 + _NATIVE_BOUND_TOLERANCE))
            or not math.isfinite(objective)
            or diagnostics.status != 0
            or diagnostics.backend_status != 0
            or isinstance(diagnostics.iterations, bool)
            or not isinstance(diagnostics.iterations, int)
            or diagnostics.iterations < 0
            or not all(math.isfinite(float(value)) and float(value) >= 0.0 for value in diagnostic_values)
            or not isinstance(diagnostics.warm_started, bool)
        ):
            raise ValueError("native grey-box result is malformed")
        self._native_failure_diagnostics = None
        return float(np.clip(sequence[0], 0.0, 1.0))

    def update(self, current: float) -> MpcStep:
        if not self._output_authorized():
            raise RuntimeError("MPC activation pair is not durably authorized")
        measured_c = to_celsius(current, self.units)
        applied_load = self._applied_combustion_load
        n_delay = _int_setting(self.config, "n_delay")
        state_names = tuple(f"q{index}" for index in range(n_delay)) + ("T_c", "d")
        self._last_measured_temperature_c = measured_c
        revision, metadata = self._model_authority()
        model_provenance = "adopted" if metadata is not None else "configured"
        identified = model_is_identified(self.config, metadata)

        solve_start = time.monotonic()
        failure_state = MpcFailureState.SUCCESS
        failure_error: BaseException | None = None
        feasibility = self._last_feasibility
        equilibrium: float | None = None
        residual_move: float | None = None
        raw_firing_load: float | None = None
        estimate_valid = False
        try:
            if self._seed_anchor_pending:
                x_hat = self._validated_estimate(self._x_hat, n_delay)
                self._seed_anchor_pending = False
            else:
                x_hat = self._validated_estimate(
                    self._estimator.update(applied_load, measured_c),
                    n_delay,
                )
            estimate_valid = True
            self._x_hat = x_hat
            disturbance = float(x_hat[-1])
            feasibility = feasibility_report(
                self.config if identified else None,
                self._set_point_c,
                disturbance=disturbance,
                model_revision=revision if identified else None,
                model_provenance=model_provenance if identified else None,
            )
            self._last_feasibility = feasibility
            equilibrium = self._equilibrium_load(self._set_point_c, disturbance, identified)
            solve = self._solver.solve(
                x_hat,
                setpoint_c=self._set_point_c,
                q_previous=applied_load,
                equilibrium_q=equilibrium,
            )
            combustion_load = self._validated_native_command(solve)
            raw_firing_load = combustion_load
            residual_move = combustion_load - equilibrium
        except Exception as error:
            failure_state = MpcFailureState.POLICY_EXCEPTION
            failure_error = error
            if isinstance(error, SolverError):
                self._native_failure_diagnostics = error.diagnostics
            if not estimate_valid:
                try:
                    reset_estimate = self._estimator.reset(applied_load, measured_c)
                    self._x_hat = None if reset_estimate is None else self._validated_estimate(reset_estimate, n_delay)
                except Exception as reset_error:
                    self._x_hat = None
                    error.add_note(f"MPC estimator reset also failed: {reset_error!r}")
            combustion_load = self._last_combustion_load
            equilibrium = None
            residual_move = None
            raw_firing_load = None
        finally:
            solve_end = time.monotonic()

        trace_estimate = self._x_hat
        if trace_estimate is None:
            trace_estimate = np.array([applied_load] * n_delay + [measured_c, 0.0], dtype=float)
        state_values = tuple(float(value) for value in trace_estimate)
        disturbance = state_values[-1]

        if failure_state is MpcFailureState.SUCCESS:
            if self._consecutive_policy_failures:
                self._logger.info(f"[mpc] policy recovered after {self._consecutive_policy_failures} failed step(s)")
            self._consecutive_policy_failures = 0
        else:
            self._consecutive_policy_failures += 1
            failure_count = self._consecutive_policy_failures
            if failure_count == 1 or failure_count in (10, 60) or failure_count % 300 == 0:
                self._logger.error(
                    f"[mpc] policy has failed {failure_count} consecutive step(s) "
                    f"({type(failure_error).__name__}: {failure_error}); holding normalized "
                    f"combustion load {combustion_load:.3f}. The grill is not being controlled "
                    "to setpoint -- check estimator state, the published acados runtime, and model configuration."
                )
            if failure_error is None:
                raise RuntimeError("policy failure did not retain its cause")
            self._on_policy_failure(failure_error)

        self._last_equilibrium_load = equilibrium
        self._last_residual_load = residual_move
        self._last_raw_combustion_load = raw_firing_load
        self._last_combustion_load = combustion_load
        fan_min = _float_setting(self.config, "fan_min_pct")
        fan_max = _float_setting(self.config, "fan_max_pct")
        fan_enabled = self.config["enable_fan_input"] is True
        baseline_allocation = allocate(
            combustion_load,
            u_max=self.u_max,
            fan_min_pct=fan_min,
            fan_max_pct=fan_max,
            enable_fan=fan_enabled,
        )
        calibration = (
            self._advance_calibration(
                combustion_load,
                measured_c,
                self._forecast_calibration,
            )
            if failure_state is MpcFailureState.SUCCESS
            else _INACTIVE_CALIBRATION
        )
        requested_load = float(np.clip(combustion_load + calibration.probe_q, 0.0, 1.0))
        allocation = (
            baseline_allocation
            if requested_load == combustion_load
            else allocate(
                requested_load,
                u_max=self.u_max,
                fan_min_pct=fan_min,
                fan_max_pct=fan_max,
                enable_fan=fan_enabled,
            )
        )

        diagnostics = MpcTraceDiagnostics(
            state_names=state_names,
            state_values=state_values,
            disturbance_estimate=disturbance,
            model_revision=revision,
            model_provenance=model_provenance,
            raw_policy_firing_load=raw_firing_load,
            equilibrium_feed_forward=equilibrium,
            residual_move=residual_move,
            bounded_firing_load=combustion_load,
            applied_combustion_load=applied_load,
            policy_kind="acados-grey",
            failure_state=failure_state,
            consecutive_policy_failures=self._consecutive_policy_failures,
            solve_start_monotonic=solve_start,
            solve_end_monotonic=solve_end,
            solve_duration_seconds=solve_end - solve_start,
            feasibility=feasibility,
            model_lifecycle=None,
        )
        return MpcStep(
            cycle_ratio=allocation.auger_duty,
            fan={"duty": allocation.fan_duty},
            diagnostics=diagnostics,
            allocation=allocation,
            baseline_allocation=baseline_allocation,
        )

    def _forecast_calibration(
        self,
        q_future: npt.NDArray[np.float64],
        ambient_future: npt.NDArray[np.float64],
    ) -> npt.NDArray[np.float64]:
        from controller.grey_box import GreyBoxPredictionAdapter

        return GreyBoxPredictionAdapter.from_estimator(
            self._estimator,
            config=self.config,
        ).forecast(q_future, ambient_future)

    def snapshot_parameters(self) -> Mapping[str, JsonValue]:
        return {key: self.config[key] for key in MODEL_PARAMETER_KEYS}

    def capture_operating_state(self) -> MpcOperatingState:
        estimate = self._x_hat
        n_delay = _int_setting(self.config, "n_delay")
        delay_states = None if estimate is None else tuple(float(value) for value in estimate[:n_delay])
        disturbance = 0.0 if estimate is None else float(estimate[-1])
        return MpcOperatingState(
            set_point_c=self._set_point_c,
            applied_combustion_load=self._applied_combustion_load,
            last_safe_combustion_load=self._last_combustion_load,
            measured_temperature_c=self._last_measured_temperature_c,
            delay_states=delay_states,
            disturbance=disturbance,
        )

    def capture_model_independent_state(self) -> MpcModelIndependentState:
        return MpcModelIndependentState(
            set_point_c=self._set_point_c,
            applied_combustion_load=self._applied_combustion_load,
            last_safe_combustion_load=self._last_combustion_load,
            measured_temperature_c=self._last_measured_temperature_c,
        )

    def adopt_model_independent_state(self, state: MpcModelIndependentState) -> None:
        """Transfer physical control fields while retaining this model's delay state."""

        if not isinstance(state, MpcModelIndependentState):
            raise TypeError("state must be an MpcModelIndependentState")
        estimate = self._x_hat
        n_delay = _int_setting(self.config, "n_delay")
        delay_states = None if estimate is None else tuple(float(value) for value in estimate[:n_delay])
        disturbance = 0.0 if estimate is None else float(estimate[-1])
        self._set_point_c = state.set_point_c
        self._applied_combustion_load = state.applied_combustion_load
        self._last_combustion_load = state.last_safe_combustion_load
        self._last_raw_combustion_load = state.last_safe_combustion_load
        self._last_equilibrium_load = None
        self._last_residual_load = None
        self._last_feasibility = None
        self._last_measured_temperature_c = state.measured_temperature_c
        self._x_hat = self._estimator.reset(
            state.applied_combustion_load,
            state.measured_temperature_c,
            delay_states=delay_states,
            disturbance=disturbance,
        )
        self._solver.reset()
        self._consecutive_policy_failures = 0
        self._native_failure_diagnostics = None

    def adopt_operating_state(self, state: MpcOperatingState) -> None:
        if not isinstance(state, MpcOperatingState):
            raise TypeError("state must be an MpcOperatingState")
        n_delay = _int_setting(self.config, "n_delay")
        if state.delay_states is not None and len(state.delay_states) != n_delay:
            raise ValueError("operating state delay chain does not match configured MPC model")
        self._set_point_c = state.set_point_c
        self._applied_combustion_load = state.applied_combustion_load
        self._last_combustion_load = state.last_safe_combustion_load
        self._last_raw_combustion_load = state.last_safe_combustion_load
        self._last_equilibrium_load = None
        self._last_residual_load = None
        self._last_feasibility = None
        self._last_measured_temperature_c = state.measured_temperature_c
        self._x_hat = self._estimator.reset(
            state.applied_combustion_load,
            state.measured_temperature_c,
            delay_states=state.delay_states,
            disturbance=state.disturbance,
        )
        self._solver.reset()
        self._consecutive_policy_failures = 0
        self._native_failure_diagnostics = None

    def clear_estimate(self) -> None:
        self._x_hat = None
        self._trajectory_seed = None
        self._seed_anchor_pending = False

    @property
    def estimator(self) -> MpcEstimator:
        return self._estimator

    @property
    def solver(self) -> MpcSolver:
        return self._solver

    @property
    def set_point_c(self) -> float:
        return self._set_point_c

    @property
    def applied_combustion_load(self) -> float:
        return self._applied_combustion_load

    @property
    def last_combustion_load(self) -> float:
        return self._last_combustion_load

    @property
    def last_raw_combustion_load(self) -> float | None:
        return self._last_raw_combustion_load

    @property
    def last_equilibrium_load(self) -> float | None:
        return self._last_equilibrium_load

    @property
    def last_residual_load(self) -> float | None:
        return self._last_residual_load

    @property
    def last_feasibility(self) -> FeasibilityReport | None:
        return self._last_feasibility

    @property
    def estimate(self) -> npt.NDArray[np.float64] | None:
        return self._x_hat

    @property
    def consecutive_policy_failures(self) -> int:
        return self._consecutive_policy_failures

    @property
    def native_failure_diagnostics(self) -> SolverDiagnostics | None:
        return self._native_failure_diagnostics

    @property
    def close_complete(self) -> bool:
        return self._closed and self._close_resources is None

    def close(self) -> None:
        if self._closed and self._close_resources is None:
            return
        self._closed = True
        close_resources = self._close_resources
        if close_resources is None:
            return
        try:
            close_resources.close()
        finally:
            if close_resources.complete:
                self._close_resources = None
