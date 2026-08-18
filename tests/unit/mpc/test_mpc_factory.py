from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Literal, Never

import numpy as np
import numpy.typing as npt
import pytest

from controller.acados import GreyBoxMPCConfig
from controller.model_learning.activation import (
    ActivationPhase,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
    canonical_snapshot_digest,
    recover_startup_activation,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.model_learning.calibration import CalibrationDecision, CalibrationProgress
from controller.mpc_config import DEFAULT_MPC_CONFIG, JsonValue, normalize_config
from controller.mpc_factory import MpcPairConfiguration, MpcPairFactory, NativeTiming, OwnedMpcPair


CYCLE: dict[str, JsonValue] = {"u_min": 0.1, "u_max": 0.9}


def _inactive_calibration(_load, _temperature, _forecast) -> CalibrationDecision:
    return CalibrationDecision(False, 0.0, None, CalibrationProgress())


class Estimator:
    def __init__(
        self,
        events: list[str],
        *,
        close_failure: BaseException | None = None,
    ) -> None:
        self.events = events
        self.close_failure = close_failure
        self.closed = 0
        self.state = np.zeros(10, dtype=float)

    def update(self, normalized_combustion_load: float, y_measured: float) -> npt.NDArray[np.float64]:
        del normalized_combustion_load, y_measured
        return self.state.copy()

    def close(self) -> None:
        self.closed += 1
        self.events.append("estimator")
        if self.close_failure is not None:
            raise self.close_failure


def test_owned_pair_close_retries_only_unfinished_real_core_resources() -> None:
    events: list[str] = []
    pair_factory, ekf, _kf, solvers = factory(events)
    configuration = pair_factory.configured(
        dict(DEFAULT_MPC_CONFIG, estimator="ekf"),
        candidate_generation=0,
        role_generation=0,
        model_identified=True,
    )
    pair = pair_factory.build(configuration, authorized=False)
    estimator = ekf.instances[-1]
    solver = solvers.instances[-1]
    solver.close_failure = RuntimeError("solver close failed once")

    with pytest.raises(RuntimeError, match="complete grey numerical pair"):
        pair.close()
    assert pair.closed
    assert solver.closed == 1
    assert estimator.closed == 1

    solver.close_failure = None
    pair.close()
    assert solver.closed == 2
    assert estimator.closed == 1


class EstimatorFactory:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.instances: list[Estimator] = []
        self.construction_inputs: list[tuple[float, float, float, float]] = []

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
    ) -> Estimator:
        self.construction_inputs.append((t_step, q_temp, q_dist, r_meas))
        del C_c, h_amb, T_amb, theta, n_delay, K_Q, sigma
        estimator = Estimator(self.events)
        self.instances.append(estimator)
        return estimator


@dataclass(frozen=True, slots=True)
class Diagnostics:
    status: int = 0
    backend_status: int = 0
    iterations: int = 2
    solve_time_s: float = 0.001
    objective: float = 1.0
    kkt_residual: float = 0.0
    constraint_residual: float = 0.0
    warm_started: bool = True


@dataclass(frozen=True, slots=True)
class Solve:
    sequence_q: npt.NDArray[np.float64]
    sequence_residual: npt.NDArray[np.float64]
    objective: float
    diagnostics: Diagnostics


class Solver:
    def __init__(
        self,
        config: GreyBoxMPCConfig,
        events: list[str],
        *,
        solve_failure: BaseException | None = None,
        nonfinite: bool = False,
        short_sequence: bool = False,
        nonfinite_objective: bool = False,
        close_failure: BaseException | None = None,
    ) -> None:
        self.config = config
        self.events = events
        self.solve_failure = solve_failure
        self.nonfinite = nonfinite
        self.short_sequence = short_sequence
        self.nonfinite_objective = nonfinite_objective
        self.close_failure = close_failure
        self.closed = 0
        self.calls = 0
        self.states: list[npt.NDArray[np.float64]] = []

    def solve(
        self,
        state: npt.ArrayLike,
        *,
        setpoint_c: float,
        q_previous: float,
        equilibrium_q: float,
    ) -> Solve:
        self.states.append(np.asarray(state, dtype=float).copy())
        del setpoint_c, q_previous, equilibrium_q
        self.calls += 1
        if self.solve_failure is not None:
            raise self.solve_failure
        length = self.config.horizon_steps - 1 if self.short_sequence else self.config.horizon_steps
        return Solve(
            sequence_q=np.full(length, np.nan if self.nonfinite else 0.4, dtype=float),
            sequence_residual=np.zeros(length, dtype=float),
            objective=math.nan if self.nonfinite_objective else 1.0,
            diagnostics=Diagnostics(),
        )

    def close(self) -> None:
        self.closed += 1
        self.events.append("solver")
        if self.close_failure is not None:
            raise self.close_failure


