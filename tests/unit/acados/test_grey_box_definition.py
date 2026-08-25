from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import warnings

import casadi as ca
import numpy as np

from controller.acados.codegen.grey_box_ocp import (
    GREY_PARAMETER_NAMES,
    build_grey_box_ocp,
    grey_box_discrete_map,
    grey_box_rhs,
)


DEFAULT_PARAMETERS = np.array(
    [
        320.0,
        0.5,
        20.0,
        50.0,
        350.0,
        1.4e-9,
        125.0,
        0.4,
        2.0,
        3.0,
        4.0,
        5.0,
    ],
    dtype=np.float64,
)


def _independent_rhs(state: np.ndarray, residual: float, parameters: np.ndarray) -> np.ndarray:
    C_c, h_amb, T_amb, theta, K_Q, sigma = parameters[:6]
    equilibrium_q = parameters[7]
    q_total = equilibrium_q + residual
    delay_time_constant = theta / 8.0

    derivative = np.empty(10, dtype=np.float64)
    derivative[0] = (q_total - state[0]) / delay_time_constant
    derivative[1:8] = (state[:7] - state[1:8]) / delay_time_constant
    derivative[8] = (
        K_Q * state[7]
        - h_amb * (state[8] - T_amb)
        - sigma * ((state[8] + 273.15) ** 4 - (T_amb + 273.15) ** 4)
        + state[9]
    ) / C_c
    derivative[9] = 0.0
    return derivative


def _independent_discrete_map(state: np.ndarray, residual: float, parameters: np.ndarray) -> np.ndarray:
    physical_state = state.copy()
    substep = 25.0 / 8.0
    for _ in range(8):
        k1 = _independent_rhs(physical_state, residual, parameters)
        k2 = _independent_rhs(physical_state + 0.5 * substep * k1, residual, parameters)
        k3 = _independent_rhs(physical_state + 0.5 * substep * k2, residual, parameters)
        k4 = _independent_rhs(physical_state + substep * k3, residual, parameters)
        physical_state = physical_state + substep * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
    return physical_state


def test_parameter_order_is_stable() -> None:
    assert GREY_PARAMETER_NAMES == (
        "C_c",
        "h_amb",
        "T_amb",
        "theta",
        "K_Q",
        "sigma",
        "setpoint_c",
        "equilibrium_q",
        "temperature_weight",
        "terminal_weight",
        "move_weight",
        "residual_weight",
    )


def test_rhs_matches_independent_physical_expression() -> None:
    state = np.array(
        [0.15, 0.22, 0.31, 0.42, 0.51, 0.58, 0.63, 0.67, 121.5, -2.25],
        dtype=np.float64,
    )

    actual = grey_box_rhs(state, residual=-0.08, parameters=DEFAULT_PARAMETERS)

    np.testing.assert_allclose(
        actual,
        _independent_rhs(state, -0.08, DEFAULT_PARAMETERS),
        rtol=1e-13,
        atol=1e-13,
    )


def test_discrete_map_matches_eight_explicit_rk4_substeps_over_25_seconds() -> None:
    state = np.array(
        [0.12, 0.18, 0.25, 0.33, 0.41, 0.49, 0.56, 0.61, 118.0, 0.75],
        dtype=np.float64,
    )

    actual = grey_box_discrete_map(
        state,
        previous_residual=-0.03,
        residual=0.07,
        parameters=DEFAULT_PARAMETERS,
    )

    expected = np.concatenate((_independent_discrete_map(state, 0.07, DEFAULT_PARAMETERS), [0.07]))
    np.testing.assert_allclose(actual, expected, rtol=1e-13, atol=1e-13)


def test_discrete_map_updates_solver_only_previous_residual() -> None:
    next_state = grey_box_discrete_map(
        np.array([0.1] * 8 + [120.0, 0.5]),
        previous_residual=-0.1,
        residual=0.2,
        parameters=DEFAULT_PARAMETERS,
    )

    assert next_state.shape == (11,)
    assert next_state[-1] == 0.2


