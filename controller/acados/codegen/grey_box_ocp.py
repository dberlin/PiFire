"""Single-source physical equations and acados OCP for the grey-box MPC."""

from __future__ import annotations

import hashlib
import json
from importlib import import_module
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import numpy.typing as npt

from controller.acados.contracts import (
    GREY_DELAY_STATES,
    GREY_HORIZON_CAPACITY,
    GREY_STATE_SIZE,
    GREY_TIMESTEP_S,
    GreyBoxMPCConfig,
)


FloatArray = npt.NDArray[np.float64]
GREY_RK4_SUBSTEPS = 8
GREY_PARAMETER_NAMES = (
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

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_EXPORT_DIRECTORY = _REPOSITORY_ROOT / "native" / "generated" / "grey_box"


def _rhs_terms(state10: Any, residual: Any, parameters: Any) -> tuple[Any, ...]:
    C_c = parameters[0]
    h_amb = parameters[1]
    T_amb = parameters[2]
    theta = parameters[3]
    K_Q = parameters[4]
    sigma = parameters[5]
    equilibrium_q = parameters[7]
    delay_time_constant = theta / GREY_DELAY_STATES
    q_total = equilibrium_q + residual

    delay_derivatives = [(q_total - state10[0]) / delay_time_constant]
    delay_derivatives.extend(
        (state10[index - 1] - state10[index]) / delay_time_constant
        for index in range(1, GREY_DELAY_STATES)
    )
    chamber_derivative = (
        K_Q * state10[7]
        - h_amb * (state10[8] - T_amb)
        - sigma * ((state10[8] + 273.15) ** 4 - (T_amb + 273.15) ** 4)
        + state10[9]
    ) / C_c
    return (*delay_derivatives, chamber_derivative, 0.0)


def _rk4_physical_map(
    state10: Any,
    residual: Any,
    parameters: Any,
    stack: Callable[[tuple[Any, ...]], Any],
) -> Any:
    state = state10
    step = GREY_TIMESTEP_S / GREY_RK4_SUBSTEPS
    for _ in range(GREY_RK4_SUBSTEPS):
        k1 = stack(_rhs_terms(state, residual, parameters))
        k2 = stack(_rhs_terms(state + 0.5 * step * k1, residual, parameters))
        k3 = stack(_rhs_terms(state + 0.5 * step * k2, residual, parameters))
        k4 = stack(_rhs_terms(state + step * k3, residual, parameters))
        state = state + step * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
    return state


def _numeric_inputs(
    state10: npt.ArrayLike, residual: float, parameters: npt.ArrayLike
) -> tuple[FloatArray, float, FloatArray]:
    state = np.asarray(state10, dtype=np.float64)
    parameter_values = np.asarray(parameters, dtype=np.float64)
    if state.shape != (GREY_STATE_SIZE,):
        raise ValueError(f"state10 must have shape ({GREY_STATE_SIZE},)")
    if parameter_values.shape != (len(GREY_PARAMETER_NAMES),):
        raise ValueError(
            f"parameters must have shape ({len(GREY_PARAMETER_NAMES)},)"
        )
    return state, float(residual), parameter_values


def grey_box_rhs(
    state10: npt.ArrayLike, residual: float, parameters: npt.ArrayLike
) -> FloatArray:
    """Evaluate the ten-state continuous physical RHS numerically."""
    state, residual_value, parameter_values = _numeric_inputs(
        state10, residual, parameters
    )
    return np.asarray(
        _rhs_terms(state, residual_value, parameter_values), dtype=np.float64
    )


def grey_box_discrete_map(
    state10: npt.ArrayLike,
    previous_residual: float,
    residual: float,
    parameters: npt.ArrayLike,
) -> FloatArray:
    """Advance eight fixed RK4 substeps and store the applied residual."""
    state, residual_value, parameter_values = _numeric_inputs(
        state10, residual, parameters
    )
    float(previous_residual)
    physical_next = _rk4_physical_map(
        state,
        residual_value,
        parameter_values,
        lambda terms: np.asarray(terms, dtype=np.float64),
    )
    return np.concatenate((physical_next, np.array([residual_value])))


def _load_codegen_api():
    acados_source = os.environ.get("ACADOS_SOURCE_DIR")
    if acados_source:
        template_root = Path(acados_source) / "interfaces" / "acados_template"
        template_root_string = str(template_root)
        if template_root_string not in sys.path:
            sys.path.insert(0, template_root_string)

    ca = import_module("casadi")
    acados_template = import_module("acados_template")
    return ca, acados_template.AcadosOcp


def _default_parameter_values(config: GreyBoxMPCConfig) -> FloatArray:
    return np.array(
        [
            config.C_c,
            config.h_amb,
            config.T_amb,
            config.theta,
            config.K_Q,
            config.sigma,
            config.T_amb,
            0.5,
            config.temperature_weight,
            config.terminal_weight,
            config.move_weight,
            config.residual_weight,
        ],
        dtype=np.float64,
    )
def normalize_generated_tree(
    directory: str | Path,
    *,
    solver_name: str = "pifire_grey",
) -> None:
    """Replace known generator-host paths and reject every unknown absolute path."""
    generated_directory = Path(directory)
    metadata_path = generated_directory / f"{solver_name}.json"
    metadata = json.loads(metadata_path.read_text())
    options = metadata["code_gen_options"]
    export_path = options["code_export_directory"]
    numpy_include, python_include = options["cython_include_dirs"]

    cmake_path = generated_directory / "CMakeLists.txt"
    cmake_lines = []
    cmake_replacements = {
        "CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELEASE": '"${CMAKE_CURRENT_LIST_DIR}"',
        "CMAKE_ARCHIVE_OUTPUT_DIRECTORY_RELEASE": '"${CMAKE_CURRENT_LIST_DIR}"',
        "CMAKE_LIBRARY_OUTPUT_DIRECTORY_RELEASE": '"${CMAKE_CURRENT_LIST_DIR}"',
        "ACADOS_INCLUDE_PATH": '"<ACADOS_SOURCE_DIRECTORY>/include"',
        "ACADOS_LIB_PATH": '"<ACADOS_BUILD_DIRECTORY>/lib"',
    }
    for line in cmake_path.read_text().splitlines():
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        for variable, replacement in cmake_replacements.items():
            marker = f"set({variable} "
            if stripped.startswith(marker):
                suffix = (
                    stripped[len(marker) :].split(" CACHE ", 1)[1]
                    if " CACHE " in stripped
                    else None
                )
                line = f"{indent}{marker}{replacement}"
                if suffix is not None:
                    line += f" CACHE {suffix}"
                else:
                    line += ")"
                break
        cmake_lines.append(line)
    cmake_path.write_text("\n".join(cmake_lines) + "\n")

    makefile_path = generated_directory / "Makefile"
    if makefile_path.exists():
        makefile_lines = []
        source_makefile_lines = makefile_path.read_text().splitlines()
        inserted_portable_paths = any(
            line.startswith("GENERATED_DIR :=") for line in source_makefile_lines
        )
        for line in source_makefile_lines:
            if line.startswith("INCLUDE_PATH = "):
                if not inserted_portable_paths:
                    makefile_lines.extend(
                        (
                            "GENERATED_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))",
                            "ACADOS_SOURCE_DIRECTORY ?= <ACADOS_SOURCE_DIRECTORY>",
                            "ACADOS_BUILD_DIRECTORY ?= <ACADOS_BUILD_DIRECTORY>",
                            "PYTHON ?= python3",
                            (
                                "NUMPY_INCLUDE ?= $(shell $(PYTHON) -c "
                                "'import numpy; print(numpy.get_include())')"
                            ),
                            (
                                "PYTHON_INCLUDE ?= $(shell $(PYTHON) -c "
                                "'import sysconfig; print(sysconfig.get_paths()[\"include\"])')"
                            ),
                            "",
                        )
                    )
                    inserted_portable_paths = True
                line = "INCLUDE_PATH = $(ACADOS_SOURCE_DIRECTORY)/include"
            elif line.startswith("LIB_PATH = "):
                line = "LIB_PATH = $(ACADOS_BUILD_DIRECTORY)/lib"
            else:
                line = line.replace(export_path, "$(GENERATED_DIR)")
                line = line.replace(numpy_include, "$(NUMPY_INCLUDE)")
                line = line.replace(python_include, "$(PYTHON_INCLUDE)")
            makefile_lines.append(line)
        makefile_path.write_text("\n".join(makefile_lines) + "\n")

    options["acados_include_path"] = "<ACADOS_SOURCE_DIRECTORY>/include"
    options["acados_lib_path"] = "<ACADOS_BUILD_DIRECTORY>/lib"
    options["code_export_directory"] = "<GENERATED_DIRECTORY>"
    options["cython_include_dirs"] = ["<NUMPY_INCLUDE>", "<PYTHON_INCLUDE>"]
    options["json_file"] = f"<GENERATED_DIRECTORY>/{solver_name}.json"
    link_libraries = options.get("acados_link_libs")
    if isinstance(link_libraries, dict) and "openmp" in link_libraries:
        link_libraries["openmp"] = ""
    if "hash" in metadata:
        stable_metadata = {key: value for key, value in metadata.items() if key != "hash"}
        metadata["hash"] = hashlib.sha256(
            json.dumps(
                stable_metadata,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    metadata_path.write_text(json.dumps(metadata, indent=4) + "\n")

    absolute_paths = (
        re.compile(r'(?<![A-Za-z0-9_.$<})>/])/[A-Za-z0-9_][^\s"\'\\)]*'),
        re.compile(r'(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s"\')]*'),
        re.compile(r'(?<![\\])\\\\[^\\/\s"\']+[\\/][^\s"\')]*'),
        re.compile(r'(?<![/])//[^/\s"\']+/[^\s"\')]*'),
    )
    for path in sorted(generated_directory.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        match = None
        for pattern in absolute_paths:
            match = pattern.search(text)
            if match is not None:
                break
        if match is not None:
            relative_path = path.relative_to(generated_directory).as_posix()
            raise ValueError(
                f"unrecognized absolute path in {relative_path}: {match.group(0)}"
            )


def generate_grey_box_solver(
    config: GreyBoxMPCConfig | None = None,
    *,
    export_directory: str | Path | None = None,
) -> Path:
    """Generate a portable, checkout-independent CMake solver tree."""
    ocp = build_grey_box_ocp(config, export_directory=export_directory)
    _load_codegen_api()
    acados_template = import_module("acados_template")
    acados_template.AcadosOcpSolver.generate(
        ocp,
        cmake_builder=acados_template.ocp_get_default_cmake_builder(),
    )
    destination = Path(ocp.code_gen_options.code_export_directory)
    normalize_generated_tree(destination)
    return destination




def build_grey_box_ocp(
    config: GreyBoxMPCConfig | None = None,
    *,
    export_directory: str | Path | None = None,
) -> Any:
    """Build the 24-interval runtime-parameterized discrete grey-box OCP."""
    ca, AcadosOcp = _load_codegen_api()
    resolved_config = config if config is not None else GreyBoxMPCConfig()

    ocp = AcadosOcp()
    ocp.name = "pifire_grey"
    ocp.model.name = "pifire_grey"

    x = ca.SX.sym("x", GREY_STATE_SIZE + 1)
    residual = ca.SX.sym("residual", 1)
    parameters = ca.SX.sym("p", len(GREY_PARAMETER_NAMES))
    physical_next = _rk4_physical_map(
        x[:GREY_STATE_SIZE],
        residual[0],
        parameters,
        lambda terms: ca.vertcat(*terms),
    )

    ocp.model.x = x
    ocp.model.u = residual
    ocp.model.p = parameters
    ocp.model.disc_dyn_expr = ca.vertcat(physical_next, residual[0])

    stage_residuals = ca.vertcat(
        ca.sqrt(parameters[8]) * (x[8] - parameters[6]),
        ca.sqrt(parameters[10]) * (residual[0] - x[10]),
        ca.sqrt(parameters[11]) * residual[0],
    )
    terminal_residual = ca.sqrt(parameters[9]) * (x[8] - parameters[6])
    ocp.cost.cost_type = "NONLINEAR_LS"
    ocp.cost.cost_type_e = "NONLINEAR_LS"
    ocp.model.cost_y_expr = stage_residuals
    ocp.model.cost_y_expr_e = terminal_residual
    ocp.cost.W = np.eye(3)
    ocp.cost.W_e = np.eye(1)
    ocp.cost.yref = np.zeros(3)
    ocp.cost.yref_e = np.zeros(1)

    ocp.model.con_h_expr = parameters[7] + residual[0]
    ocp.model.con_h_expr_0 = parameters[7] + residual[0]
    ocp.constraints.lh = np.array([0.0])
    ocp.constraints.uh = np.array([1.0])
    ocp.constraints.lh_0 = np.array([0.0])
    ocp.constraints.uh_0 = np.array([1.0])
    ocp.constraints.x0 = np.zeros(GREY_STATE_SIZE + 1)

    ocp.parameter_values = _default_parameter_values(resolved_config)
    ocp.solver_options.N_horizon = GREY_HORIZON_CAPACITY
    ocp.solver_options.tf = GREY_HORIZON_CAPACITY * GREY_TIMESTEP_S
    ocp.solver_options.cost_scaling = np.ones(GREY_HORIZON_CAPACITY + 1)
    ocp.solver_options.integrator_type = "DISCRETE"
    ocp.solver_options.nlp_solver_type = "SQP"
    ocp.solver_options.hessian_approx = "GAUSS_NEWTON"
    ocp.solver_options.qp_solver = "PARTIAL_CONDENSING_HPIPM"
    ocp.solver_options.hpipm_mode = "ROBUST"
    ocp.solver_options.nlp_solver_max_iter = resolved_config.max_iterations
    ocp.solver_options.qp_solver_warm_start = 2
    ocp.solver_options.nlp_solver_warm_start_first_qp = True
    ocp.solver_options.print_level = 0

    destination = (
        Path(export_directory).resolve()
        if export_directory is not None
        else _DEFAULT_EXPORT_DIRECTORY
    )
    ocp.code_gen_options.code_export_directory = str(destination)
    ocp.code_gen_options.json_file = "pifire_grey.json"
    return ocp