class SolverFactory:
    def __init__(
        self,
        events: list[str],
        *,
        build_failure: BaseException | None = None,
        solve_failure: BaseException | None = None,
        mutate_config: bool = False,
        nonfinite: bool = False,
        short_sequence: bool = False,
        nonfinite_objective: bool = False,
    ) -> None:
        self.events = events
        self.build_failure = build_failure
        self.solve_failure = solve_failure
        self.mutate_config = mutate_config
        self.nonfinite = nonfinite
        self.short_sequence = short_sequence
        self.nonfinite_objective = nonfinite_objective
        self.instances: list[Solver] = []

    def __call__(self, config: GreyBoxMPCConfig) -> Solver:
        if self.build_failure is not None:
            raise self.build_failure
        actual = replace(config, theta=config.theta + 1.0) if self.mutate_config else config
        solver = Solver(
            actual,
            self.events,
            solve_failure=self.solve_failure,
            nonfinite=self.nonfinite,
            short_sequence=self.short_sequence,
            nonfinite_objective=self.nonfinite_objective,
        )
        self.instances.append(solver)
        return solver


def configuration(
    *,
    estimator: Literal["ekf", "kf"] = "ekf",
    identified: bool = False,
    generation: int = 0,
    control_period: float = 5.0,
    est_q_temp: float = 1e-2,
    est_q_dist: float = 0.05,
    est_r_meas: float = 0.04,
) -> MpcPairConfiguration:
    settings = normalize_config(
        dict(
            DEFAULT_MPC_CONFIG,
            estimator=estimator,
            n_horizon=5,
            control_period=control_period,
            est_q_temp=est_q_temp,
            est_q_dist=est_q_dist,
            est_r_meas=est_r_meas,
        )
    )
    return MpcPairConfiguration(
        settings=settings,
        estimator_kind=estimator,
        candidate_generation=generation,
        role_generation=generation,
        model_identified=identified,
    )


def factory(
    events: list[str],
    *,
    build_failure: BaseException | None = None,
    solve_failure: BaseException | None = None,
    mutate_config: bool = False,
    nonfinite: bool = False,
    short_sequence: bool = False,
    nonfinite_objective: bool = False,
    clock_values: tuple[float, ...] = (),
    authority_failure: BaseException | None = None,
    base_overrides: dict[str, JsonValue] | None = None,
) -> tuple[MpcPairFactory, EstimatorFactory, EstimatorFactory, SolverFactory]:
    ekf = EstimatorFactory(events)
    kf = EstimatorFactory(events)
    solver = SolverFactory(
        events,
        build_failure=build_failure,
        solve_failure=solve_failure,
        mutate_config=mutate_config,
        nonfinite=nonfinite,
        short_sequence=short_sequence,
        nonfinite_objective=nonfinite_objective,
    )
    ticks = iter(clock_values)

    def authority():
        if authority_failure is not None:
            raise authority_failure
        return 0, None

    base_configuration = dict(DEFAULT_MPC_CONFIG)
    if base_overrides is not None:
        base_configuration.update(base_overrides)
    pair_factory = MpcPairFactory(
        normalize_config(base_configuration),
        "C",
        CYCLE,
        advance_calibration=_inactive_calibration,
        model_authority=authority,
        on_policy_failure=lambda _error: None,
        ekf_factory=ekf,
        kf_factory=kf,
        solver_factory=solver,
        monotonic=lambda: next(ticks),
    )
    return pair_factory, ekf, kf, solver


