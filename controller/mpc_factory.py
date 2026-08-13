"""Typed construction and ownership boundary for grey MPC control pairs."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from numbers import Integral, Real
from typing import Literal

import numpy as np

from controller.acados import GreyBoxMPCConfig
from controller.model_learning.activation import (
    GreyControlPairDescriptor,
    canonical_snapshot_digest,
)
from controller.mpc_config import JsonValue, MpcConfig, model_is_identified, normalize_config
from controller.mpc_core import (
    Closable,
    EkfFactory,
    KfFactory,
    LoadAdjustment,
    ModelAuthority,
    MpcCore,
    MpcEstimator,
    MpcSolver,
    PolicyFailureHandler,
    SolverFactory,
)
from controller.runtime.model_fitting import TargetTimingEvidence


@dataclass(frozen=True, slots=True)
class NativeTiming(TargetTimingEvidence):
    """Deterministic timing evidence from exercising one native candidate."""


@dataclass(frozen=True, slots=True)
class MpcPairConfiguration:
    """Complete typed input needed to construct and identify one MPC pair."""

    settings: MpcConfig | GreyBoxMPCConfig
    estimator_kind: Literal["ekf", "kf"]
    candidate_generation: int
    role_generation: int
    model_identified: bool

    def __post_init__(self) -> None:
        if self.estimator_kind not in {"ekf", "kf"}:
            raise ValueError("estimator_kind must be 'ekf' or 'kf'")
        for name, value in (
            ("candidate_generation", self.candidate_generation),
            ("role_generation", self.role_generation),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.model_identified, bool):
            raise ValueError("model_identified must be a bool")


class _OutputAuthorization:
    def __init__(self, authorized: bool) -> None:
        self._lock = threading.Lock()
        self._authorized = authorized

    def __call__(self) -> bool:
        with self._lock:
            return self._authorized

    def authorize(self) -> None:
        with self._lock:
            self._authorized = True

    def revoke(self) -> None:
        with self._lock:
            self._authorized = False


@dataclass(slots=True)
class OwnedMpcPair:
    """One indivisible, idempotently closed numerical MPC owner."""

    core: MpcCore
    descriptor: GreyControlPairDescriptor
    _authorization: _OutputAuthorization = field(
        default_factory=lambda: _OutputAuthorization(False),
        repr=False,
        compare=False,
    )
    _close_lock: threading.Lock = field(init=False, repr=False, compare=False)
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.core, MpcCore):
            raise TypeError("core must be an MpcCore")
        if not isinstance(self.descriptor, GreyControlPairDescriptor):
            raise TypeError("descriptor must be a GreyControlPairDescriptor")
        self._close_lock = threading.Lock()
        self._closed = False

    @property
    def estimator(self) -> MpcEstimator:
        return self.core.estimator

    @property
    def solver(self) -> MpcSolver:
        return self.core.solver

    @property
    def authorized(self) -> bool:
        return self._authorization()

    @property
    def closed(self) -> bool:
        with self._close_lock:
            return self._closed

    def authorize_output(self) -> None:
        with self._close_lock:
            if self._closed:
                raise RuntimeError("cannot authorize a closed MPC pair")
            self._authorization.authorize()

    def revoke_output(self) -> None:
        self._authorization.revoke()

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._authorization.revoke()
        self.core.close()


class MpcPairFactory:
    """Sole builder, restorer, validator, and dry-solve owner for MPC pairs."""

    def __init__(
        self,
        base_configuration: Mapping[str, JsonValue] | None,
        units: str,
        cycle_data: Mapping[str, JsonValue],
        *,
        adjust_load: LoadAdjustment,
        model_authority: ModelAuthority,
        on_policy_failure: PolicyFailureHandler,
        ekf_factory: EkfFactory | None = None,
        kf_factory: KfFactory | None = None,
        solver_factory: SolverFactory | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_configuration = normalize_config(base_configuration)
        self._units = units
        self._cycle_data = cycle_data
        self._adjust_load = adjust_load
        self._model_authority = model_authority
        self._on_policy_failure = on_policy_failure
        self._ekf_factory = ekf_factory
        self._kf_factory = kf_factory
        self._solver_factory = solver_factory
        self._monotonic = monotonic

    def configured(
        self,
        settings: Mapping[str, JsonValue] | None,
        *,
        candidate_generation: int,
        role_generation: int,
        model_identified: bool | None = None,
    ) -> MpcPairConfiguration:
        normalized = normalize_config(settings)
        raw_estimator_kind = normalized.get("estimator")
        if not isinstance(raw_estimator_kind, str):
            raise ValueError("estimator must be 'ekf' or 'kf'")
        estimator_kind = raw_estimator_kind.lower()
        if estimator_kind == "ekf":
            kind: Literal["ekf", "kf"] = "ekf"
        elif estimator_kind == "kf":
            kind = "kf"
        else:
            raise ValueError("estimator must be 'ekf' or 'kf'")
        return MpcPairConfiguration(
            settings=normalized,
            estimator_kind=kind,
            candidate_generation=candidate_generation,
            role_generation=role_generation,
            model_identified=(
                model_is_identified(normalized)
                if model_identified is None
                else model_identified
            ),
        )

    def native(
        self,
        settings: GreyBoxMPCConfig,
        *,
        estimator_kind: Literal["ekf", "kf"],
        candidate_generation: int,
        role_generation: int,
    ) -> MpcPairConfiguration:
        return MpcPairConfiguration(
            settings=settings,
            estimator_kind=estimator_kind,
            candidate_generation=candidate_generation,
            role_generation=role_generation,
            model_identified=settings.residual_weight > 0.0,
        )
    def build(
        self,
        configuration: MpcPairConfiguration,
        *,
        authorized: bool,
    ) -> OwnedMpcPair:
        if not isinstance(authorized, bool):
            raise ValueError("authorized must be a bool")
        settings, expected_native = self._settings(configuration)
        gate = _OutputAuthorization(False)
        core = MpcCore(
            settings,
            self._units,
            self._cycle_data,
            output_authorized=gate,
            adjust_load=self._adjust_load,
            model_authority=self._model_authority,
            on_policy_failure=self._on_policy_failure,
            ekf_factory=self._ekf_factory,
            kf_factory=self._kf_factory,
            solver_factory=self._solver_factory,
            model_identified=configuration.model_identified,
        )
        pair = self._finish(core, gate, configuration, expected_native)
        if authorized:
            pair.authorize_output()
        return pair

    def adopt(
        self,
        configuration: MpcPairConfiguration,
        estimator: MpcEstimator,
        solver: MpcSolver,
        *,
        authorized: bool,
    ) -> OwnedMpcPair:
        if not isinstance(authorized, bool):
            raise ValueError("authorized must be a bool")
        settings, expected_native = self._settings(configuration)
        gate = _OutputAuthorization(False)
        try:
            core = MpcCore(
                settings,
                self._units,
                self._cycle_data,
                output_authorized=gate,
                adjust_load=self._adjust_load,
                model_authority=self._model_authority,
                on_policy_failure=self._on_policy_failure,
                components=(estimator, solver),
                model_identified=configuration.model_identified,
            )
        except BaseException:
            self.close_components(estimator, solver)
            raise
        pair = self._finish(core, gate, configuration, expected_native)
        if authorized:
            pair.authorize_output()
        return pair

    def restore(self, descriptor: GreyControlPairDescriptor) -> OwnedMpcPair:
        if descriptor.solver_kind != "acados-grey":
            raise ValueError("unsupported solver kind")
        estimator_kind = descriptor.estimator_kind.lower()
        if estimator_kind == "ekf":
            kind: Literal["ekf", "kf"] = "ekf"
        elif estimator_kind == "kf":
            kind = "kf"
        else:
            raise ValueError("unsupported estimator kind")
        native = self._native_from_descriptor(descriptor)
        pair = self.build(
            self.native(
                native,
                estimator_kind=kind,
                candidate_generation=descriptor.candidate_generation,
                role_generation=descriptor.role_generation,
            ),
            authorized=False,
        )
        if pair.descriptor != descriptor:
            pair.close()
            raise ValueError("restored pair descriptor changed")
        return pair

    def validate(self, pair: OwnedMpcPair) -> bool:
        native = self._native_mapping(pair.solver.config)
        estimator_kind = pair.core.config.get("estimator")
        return (
            isinstance(estimator_kind, str)
            and pair.descriptor.solver_kind == "acados-grey"
            and pair.descriptor.estimator_kind == estimator_kind.lower()
            and pair.descriptor.model_digest == canonical_snapshot_digest(native)
            and pair.descriptor.configuration == native
        )

    def dry_solve(self, pair: OwnedMpcPair, *, temperature_c: float) -> NativeTiming:
        try:
            return self._probe(pair.solver, temperature_c=temperature_c)
        except BaseException:
            pair.close()
            raise

    def build_estimator(self, native: GreyBoxMPCConfig) -> MpcEstimator:
        estimator_kind = self._base_configuration.get("estimator")
        if not isinstance(estimator_kind, str):
            raise ValueError("estimator must be 'ekf' or 'kf'")
        settings = self._settings_from_native(native, estimator_kind)
        return MpcCore.build_estimator(
            settings,
            native.delay_states,
            ekf_factory=self._ekf_factory,
            kf_factory=self._kf_factory,
        )

    def build_solver(self, native: GreyBoxMPCConfig) -> MpcSolver:
        return (
            MpcCore.build_solver(native)
            if self._solver_factory is None
            else self._solver_factory(native)
        )

    def probe_solver(self, solver: MpcSolver) -> NativeTiming:
        return self._probe(solver, temperature_c=float(solver.config.T_amb))

    def _finish(
        self,
        core: MpcCore,
        gate: _OutputAuthorization,
        configuration: MpcPairConfiguration,
        expected_native: GreyBoxMPCConfig | None,
    ) -> OwnedMpcPair:
        try:
            native = self._native_mapping(core.solver.config)
            if expected_native is not None and native != self._native_mapping(expected_native):
                raise ValueError("constructed pair configuration digest changed")
            descriptor = GreyControlPairDescriptor(
                model_digest=canonical_snapshot_digest(native),
                configuration=native,
                estimator_kind=configuration.estimator_kind,
                solver_kind="acados-grey",
                candidate_generation=configuration.candidate_generation,
                role_generation=configuration.role_generation,
            )
            pair = OwnedMpcPair(core, descriptor, gate)
            if not self.validate(pair):
                raise ValueError("constructed pair validation failed")
            return pair
        except BaseException:
            core.close()
            raise
    def _settings(
        self,
        configuration: MpcPairConfiguration,
    ) -> tuple[MpcConfig, GreyBoxMPCConfig | None]:
        if isinstance(configuration.settings, GreyBoxMPCConfig):
            return (
                self._settings_from_native(configuration.settings, configuration.estimator_kind),
                configuration.settings,
            )
        settings = normalize_config(configuration.settings)
        settings["estimator"] = configuration.estimator_kind
        return settings, None

    def _settings_from_native(
        self,
        native: GreyBoxMPCConfig,
        estimator_kind: str,
    ) -> MpcConfig:
        settings = normalize_config(self._base_configuration)
        settings.update(
            {
                "C_c": native.C_c,
                "h_amb": native.h_amb,
                "T_amb": native.T_amb,
                "theta": native.theta,
                "K_Q": native.K_Q,
                "sigma": native.sigma,
                "n_horizon": native.horizon_steps,
                "n_delay": native.delay_states,
                "Q_w": native.temperature_weight,
                "R_dQ": native.move_weight,
                "estimator": estimator_kind,
            }
        )
        return normalize_config(settings)

    def _probe(self, solver: MpcSolver, *, temperature_c: float) -> NativeTiming:
        if not math.isfinite(temperature_c):
            raise ValueError("dry-solve temperature must be finite")
        state = np.zeros(solver.config.state_size, dtype=float)
        state[0] = temperature_c
        state[solver.config.delay_states] = float(solver.config.T_amb)
        durations: list[float] = []
        for _ in range(5):
            started = self._monotonic()
            result = solver.solve(
                state,
                setpoint_c=temperature_c + 50.0,
                q_previous=0.0,
                equilibrium_q=0.4,
            )
            sequence = np.asarray(result.sequence_q, dtype=float)
            if sequence.shape != (solver.config.horizon_steps,) or not np.isfinite(sequence).all():
                raise ValueError("native dry-solve sequence must be finite and horizon-sized")
            if not math.isfinite(float(result.objective)):
                raise ValueError("native dry-solve objective must be finite")
            elapsed_ms = (self._monotonic() - started) * 1_000.0
            if not math.isfinite(elapsed_ms) or elapsed_ms < 0.0:
                raise ValueError("native dry-solve timing must be finite and non-negative")
            durations.append(elapsed_ms)
        control_period = self._base_configuration.get("control_period")
        if isinstance(control_period, bool) or not isinstance(control_period, Real):
            raise ValueError("control_period must be numeric")
        return NativeTiming(
            target="active-runtime",
            samples=len(durations),
            p99_ms=max(durations),
            limit_ms=float(control_period) * 200.0,
        )

    @staticmethod
    def _native_mapping(config: GreyBoxMPCConfig) -> dict[str, JsonValue]:
        return {
            "C_c": config.C_c,
            "h_amb": config.h_amb,
            "T_amb": config.T_amb,
            "theta": config.theta,
            "K_Q": config.K_Q,
            "sigma": config.sigma,
            "horizon_steps": config.horizon_steps,
            "delay_states": config.delay_states,
            "state_size": config.state_size,
            "timestep_s": config.timestep_s,
            "temperature_weight": config.temperature_weight,
            "terminal_weight": config.terminal_weight,
            "move_weight": config.move_weight,
            "residual_weight": config.residual_weight,
            "max_iterations": config.max_iterations,
        }

    @staticmethod
    def _native_from_descriptor(descriptor: GreyControlPairDescriptor) -> GreyBoxMPCConfig:
        configuration = descriptor.configuration

        def number(name: str) -> float:
            value = configuration.get(name)
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"descriptor {name} must be numeric")
            normalized = float(value)
            if not math.isfinite(normalized):
                raise ValueError(f"descriptor {name} must be finite")
            return normalized

        def integer(name: str) -> int:
            value = configuration.get(name)
            if isinstance(value, bool) or not isinstance(value, Integral):
                raise ValueError(f"descriptor {name} must be an integer")
            return int(value)

        return GreyBoxMPCConfig(
            C_c=number("C_c"),
            h_amb=number("h_amb"),
            T_amb=number("T_amb"),
            theta=number("theta"),
            K_Q=number("K_Q"),
            sigma=number("sigma"),
            horizon_steps=integer("horizon_steps"),
            delay_states=integer("delay_states"),
            state_size=integer("state_size"),
            timestep_s=number("timestep_s"),
            temperature_weight=number("temperature_weight"),
            terminal_weight=number("terminal_weight"),
            move_weight=number("move_weight"),
            residual_weight=number("residual_weight"),
            max_iterations=integer("max_iterations"),
        )

    @staticmethod
    def close_components(estimator: MpcEstimator, solver: MpcSolver) -> None:
        errors: list[BaseException] = []
        for component in (solver, estimator):
            if isinstance(component, Closable):
                try:
                    component.close()
                except BaseException as error:
                    errors.append(error)
        if errors:
            raise RuntimeError("could not close failed MPC pair construction") from errors[0]
