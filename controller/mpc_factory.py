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
    CalibrationAdvance,
    Closable,
    EkfFactory,
    KfFactory,
    ModelAuthority,
    MpcCore,
    MpcEstimator,
    MpcSolver,
    PolicyFailureHandler,
    SolverFactory,
)
from controller.runtime.model_fitting import TargetTimingEvidence

_ESTIMATOR_CONFIGURATION_FIELDS = frozenset({"control_period", "est_q_temp", "est_q_dist", "est_r_meas"})
_NATIVE_CONFIGURATION_FIELDS = frozenset(
    {
        "C_c",
        "h_amb",
        "T_amb",
        "theta",
        "K_Q",
        "sigma",
        "horizon_steps",
        "delay_states",
        "state_size",
        "timestep_s",
        "temperature_weight",
        "terminal_weight",
        "move_weight",
        "residual_weight",
        "max_iterations",
    }
)
_PAIR_CONFIGURATION_FIELDS = _NATIVE_CONFIGURATION_FIELDS | _ESTIMATOR_CONFIGURATION_FIELDS
_LEGACY_ESTIMATOR_CONFIGURATION: dict[str, JsonValue] = {
    "control_period": 5.0,
    "est_q_temp": 1e-2,
    "est_q_dist": 0.05,
    "est_r_meas": 0.04,
}
_LEGACY_V4_CONFIGURATION_FIELDS = frozenset({"schema", "n_delay", "parameters"})
_LEGACY_V4_PARAMETER_FIELDS = frozenset({"C_c", "K_Q", "theta", "h_amb", "T_amb", "sigma"})
_LEGACY_V4_IDENTIFICATION_DEFAULTS = {
    "C_c": 320.0,
    "K_Q": 350.0,
    "theta": 50.0,
    "h_amb": 0.5,
    "sigma": 1.4e-9,
}


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
            raise TypeError("model_identified must be a bool")


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
            if self._closed and self.core.close_complete:
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
        advance_calibration: CalibrationAdvance,
        model_authority: ModelAuthority,
        on_policy_failure: PolicyFailureHandler,
        ekf_factory: EkfFactory | None = None,
        kf_factory: KfFactory | None = None,
        solver_factory: SolverFactory | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_configuration = normalize_config(base_configuration)
        control_period = self._base_configuration.get("control_period")
        if not isinstance(control_period, float):
            raise RuntimeError("normalized control_period must be a float")  # noqa: TRY004  invariant on already-normalized input, not caller type validation
        self._control_period = control_period
        self._units = units
        self._cycle_data = cycle_data
        self._advance_calibration = advance_calibration
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
            raise TypeError("estimator must be 'ekf' or 'kf'")
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
            model_identified=(model_is_identified(normalized) if model_identified is None else model_identified),
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

    def descriptor(self, configuration: MpcPairConfiguration) -> GreyControlPairDescriptor:
        """Describe the exact native contract without constructing live resources."""

        settings, native = self._settings(configuration)
        descriptor_configuration = self._descriptor_mapping(settings, native)
        return GreyControlPairDescriptor(
            model_digest=canonical_snapshot_digest(self._native_mapping(native)),
            configuration=descriptor_configuration,
            estimator_kind=configuration.estimator_kind,
            solver_kind="acados-grey",
            candidate_generation=configuration.candidate_generation,
            role_generation=configuration.role_generation,
        )

    def build(
        self,
        configuration: MpcPairConfiguration,
        *,
        authorized: bool,
    ) -> OwnedMpcPair:
        if not isinstance(authorized, bool):
            raise TypeError("authorized must be a bool")
        settings, expected_native = self._settings(configuration)
        return self._build_owned(
            configuration,
            settings,
            expected_native,
            authorized=authorized,
        )

    def _build_owned(
        self,
        configuration: MpcPairConfiguration,
        settings: MpcConfig,
        expected_native: GreyBoxMPCConfig,
        *,
        authorized: bool,
    ) -> OwnedMpcPair:
        gate = _OutputAuthorization(False)
        core = MpcCore(
            settings,
            self._units,
            self._cycle_data,
            output_authorized=gate,
            advance_calibration=self._advance_calibration,
            model_authority=self._model_authority,
            on_policy_failure=self._on_policy_failure,
            ekf_factory=self._ekf_factory,
            kf_factory=self._kf_factory,
            solver_factory=self._solver_factory,
            model_identified=configuration.model_identified,
        )
        pair = self._finish(core, gate, configuration, settings, expected_native)
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
            raise TypeError("authorized must be a bool")
        settings, expected_native = self._settings(configuration)
        gate = _OutputAuthorization(False)
        try:
            core = MpcCore(
                settings,
                self._units,
                self._cycle_data,
                output_authorized=gate,
                advance_calibration=self._advance_calibration,
                model_authority=self._model_authority,
                on_policy_failure=self._on_policy_failure,
                components=(estimator, solver),
                model_identified=configuration.model_identified,
            )
        except BaseException:
            self.close_components(estimator, solver)
            raise
        pair = self._finish(core, gate, configuration, settings, expected_native)
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
        self._require_complete_descriptor(descriptor)
        native = self._native_from_descriptor(descriptor)
        settings = self._settings_from_descriptor(descriptor, native, kind)
        configuration = self.native(
            native,
            estimator_kind=kind,
            candidate_generation=descriptor.candidate_generation,
            role_generation=descriptor.role_generation,
        )
        pair = self._build_owned(
            configuration,
            settings,
            native,
            authorized=False,
        )
        if pair.descriptor != descriptor:
            pair.close()
            raise ValueError("restored pair descriptor changed")
        return pair

    def validate(self, pair: OwnedMpcPair) -> bool:
        if pair.closed:
            return False
        construction = self._descriptor_mapping(pair.core.config, pair.solver.config)
        estimator_kind = pair.core.config.get("estimator")
        return (
            isinstance(estimator_kind, str)
            and pair.descriptor.solver_kind == "acados-grey"
            and pair.descriptor.estimator_kind == estimator_kind.lower()
            and pair.descriptor.model_digest == canonical_snapshot_digest(self._native_mapping(pair.solver.config))
            and pair.descriptor.configuration == construction
        )

    def dry_solve(self, pair: OwnedMpcPair, *, temperature_c: float) -> NativeTiming:
        try:
            return self._probe(
                pair.solver,
                temperature_c=temperature_c,
                control_period=pair.core.config["control_period"],
            )
        except BaseException:
            pair.close()
            raise

    def build_estimator(self, native: GreyBoxMPCConfig) -> MpcEstimator:
        estimator_kind = self._base_configuration.get("estimator")
        if not isinstance(estimator_kind, str):
            raise RuntimeError("normalized estimator must be a string")  # noqa: TRY004  invariant on already-normalized input, not caller type validation
        settings = self._settings_from_native(native, estimator_kind)
        return MpcCore.build_estimator(
            settings,
            native.delay_states,
            ekf_factory=self._ekf_factory,
            kf_factory=self._kf_factory,
        )

    def build_solver(self, native: GreyBoxMPCConfig) -> MpcSolver:
        return MpcCore.build_solver(native) if self._solver_factory is None else self._solver_factory(native)

    def probe_solver(self, solver: MpcSolver) -> NativeTiming:
        return self._probe(
            solver,
            temperature_c=float(solver.config.T_amb),
            control_period=self._control_period,
        )

    def _finish(
        self,
        core: MpcCore,
        gate: _OutputAuthorization,
        configuration: MpcPairConfiguration,
        settings: MpcConfig,
        expected_native: GreyBoxMPCConfig,
    ) -> OwnedMpcPair:
        try:
            native = self._native_mapping(core.solver.config)
            if native != self._native_mapping(expected_native):
                raise ValueError("constructed pair configuration digest changed")
            descriptor_configuration = self._descriptor_mapping(
                settings,
                core.solver.config,
            )
            descriptor = GreyControlPairDescriptor(
                model_digest=canonical_snapshot_digest(native),
                configuration=descriptor_configuration,
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
    ) -> tuple[MpcConfig, GreyBoxMPCConfig]:
        if isinstance(configuration.settings, GreyBoxMPCConfig):
            return (
                self._settings_from_native(configuration.settings, configuration.estimator_kind),
                configuration.settings,
            )
        settings = normalize_config(configuration.settings)
        settings["estimator"] = configuration.estimator_kind
        native = MpcCore.native_configuration(
            settings,
            model_identified=configuration.model_identified,
        )
        return settings, native

    def _settings_from_native(
        self,
        native: GreyBoxMPCConfig,
        estimator_kind: str,
    ) -> MpcConfig:
        return self._settings_for_native(
            native,
            estimator_kind,
            control_period=self._base_configuration["control_period"],
            est_q_temp=self._base_configuration["est_q_temp"],
            est_q_dist=self._base_configuration["est_q_dist"],
            est_r_meas=self._base_configuration["est_r_meas"],
        )

    @staticmethod
    def _settings_for_native(
        native: GreyBoxMPCConfig,
        estimator_kind: str,
        *,
        control_period: float,
        est_q_temp: float,
        est_q_dist: float,
        est_r_meas: float,
    ) -> MpcConfig:
        return normalize_config(
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
                "control_period": control_period,
                "est_q_temp": est_q_temp,
                "est_q_dist": est_q_dist,
                "est_r_meas": est_r_meas,
            }
        )

    def _probe(
        self,
        solver: MpcSolver,
        *,
        temperature_c: float,
        control_period: float,
    ) -> NativeTiming:
        if not math.isfinite(temperature_c):
            raise ValueError("dry-solve temperature must be finite")
        state = np.zeros(solver.config.state_size, dtype=float)
        state[solver.config.delay_states] = temperature_c
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
        return NativeTiming(
            target="active-runtime",
            samples=len(durations),
            p99_ms=max(durations),
            limit_ms=control_period * 200.0,
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

    @classmethod
    def _descriptor_mapping(
        cls,
        settings: MpcConfig,
        native: GreyBoxMPCConfig,
    ) -> dict[str, JsonValue]:
        construction = cls._native_mapping(native)
        construction.update(
            {
                "control_period": settings["control_period"],
                "est_q_temp": settings["est_q_temp"],
                "est_q_dist": settings["est_q_dist"],
                "est_r_meas": settings["est_r_meas"],
            }
        )
        return construction

    @staticmethod
    def migrate_legacy_descriptor(
        descriptor: GreyControlPairDescriptor,
    ) -> GreyControlPairDescriptor:
        fields = frozenset(descriptor.configuration)
        if fields == _PAIR_CONFIGURATION_FIELDS:
            return descriptor
        if fields == _LEGACY_V4_CONFIGURATION_FIELDS:
            return MpcPairFactory._migrate_nested_v4_descriptor(descriptor)
        if fields == _NATIVE_CONFIGURATION_FIELDS:
            configuration = dict(descriptor.configuration)
            configuration.update(_LEGACY_ESTIMATOR_CONFIGURATION)
            return GreyControlPairDescriptor(
                model_digest=descriptor.model_digest,
                configuration=configuration,
                estimator_kind=descriptor.estimator_kind,
                solver_kind=descriptor.solver_kind,
                candidate_generation=descriptor.candidate_generation,
                role_generation=descriptor.role_generation,
            )
        MpcPairFactory._require_complete_descriptor(descriptor)
        return descriptor

    @staticmethod
    def _migrate_nested_v4_descriptor(
        descriptor: GreyControlPairDescriptor,
    ) -> GreyControlPairDescriptor:
        configuration = descriptor.configuration
        if configuration.get("schema") != "pifire-grey-box-model/v4":
            raise ValueError("unsupported legacy descriptor schema")
        n_delay = configuration.get("n_delay")
        if isinstance(n_delay, bool) or not isinstance(n_delay, Integral) or n_delay != 8:
            raise ValueError("legacy descriptor n_delay must be the integer 8")
        parameters = configuration.get("parameters")
        if not isinstance(parameters, Mapping) or frozenset(parameters) != _LEGACY_V4_PARAMETER_FIELDS:
            raise ValueError("legacy descriptor parameters do not match the v4 schema")
        normalized_parameters: dict[str, float] = {}
        for name in _LEGACY_V4_PARAMETER_FIELDS:
            value = parameters.get(name)
            if isinstance(value, bool) or not isinstance(value, Real):
                raise TypeError(f"legacy descriptor parameter {name} must be numeric")
            normalized = float(value)
            if not math.isfinite(normalized):
                raise ValueError(f"legacy descriptor parameter {name} must be finite")
            normalized_parameters[name] = normalized
        model_identified = any(
            normalized_parameters[name] != expected for name, expected in _LEGACY_V4_IDENTIFICATION_DEFAULTS.items()
        )
        native = GreyBoxMPCConfig(
            C_c=normalized_parameters["C_c"],
            h_amb=normalized_parameters["h_amb"],
            T_amb=normalized_parameters["T_amb"],
            theta=normalized_parameters["theta"],
            K_Q=normalized_parameters["K_Q"],
            sigma=normalized_parameters["sigma"],
            horizon_steps=24,
            delay_states=8,
            state_size=10,
            timestep_s=25.0,
            temperature_weight=1.0,
            terminal_weight=1.0,
            move_weight=0.1,
            residual_weight=1_000.0 if model_identified else 0.0,
            max_iterations=10,
        )
        settings = MpcPairFactory._settings_for_native(
            native,
            descriptor.estimator_kind,
            control_period=5.0,
            est_q_temp=1e-2,
            est_q_dist=0.05,
            est_r_meas=0.04,
        )
        migrated_configuration = MpcPairFactory._descriptor_mapping(settings, native)
        return GreyControlPairDescriptor(
            model_digest=canonical_snapshot_digest(MpcPairFactory._native_mapping(native)),
            configuration=migrated_configuration,
            estimator_kind=descriptor.estimator_kind,
            solver_kind=descriptor.solver_kind,
            candidate_generation=descriptor.candidate_generation,
            role_generation=descriptor.role_generation,
        )

    @staticmethod
    def _require_complete_descriptor(
        descriptor: GreyControlPairDescriptor,
    ) -> None:
        fields = frozenset(descriptor.configuration)
        if fields == _PAIR_CONFIGURATION_FIELDS:
            return
        missing = ", ".join(sorted(_PAIR_CONFIGURATION_FIELDS - fields))
        unexpected = ", ".join(sorted(fields - _PAIR_CONFIGURATION_FIELDS))
        raise ValueError(
            "descriptor configuration fields do not match"
            f"; missing: {missing or 'none'}"
            f"; unexpected: {unexpected or 'none'}"
        )

    @staticmethod
    def _settings_from_descriptor(
        descriptor: GreyControlPairDescriptor,
        native: GreyBoxMPCConfig,
        estimator_kind: str,
    ) -> MpcConfig:
        configuration = descriptor.configuration

        def number(name: str) -> float:
            value = configuration.get(name)
            if isinstance(value, bool) or not isinstance(value, Real):
                raise TypeError(f"descriptor {name} must be numeric")
            return float(value)

        return MpcPairFactory._settings_for_native(
            native,
            estimator_kind,
            control_period=number("control_period"),
            est_q_temp=number("est_q_temp"),
            est_q_dist=number("est_q_dist"),
            est_r_meas=number("est_r_meas"),
        )

    @staticmethod
    def _native_from_descriptor(descriptor: GreyControlPairDescriptor) -> GreyBoxMPCConfig:
        configuration = descriptor.configuration

        def number(name: str) -> float:
            value = configuration.get(name)
            if isinstance(value, bool) or not isinstance(value, Real):
                raise TypeError(f"descriptor {name} must be numeric")
            normalized = float(value)
            return normalized

        def integer(name: str) -> int:
            value = configuration.get(name)
            if isinstance(value, bool) or not isinstance(value, Integral):
                raise TypeError(f"descriptor {name} must be an integer")
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