def descriptor(configuration: Mapping[str, object], *, estimator: str = "ekf") -> GreyControlPairDescriptor:
    model_configuration = {
        key: value
        for key, value in configuration.items()
        if key not in {"control_period", "est_q_temp", "est_q_dist", "est_r_meas"}
    }
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(model_configuration),
        configuration=configuration,
        estimator_kind=estimator,
        solver_kind="acados-grey",
        candidate_generation=1,
        role_generation=1,
    )


@pytest.mark.parametrize("kind", ["ekf", "kf"])
def test_build_selects_estimator_and_transfers_one_complete_owned_core(
    kind: Literal["ekf", "kf"],
) -> None:
    events: list[str] = []
    pair_factory, ekf, kf, solvers = factory(events)
    pair = pair_factory.build(configuration(estimator=kind), authorized=True)

    assert isinstance(pair, OwnedMpcPair)
    assert pair.authorized
    assert len(ekf.instances) == (kind == "ekf")
    assert len(kf.instances) == (kind == "kf")
    assert pair.estimator is (ekf.instances or kf.instances)[0]
    assert pair.solver is solvers.instances[0]
    native = {name: getattr(pair.solver.config, name) for name in pair.solver.config.__dataclass_fields__}
    construction = {
        **native,
        "control_period": 5.0,
        "est_q_temp": 1e-2,
        "est_q_dist": 0.05,
        "est_r_meas": 0.04,
    }
    assert pair.descriptor.configuration == construction
    assert pair.descriptor.model_digest == canonical_snapshot_digest(native)


def test_native_build_failure_closes_only_the_partial_estimator_once() -> None:
    events: list[str] = []
    pair_factory, ekf, _kf, solvers = factory(events, build_failure=RuntimeError("native-build"))

    with pytest.raises(RuntimeError, match="native-build"):
        pair_factory.build(configuration(), authorized=False)

    assert ekf.instances[0].closed == 1
    assert events == ["estimator"]
    assert solvers.instances == []


def test_generated_descriptor_digest_mismatch_closes_complete_candidate_in_order() -> None:
    events: list[str] = []
    pair_factory, ekf, _kf, solvers = factory(events, mutate_config=True)

    with pytest.raises(ValueError, match="configuration digest changed"):
        pair_factory.build(
            MpcPairConfiguration(
                settings=GreyBoxMPCConfig(horizon_steps=5),
                estimator_kind="ekf",
                candidate_generation=1,
                role_generation=1,
                model_identified=False,
            ),
            authorized=False,
        )

    assert solvers.instances[0].closed == 1
    assert ekf.instances[0].closed == 1
    assert events == ["solver", "estimator"]


def test_restore_reconstructs_exact_descriptor_and_starts_unauthorized() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(events)
    source = pair_factory.build(configuration(identified=True, generation=7), authorized=True)
    original_descriptor = source.descriptor
    source.close()
    events.clear()

    restored = pair_factory.restore(original_descriptor)

    assert restored.descriptor == original_descriptor
    assert not restored.authorized
    assert restored.solver.config.residual_weight == original_descriptor.configuration["residual_weight"]
    restored.authorize_output()
    assert restored.authorized
    restored.revoke_output()
    assert not restored.authorized


def test_estimator_setting_change_preserves_model_digest_and_changes_ownership_digest() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(events)
    first = pair_factory.build(
        configuration(est_q_temp=0.02),
        authorized=False,
    )
    second = pair_factory.build(
        configuration(est_q_temp=0.03),
        authorized=False,
    )

    assert first.descriptor.configuration["est_q_temp"] == 0.02
    assert second.descriptor.configuration["est_q_temp"] == 0.03
    assert first.descriptor.model_digest == second.descriptor.model_digest
    assert first.descriptor.ownership_digest != second.descriptor.ownership_digest
    changed_configuration = dict(first.descriptor.configuration)
    changed_configuration["est_q_temp"] = 0.03
    with pytest.raises(ValueError, match="ownership_digest"):
        replace(first.descriptor, configuration=changed_configuration)
    first.close()
    second.close()