def _fake_acados_ocp() -> SimpleNamespace:
    ocp = SimpleNamespace(
        model=SimpleNamespace(),
        cost=SimpleNamespace(),
        constraints=SimpleNamespace(),
        solver_options=SimpleNamespace(),
        code_gen_options=SimpleNamespace(),
    )

    def make_consistent() -> None:
        ocp.dims = SimpleNamespace(nh_0=1)

    ocp.make_consistent = make_consistent
    return ocp


def _build_fake_ocp(monkeypatch) -> SimpleNamespace:
    monkeypatch.setattr(
        "controller.acados.codegen.grey_box_ocp._load_codegen_api",
        lambda: (ca, _fake_acados_ocp),
    )
    return build_grey_box_ocp()


def test_build_grey_box_ocp_declares_fixed_generated_problem(monkeypatch) -> None:
    ocp = _build_fake_ocp(monkeypatch)

    assert ocp.model.name == "pifire_grey"
    assert ocp.name == "pifire_grey"
    assert ocp.model.x.shape == (11, 1)
    assert ocp.model.u.shape == (1, 1)
    assert ocp.model.p.shape == (12, 1)
    assert ocp.solver_options.N_horizon == 24
    assert ocp.solver_options.tf == 600.0
    assert ocp.solver_options.integrator_type == "DISCRETE"
    assert ocp.solver_options.nlp_solver_type == "SQP"
    assert ocp.solver_options.hessian_approx == "GAUSS_NEWTON"
    assert ocp.solver_options.qp_solver == "PARTIAL_CONDENSING_HPIPM"
    assert ocp.solver_options.nlp_solver_max_iter == 10
    assert ocp.solver_options.qp_solver_warm_start > 0
    np.testing.assert_array_equal(ocp.solver_options.cost_scaling, np.ones(25))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        ocp.make_consistent()
    assert ocp.dims.nh_0 == 1
    assert Path(ocp.code_gen_options.code_export_directory).parts[-3:] == (
        "native",
        "generated",
        "grey_box",
    )


def test_stage_and_terminal_residuals_and_load_constraint_match_contract(
    monkeypatch,
) -> None:
    ocp = _build_fake_ocp(monkeypatch)
    stage = ca.Function("stage_contract", [ocp.model.x, ocp.model.u, ocp.model.p], [ocp.model.cost_y_expr])
    terminal = ca.Function("terminal_contract", [ocp.model.x, ocp.model.p], [ocp.model.cost_y_expr_e])
    load = ca.Function("load_contract", [ocp.model.u, ocp.model.p], [ocp.model.con_h_expr])
    initial_load = ca.Function(
        "initial_load_contract",
        [ocp.model.u, ocp.model.p],
        [ocp.model.con_h_expr_0],
    )
    state = np.array([0.1] * 8 + [122.0, 0.5, -0.2])

    np.testing.assert_allclose(
        np.asarray(stage(state, 0.1, DEFAULT_PARAMETERS)).reshape(-1),
        [np.sqrt(2.0) * -3.0, np.sqrt(4.0) * 0.3, np.sqrt(5.0) * 0.1],
    )
    np.testing.assert_allclose(
        np.asarray(terminal(state, DEFAULT_PARAMETERS)).reshape(-1),
        [np.sqrt(3.0) * -3.0],
    )
    np.testing.assert_allclose(np.asarray(load(0.1, DEFAULT_PARAMETERS)).reshape(-1), [0.5])
    np.testing.assert_allclose(np.asarray(initial_load(0.1, DEFAULT_PARAMETERS)).reshape(-1), [0.5])
    np.testing.assert_array_equal(ocp.constraints.lh, [0.0])
    np.testing.assert_array_equal(ocp.constraints.uh, [1.0])
    np.testing.assert_array_equal(ocp.constraints.lh_0, [0.0])
    np.testing.assert_array_equal(ocp.constraints.uh_0, [1.0])
