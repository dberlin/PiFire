from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np
import numpy.typing as npt
import pytest

from controller.acados import GreyBoxMPCConfig
from controller.model_learning.activation import GreyControlPairDescriptor, canonical_snapshot_digest
from controller.mpc_config import DEFAULT_MPC_CONFIG, JsonValue, normalize_config
from controller.mpc_factory import MpcPairConfiguration, MpcPairFactory, NativeTiming, OwnedMpcPair


CYCLE: dict[str, JsonValue] = {"u_min": 0.1, "u_max": 0.9}


class Estimator:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.closed = 0
        self.state = np.zeros(10, dtype=float)

    def update(self, normalized_combustion_load: float, y_measured: float) -> npt.NDArray[np.float64]:
        del normalized_combustion_load, y_measured
        return self.state.copy()

    def close(self) -> None:
        self.closed += 1
        self.events.append("estimator")


class EstimatorFactory:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.instances: list[Estimator] = []

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
        del C_c, h_amb, T_amb, t_step, q_temp, q_dist, r_meas, theta, n_delay, K_Q, sigma
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
    ) -> None:
        self.config = config
        self.events = events
        self.solve_failure = solve_failure
        self.nonfinite = nonfinite
        self.short_sequence = short_sequence
        self.nonfinite_objective = nonfinite_objective
        self.closed = 0
        self.calls = 0

    def solve(
        self,
        state: npt.ArrayLike,
        *,
        setpoint_c: float,
        q_previous: float,
        equilibrium_q: float,
    ) -> Solve:
        del state, setpoint_c, q_previous, equilibrium_q
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
) -> MpcPairConfiguration:
    settings = normalize_config(dict(DEFAULT_MPC_CONFIG, estimator=estimator, n_horizon=5))
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

    pair_factory = MpcPairFactory(
        normalize_config(DEFAULT_MPC_CONFIG),
        "C",
        CYCLE,
        adjust_load=lambda load, _temperature: load,
        model_authority=authority,
        on_policy_failure=lambda _error: None,
        ekf_factory=ekf,
        kf_factory=kf,
        solver_factory=solver,
        monotonic=lambda: next(ticks),
    )
    return pair_factory, ekf, kf, solver


def descriptor(configuration: dict[str, object], *, estimator: str = "ekf") -> GreyControlPairDescriptor:
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(configuration),
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
    assert pair.descriptor.configuration == native
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
    configured = pair_factory.configured(
        dict(DEFAULT_MPC_CONFIG, theta=51.0),
        candidate_generation=0,
        role_generation=0,
    )
    assert configured.model_identified


def test_restore_rejects_malformed_numeric_and_integer_descriptor_fields() -> None:
    events: list[str] = []
    pair_factory, _ekf, _kf, _solvers = factory(events)
    native = GreyBoxMPCConfig(horizon_steps=5)
    values = {name: getattr(native, name) for name in native.__dataclass_fields__}

    for field, invalid in (("C_c", "bad"), ("horizon_steps", 5.5)):
        malformed = dict(values, **{field: invalid})
        with pytest.raises(ValueError, match=field):
            pair_factory.restore(descriptor(malformed))


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