def test_restore_uses_descriptor_estimator_settings_instead_of_factory_base() -> None:
    source_events: list[str] = []
    source_factory, _source_ekf, _source_kf, _source_solvers = factory(source_events)
    source = source_factory.build(
        configuration(
            control_period=2.5,
            est_q_temp=0.12,
            est_q_dist=0.13,
            est_r_meas=0.14,
        ),
        authorized=False,
    )
    durable_descriptor = source.descriptor
    source.close()

    restore_events: list[str] = []
    restore_ticks = tuple(value for sample in range(5) for value in (sample * 0.01, sample * 0.01 + 0.001))
    restore_factory, restored_ekf, _restored_kf, _restored_solvers = factory(
        restore_events,
        clock_values=restore_ticks,
        base_overrides={
            "control_period": 9.0,
            "est_q_temp": 0.91,
            "est_q_dist": 0.92,
            "est_r_meas": 0.93,
        },
    )
    restored = restore_factory.restore(durable_descriptor)

    assert restored.descriptor == durable_descriptor
    assert restored_ekf.construction_inputs == [(2.5, 0.12, 0.13, 0.14)]
    timing = restore_factory.dry_solve(restored, temperature_c=70.0)
    assert timing.limit_ms == 500.0
    restored.close()


def test_restore_rejects_unknown_kinds_without_constructing_resources() -> None:
    events: list[str] = []
    pair_factory, ekf, kf, solvers = factory(events)
    source = pair_factory.build(configuration(), authorized=True)
    descriptors = (
        replace(source.descriptor, solver_kind="other", ownership_digest=""),
        replace(source.descriptor, estimator_kind="other", ownership_digest=""),
    )
    source.close()
    ekf.instances.clear()
    kf.instances.clear()
    solvers.instances.clear()
    events.clear()

    for invalid in descriptors:
        with pytest.raises(ValueError, match="unsupported"):
            pair_factory.restore(invalid)

    assert not ekf.instances and not kf.instances and not solvers.instances
    assert events == []


def test_dry_solve_returns_deterministic_timing_without_transferring_or_closing() -> None:
    events: list[str] = []
    ticks = tuple(value for sample in range(5) for value in (sample * 0.01, sample * 0.01 + 0.002))
    pair_factory, _ekf, _kf, solvers = factory(events, clock_values=ticks)
    pair = pair_factory.build(configuration(), authorized=False)

    timing = pair_factory.dry_solve(pair, temperature_c=70.0)

    assert isinstance(timing, NativeTiming)
    assert timing.samples == 5
    assert timing.p99_ms == pytest.approx(2.0)
    assert timing.limit_ms == 1_000.0
    assert timing.accepted
    assert solvers.instances[0].calls == 5
    assert not pair.closed
    expected_state = np.zeros(pair.solver.config.state_size, dtype=float)
    expected_state[pair.solver.config.delay_states] = 70.0
    assert len(solvers.instances[0].states) == 5
    for state in solvers.instances[0].states:
        np.testing.assert_array_equal(state, expected_state)


@pytest.mark.parametrize(
    ("failure_kind", "message"),
    [
        ("exception", "dry-solve"),
        ("nonfinite", "finite"),
        ("short", "finite"),
        ("objective", "objective"),
    ],
)
def test_dry_solve_failure_closes_only_the_request_owned_pair_once(
    failure_kind: str,
    message: str,
) -> None:
    events: list[str] = []
    if failure_kind == "exception":
        built = factory(events, clock_values=(0.0,), solve_failure=RuntimeError("dry-solve"))
    elif failure_kind == "nonfinite":
        built = factory(events, clock_values=(0.0,), nonfinite=True)
    elif failure_kind == "short":
        built = factory(events, clock_values=(0.0,), short_sequence=True)
    else:
        built = factory(events, clock_values=(0.0,), nonfinite_objective=True)
    pair_factory, ekf, _kf, solvers = built
    pair = pair_factory.build(configuration(), authorized=False)

    with pytest.raises((RuntimeError, ValueError), match=message):
        pair_factory.dry_solve(pair, temperature_c=70.0)
    pair.close()

    assert solvers.instances[0].closed == 1
    assert ekf.instances[0].closed == 1
    assert events == ["solver", "estimator"]


def test_adopted_candidate_validates_digest_and_closes_in_order_once() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(events)
    native = GreyBoxMPCConfig(horizon_steps=5)
    estimator = Estimator(events)
    solver = Solver(native, events)

    pair = pair_factory.adopt(
        MpcPairConfiguration(
            settings=native,
            estimator_kind="ekf",
            candidate_generation=3,
            role_generation=2,
            model_identified=False,
        ),
        estimator,
        solver,
        authorized=False,
    )

    assert pair.estimator is estimator
    assert pair.solver is solver
    pair.close()
    pair.close()
    assert events == ["solver", "estimator"]
    assert solver.closed == estimator.closed == 1


def test_adopt_failure_closes_both_request_owned_handles() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(
        events,
        authority_failure=RuntimeError("authority"),
    )
    estimator = Estimator(events)
    solver = Solver(GreyBoxMPCConfig(horizon_steps=5), events)

    with pytest.raises(RuntimeError, match="authority"):
        pair_factory.adopt(configuration(), estimator, solver, authorized=False)

    assert events == ["solver", "estimator"]


def test_configured_rejects_unknown_estimator_and_infers_identification() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(events)

    with pytest.raises(ValueError, match="estimator"):
        pair_factory.configured(
            dict(DEFAULT_MPC_CONFIG, estimator="other"),
            candidate_generation=0,
            role_generation=0,
        )
    # A pasted `controller/update_mpc.py` fit: the whole solved set moved.
    configured = pair_factory.configured(
        dict(DEFAULT_MPC_CONFIG, theta=51.0, K_Q=412.7, C_c=286.4),
        candidate_generation=0,
        role_generation=0,
    )
    assert configured.model_identified

    # One parameter off its default is an edit, not a fit.
    configured = pair_factory.configured(
        dict(DEFAULT_MPC_CONFIG, theta=51.0),
        candidate_generation=0,
        role_generation=0,
    )
    assert not configured.model_identified


def test_restore_rejects_malformed_numeric_and_integer_descriptor_fields() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(events)
    native = GreyBoxMPCConfig(horizon_steps=5)
    values = {
        **{name: getattr(native, name) for name in native.__dataclass_fields__},
        "control_period": 5.0,
        "est_q_temp": 1e-2,
        "est_q_dist": 0.05,
        "est_r_meas": 0.04,
    }

    for field, invalid in (("C_c", "bad"), ("horizon_steps", 5.5)):
        malformed = dict(values, **{field: invalid})
        with pytest.raises(ValueError, match=field):
            pair_factory.restore(descriptor(malformed))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"est_q_temp": "bad"}, "est_q_temp"),
        ({"est_q_temp": None}, "est_q_temp"),
        ({"unexpected": 1.0}, "fields"),
    ],
)
def test_restore_rejects_malformed_incomplete_or_extra_estimator_settings_before_build(
    change: dict[str, object],
    message: str,
) -> None:
    events: list[str] = []
    pair_factory, ekf, kf, solvers = factory(events)
    source = pair_factory.build(configuration(), authorized=False)
    durable_configuration = dict(source.descriptor.configuration)
    source.close()
    ekf.instances.clear()
    kf.instances.clear()
    solvers.instances.clear()
    events.clear()
    if "est_q_temp" in change and change["est_q_temp"] is None:
        durable_configuration.pop("est_q_temp")
    else:
        durable_configuration.update(change)
    with pytest.raises(ValueError, match=message):
        malformed = descriptor(durable_configuration)
        pair_factory.restore(malformed)

    assert ekf.instances == [] and kf.instances == [] and solvers.instances == []
    assert events == []


def test_restore_explicitly_migrates_the_exact_legacy_native_descriptor() -> None:
    events: list[str] = []
    pair_factory, ekf, _kf, _solvers = factory(
        events,
        base_overrides={
            "control_period": 9.0,
            "est_q_temp": 0.91,
            "est_q_dist": 0.92,
            "est_r_meas": 0.93,
        },
    )
    native = GreyBoxMPCConfig(horizon_steps=5)
    legacy_configuration = {name: getattr(native, name) for name in native.__dataclass_fields__}
    legacy = descriptor(legacy_configuration)

    migrated = pair_factory.migrate_legacy_descriptor(legacy)
    restored = pair_factory.restore(migrated)

    assert restored.descriptor == migrated
    assert migrated.model_digest == legacy.model_digest
    assert migrated.ownership_digest != legacy.ownership_digest
    assert migrated.configuration == {
        **legacy_configuration,
        "control_period": 5.0,
        "est_q_temp": 1e-2,
        "est_q_dist": 0.05,
        "est_r_meas": 0.04,
    }
    assert ekf.construction_inputs == [(5.0, 1e-2, 0.05, 0.04)]
    restored.close()


@pytest.mark.parametrize(
    ("theta", "residual_weight"),
    ((50.0, 0.0), (40.0, 1_000.0)),
)
def test_restore_explicitly_migrates_pre_task3_nested_v4_descriptor(
    theta: float,
    residual_weight: float,
) -> None:
    events: list[str] = []
    pair_factory, ekf, _kf, _solvers = factory(
        events,
        base_overrides={
            "control_period": 9.0,
            "est_q_temp": 0.91,
            "est_q_dist": 0.92,
            "est_r_meas": 0.93,
        },
    )
    legacy_configuration = {
        "schema": "pifire-grey-box-model/v4",
        "n_delay": 8,
        "parameters": {
            "C_c": 320.0,
            "K_Q": 350.0,
            "theta": theta,
            "h_amb": 0.5,
            "T_amb": 20.0,
            "sigma": 1.4e-9,
        },
    }
    legacy = descriptor(legacy_configuration)

    migrated = pair_factory.migrate_legacy_descriptor(legacy)
    restored = pair_factory.restore(migrated)

    assert restored.descriptor == migrated
    assert migrated.model_digest != legacy.model_digest
    assert migrated.configuration["theta"] == theta
    assert migrated.configuration["residual_weight"] == residual_weight
    assert migrated.configuration["horizon_steps"] == DEFAULT_MPC_CONFIG["n_horizon"]
    assert migrated.configuration["delay_states"] == 8
    assert migrated.configuration["state_size"] == 10
    assert migrated.configuration["timestep_s"] == 25.0
    assert migrated.configuration["control_period"] == 5.0
    assert migrated.configuration["est_q_temp"] == 1e-2
    assert migrated.configuration["est_q_dist"] == 0.05
    assert migrated.configuration["est_r_meas"] == 0.04
    assert ekf.construction_inputs == [(5.0, 1e-2, 0.05, 0.04)]
    restored.close()


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("schema", "schema"),
        ("boolean-delay", "integer 8"),
        ("float-delay", "integer 8"),
        ("wrong-delay", "integer 8"),
        ("nonmapping-parameters", "parameters"),
        ("missing-parameter", "parameters"),
        ("boolean-parameter", "numeric"),
        ("string-parameter", "numeric"),
    ),
)
def test_nested_v4_migration_rejects_every_malformed_legacy_shape(
    case: str,
    message: str,
) -> None:
    legacy_configuration: dict[str, JsonValue] = {
        "schema": "pifire-grey-box-model/v4",
        "n_delay": 8,
        "parameters": {
            "C_c": 320.0,
            "K_Q": 350.0,
            "theta": 50.0,
            "h_amb": 0.5,
            "T_amb": 20.0,
            "sigma": 1.4e-9,
        },
    }
    if case == "schema":
        legacy_configuration["schema"] = "other"
    elif case == "boolean-delay":
        legacy_configuration["n_delay"] = True
    elif case == "float-delay":
        legacy_configuration["n_delay"] = 8.0
    elif case == "wrong-delay":
        legacy_configuration["n_delay"] = 7
    elif case == "nonmapping-parameters":
        legacy_configuration["parameters"] = []
    else:
        parameters = legacy_configuration["parameters"]
        assert isinstance(parameters, dict)
        if case == "missing-parameter":
            parameters.pop("theta")
        elif case == "boolean-parameter":
            parameters["theta"] = True
        else:
            parameters["theta"] = "bad"
    legacy = descriptor(legacy_configuration)

    with pytest.raises(ValueError, match=message):
        MpcPairFactory.migrate_legacy_descriptor(legacy)


def test_startup_recovery_migrates_every_legacy_pair_before_exact_restore() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(events)
    native = GreyBoxMPCConfig(horizon_steps=5)
    legacy_configuration = {name: getattr(native, name) for name in native.__dataclass_fields__}
    legacy = descriptor(legacy_configuration)
    serialized = json.dumps(legacy.to_dict())
    state = SimpleNamespace(
        phase=ActivationPhase.ACTIVE,
        transaction_id="a" * 64,
        incumbent_pair_json=serialized,
        candidate_pair_json=serialized,
        rollback_pair_json=serialized,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        evidence_decision_id="legacy-startup",
        reason=None,
    )

    def persist_aborted(_record: PreparedActivationRecord) -> Never:
        raise AssertionError("active startup must not persist an aborted record")

    recovery = recover_startup_activation(
        state,
        persist_aborted=persist_aborted,
    )
    migrated = pair_factory.migrate_legacy_descriptor(legacy)

    assert recovery.restore == migrated
    assert recovery.rollback == migrated
    assert recovery.record.incumbent == migrated
    assert recovery.record.candidate == migrated
    assert recovery.record.rollback == migrated
    restored = pair_factory.restore(recovery.restore)
    assert restored.descriptor == recovery.restore
    restored.close()


def test_invalid_dry_solve_temperature_fails_before_native_work() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, solvers = factory(events)
    pair = pair_factory.build(configuration(), authorized=False)
    with pytest.raises(ValueError, match="temperature"):
        pair_factory.dry_solve(pair, temperature_c=math.nan)

    assert pair.closed
    assert solvers.instances[0].calls == 0


def test_closed_pair_cannot_be_reauthorized() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(events)
    pair = pair_factory.build(configuration(), authorized=False)
    pair.close()

    with pytest.raises(RuntimeError, match="closed MPC pair"):
        pair.authorize_output()
    assert not pair_factory.validate(pair)


def test_configuration_runtime_validation_rejects_each_invalid_identity_field() -> None:
    settings = normalize_config(DEFAULT_MPC_CONFIG)
    invalid_values = (
        ("other", 0, 0, False),
        ("ekf", True, 0, False),
        ("ekf", 0, -1, False),
        ("ekf", 0, 0, 1),
    )

    for estimator_kind, candidate_generation, role_generation, model_identified in invalid_values:
        with pytest.raises(ValueError):
            MpcPairConfiguration(
                settings,
                estimator_kind,
                candidate_generation,
                role_generation,
                model_identified,
            )


def test_owned_pair_runtime_validation_rejects_wrong_core_and_descriptor_types() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(events)
    pair = pair_factory.build(configuration(), authorized=False)

    with pytest.raises(TypeError, match="core"):
        OwnedMpcPair("not-a-core", pair.descriptor)
    with pytest.raises(TypeError, match="descriptor"):
        OwnedMpcPair(pair.core, "not-a-descriptor")
    pair.close()


def test_configured_accepts_kf_and_rejects_non_string_estimator() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(events)

    configured = pair_factory.configured(
        dict(DEFAULT_MPC_CONFIG, estimator="KF"),
        candidate_generation=2,
        role_generation=3,
    )
    assert configured.estimator_kind == "kf"
    with pytest.raises(ValueError, match="estimator"):
        pair_factory.configured(
            dict(DEFAULT_MPC_CONFIG, estimator=7),
            candidate_generation=0,
            role_generation=0,
        )


def test_authorization_validation_precedes_build_and_adopt_ownership_transfer() -> None:
    events: list[str] = []
    pair_factory, ekf, _kf, solvers = factory(events)

    with pytest.raises(ValueError, match="authorized"):
        pair_factory.build(configuration(), authorized=1)
    assert ekf.instances == [] and solvers.instances == []

    estimator = Estimator(events)
    solver = Solver(GreyBoxMPCConfig(horizon_steps=5), events)
    with pytest.raises(ValueError, match="authorized"):
        pair_factory.adopt(configuration(), estimator, solver, authorized=1)
    assert estimator.closed == solver.closed == 0


def test_adopt_can_transfer_an_already_authorized_pair() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(events)
    native = GreyBoxMPCConfig(horizon_steps=5)

    pair = pair_factory.adopt(
        pair_factory.native(
            native,
            estimator_kind="ekf",
            candidate_generation=1,
            role_generation=2,
        ),
        Estimator(events),
        Solver(native, events),
        authorized=True,
    )

    assert pair.authorized
    pair.close()


def test_restore_covers_kf_and_closes_case_changed_descriptor_mismatch() -> None:
    events: list[str] = []
    pair_factory, _ekf, kf, solvers = factory(events)
    source = pair_factory.build(configuration(estimator="kf"), authorized=False)
    restored = pair_factory.restore(source.descriptor)
    assert restored.descriptor.estimator_kind == "kf"
    assert len(kf.instances) == 2
    source.close()
    restored.close()

    ekf_source = pair_factory.build(configuration(), authorized=False)
    mismatched = replace(
        ekf_source.descriptor,
        estimator_kind="EKF",
        ownership_digest="",
    )
    ekf_source.close()
    before = len(solvers.instances)
    with pytest.raises(ValueError, match="descriptor changed"):
        pair_factory.restore(mismatched)
    assert solvers.instances[before].closed == 1


class RejectingFactory(MpcPairFactory):
    def validate(self, pair: OwnedMpcPair) -> bool:
        del pair
        return False


def test_public_adopt_closes_pair_when_postconstruction_validation_fails() -> None:
    events: list[str] = []
    ekf = EstimatorFactory(events)
    kf = EstimatorFactory(events)
    solver_factory = SolverFactory(events)
    pair_factory = RejectingFactory(
        normalize_config(DEFAULT_MPC_CONFIG),
        "C",
        CYCLE,
        advance_calibration=_inactive_calibration,
        model_authority=lambda: (0, None),
        on_policy_failure=lambda _error: None,
        ekf_factory=ekf,
        kf_factory=kf,
        solver_factory=solver_factory,
    )
    native = GreyBoxMPCConfig(horizon_steps=5)
    estimator = Estimator(events)
    solver = Solver(native, events)

    with pytest.raises(ValueError, match="validation"):
        pair_factory.adopt(
            pair_factory.native(
                native,
                estimator_kind="ekf",
                candidate_generation=0,
                role_generation=0,
            ),
            estimator,
            solver,
            authorized=False,
        )

    assert solver.closed == estimator.closed == 1


@pytest.mark.parametrize(
    "ticks",
    [
        (1.0, 0.0),
        (0.0, math.nan),
    ],
)
def test_dry_solve_rejects_invalid_elapsed_timing_and_closes_pair(
    ticks: tuple[float, float],
) -> None:
    events: list[str] = []
    pair_factory, ekf, _kf, solvers = factory(events, clock_values=ticks)
    pair = pair_factory.build(configuration(), authorized=False)

    with pytest.raises(ValueError, match="timing"):
        pair_factory.dry_solve(pair, temperature_c=70.0)

    assert solvers.instances[0].closed == ekf.instances[0].closed == 1


def test_adopt_surfaces_cleanup_failure_after_attempting_both_owned_closes() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(
        events,
        authority_failure=RuntimeError("authority"),
    )
    estimator = Estimator(events, close_failure=RuntimeError("estimator-close"))
    solver = Solver(
        GreyBoxMPCConfig(horizon_steps=5),
        events,
        close_failure=RuntimeError("solver-close"),
    )

    with pytest.raises(RuntimeError, match="could not close failed MPC pair construction"):
        pair_factory.adopt(configuration(), estimator, solver, authorized=False)

    assert events == ["solver", "estimator"]
    assert solver.closed == estimator.closed == 1


def test_adopt_preserves_original_failure_for_protocol_components_without_close() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(
        events,
        authority_failure=RuntimeError("authority"),
    )

    class NonClosableEstimator:
        def update(
            self,
            normalized_combustion_load: float,
            y_measured: float,
        ) -> npt.NDArray[np.float64]:
            del normalized_combustion_load, y_measured
            return np.zeros(10, dtype=float)

    class NonClosableSolver:
        config = GreyBoxMPCConfig(horizon_steps=5)

        def solve(
            self,
            state: npt.ArrayLike,
            *,
            setpoint_c: float,
            q_previous: float,
            equilibrium_q: float,
        ) -> Solve:
            del state, setpoint_c, q_previous, equilibrium_q
            return Solve(
                sequence_q=np.zeros(5, dtype=float),
                sequence_residual=np.zeros(5, dtype=float),
                objective=0.0,
                diagnostics=Diagnostics(),
            )

    with pytest.raises(RuntimeError, match="authority"):
        pair_factory.adopt(
            configuration(),
            NonClosableEstimator(),
            NonClosableSolver(),
            authorized=False,
        )
