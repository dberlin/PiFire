"""Supported deterministic regeneration command for the checked-in grey solver."""

from __future__ import annotations

import argparse
import ctypes
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from uuid import uuid4

from .manifest import (
    EnvironmentMismatch,
    collect_environment,
    create_manifest,
    validate_environment,
    write_manifest,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SOLVER_NAMES = ("grey_box",)
_DIRECT_WRITE_ERROR = (
    "direct --write is unsupported because generated-source publication requires "
    "the complete equation, parity, compile, ABI, and smoke gate; use --check "
    "until Task 6 provides the sole public rebuild entry point, ./rebuild-acados.sh"
)
Generator = Callable[[Path], Path]
Gate = Callable[[Path, Path, Path], None]


class RegenerationError(RuntimeError):
    """A generation, compilation, parity, or replacement gate failed."""


@dataclass(frozen=True)
class FileDifference:
    kind: str
    path: str


def _default_generators() -> dict[str, Generator]:
    from .grey_box_ocp import generate_grey_box_solver

    return {
        "grey_box": lambda directory: generate_grey_box_solver(
            export_directory=directory
        ),
    }


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def compare_trees(expected: Path, actual: Path) -> tuple[FileDifference, ...]:
    """Return deterministic relative byte differences from expected to actual."""
    expected_files = _tree_bytes(expected)
    actual_files = _tree_bytes(actual)
    differences = [
        FileDifference("added", path)
        for path in sorted(actual_files.keys() - expected_files.keys())
    ]
    differences.extend(
        FileDifference("removed", path)
        for path in sorted(expected_files.keys() - actual_files.keys())
    )
    differences.extend(
        FileDifference("changed", path)
        for path in sorted(expected_files.keys() & actual_files.keys())
        if expected_files[path] != actual_files[path]
    )
    return tuple(sorted(differences, key=lambda item: (item.path, item.kind)))


def _platform_directory_exchange(left: Path, right: Path) -> None:
    """Exchange two directory names with one platform atomic-swap syscall."""
    library = ctypes.CDLL(None, use_errno=True)
    left_bytes = os.fsencode(left)
    right_bytes = os.fsencode(right)
    if sys.platform.startswith("linux"):
        renameat2 = getattr(library, "renameat2", None)
        if renameat2 is None:
            raise RegenerationError(
                "atomic directory exchange is unsupported: renameat2 unavailable"
            )
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, left_bytes, -100, right_bytes, 2)
    elif sys.platform == "darwin":
        renamex_np = getattr(library, "renamex_np", None)
        if renamex_np is None:
            raise RegenerationError(
                "atomic directory exchange is unsupported: renamex_np unavailable"
            )
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(left_bytes, right_bytes, 2)
    else:
        raise RegenerationError(
            f"atomic directory exchange is unsupported on {sys.platform}"
        )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            f"{left} <-> {right}",
        )


def _probe_directory_exchange(parent: Path) -> None:
    """Fail before target mutation if this filesystem cannot exchange directories."""
    probe_root = Path(
        tempfile.mkdtemp(prefix=".acados-exchange-probe-", dir=parent)
    )
    left = probe_root / "left"
    right = probe_root / "right"
    left.mkdir()
    right.mkdir()
    try:
        _platform_directory_exchange(left, right)
    except (OSError, RegenerationError) as error:
        raise RegenerationError(
            f"atomic directory exchange is unsupported for {parent}: {error}"
        ) from error
    finally:
        shutil.rmtree(probe_root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        _fsync_directory(directory)
    _fsync_directory(root)


def _atomic_replace_tree(staged: Path, target: Path) -> None:
    """Commit the complete generated tree with one atomic directory exchange."""
    target_parent = target.parent
    if not target.is_dir():
        raise RegenerationError(
            f"atomic directory exchange requires existing target tree: {target}"
        )
    _probe_directory_exchange(target_parent)
    transaction = target_parent / f".{target.name}-stage-{uuid4().hex}"
    shutil.copytree(staged, transaction)
    try:
        _fsync_tree(transaction)
        _platform_directory_exchange(transaction, target)
        _fsync_directory(target_parent)
    finally:
        if transaction.exists():
            shutil.rmtree(transaction)


def _populate_staged_tree(
    repository: Path,
    staged: Path,
    *,
    generators: Mapping[str, Generator],
    environment: Mapping[str, Any],
) -> None:
    """Generate the complete normalized tree and manifest into empty staging."""
    if staged.is_symlink():
        raise RegenerationError(f"staging path must not be a symlink: {staged}")
    if staged.exists():
        if not staged.is_dir():
            raise RegenerationError(f"staging path must be a directory: {staged}")
        if any(staged.iterdir()):
            raise RegenerationError(f"staging directory must be empty: {staged}")
    else:
        staged.mkdir(parents=True)

    for solver in _SOLVER_NAMES:
        destination = staged / solver
        result = Path(generators[solver](destination)).resolve()
        if result != destination.resolve():
            raise RegenerationError(
                f"{solver} generator returned {result}, expected {destination.resolve()}"
            )
    manifest = create_manifest(
        repository,
        staged,
        environment=environment,
    )
    write_manifest(staged / "manifest.json", manifest)


def regenerate(
    mode: str,
    *,
    repository_root: str | Path = _REPOSITORY_ROOT,
    generators: Mapping[str, Generator] | None = None,
    gate: Gate | None = None,
    environment: Mapping[str, Any] | None = None,
    staging: str | Path | None = None,
    output: Callable[[str], None] = print,
) -> int:
    """Generate the grey solver for comparison, gated publication, or staging."""
    if mode not in {"check", "write", "stage"}:
        raise ValueError(f"unsupported regeneration mode: {mode}")
    if mode == "write" and gate is None:
        raise RegenerationError(_DIRECT_WRITE_ERROR)
    if mode == "stage" and staging is None:
        raise RegenerationError("internal stage mode requires a destination")
    if mode != "stage" and staging is not None:
        raise RegenerationError("a staging destination is valid only in stage mode")
    repository = Path(repository_root).resolve()
    target = repository / "native/generated"
    resolved_environment = (
        collect_environment(repository) if environment is None else dict(environment)
    )
    validate_environment(resolved_environment)
    resolved_generators = dict(generators or _default_generators())
    if set(resolved_generators) != set(_SOLVER_NAMES):
        raise RegenerationError(
            f"generators must define exactly {', '.join(_SOLVER_NAMES)}"
        )

    if mode == "stage":
        assert staging is not None
        staged = Path(staging).resolve()
        if staged == target or target in staged.parents:
            raise RegenerationError("staging must be outside native/generated")
        _populate_staged_tree(
            repository,
            staged,
            generators=resolved_generators,
            environment=resolved_environment,
        )
        output(f"generated solver tree staged at {staged}")
        return 0

    with tempfile.TemporaryDirectory(prefix="acados-regenerate-") as temporary:
        temporary_root = Path(temporary).resolve()
        staged = temporary_root / "generated"
        staged.mkdir()
        _populate_staged_tree(
            repository,
            staged,
            generators=resolved_generators,
            environment=resolved_environment,
        )

        if mode == "check":
            differences = compare_trees(staged, target)
            if differences:
                output("generated solver tree differs:")
                for difference in differences:
                    output(f"{difference.kind}: {difference.path}")
                return 1
            output("generated solver tree is current")
            return 0

        if gate is None:
            raise RegenerationError(_DIRECT_WRITE_ERROR)
        gate(repository, staged, temporary_root)
        _atomic_replace_tree(staged, target)
        output("generated solver tree updated")
        return 0


EquationEvaluator = Callable[
    [tuple[float, ...], float, tuple[float, ...]], tuple[float, ...]
]


def _reference_discrete_map(
    state11: tuple[float, ...],
    residual: float,
    parameters: tuple[float, ...],
) -> tuple[float, ...]:
    def rhs(current: tuple[float, ...]) -> tuple[float, ...]:
        C_c, h_amb, T_amb, theta, K_Q, sigma = parameters[:6]
        equilibrium_q = parameters[7]
        derivatives = [
            (equilibrium_q + residual - current[0]) / (theta / 8.0)
        ]
        derivatives.extend(
            (current[index - 1] - current[index]) / (theta / 8.0)
            for index in range(1, 8)
        )
        derivatives.append(
            (
                K_Q * current[7]
                - h_amb * (current[8] - T_amb)
                - sigma
                * (
                    (current[8] + 273.15) ** 4
                    - (T_amb + 273.15) ** 4
                )
                + current[9]
            )
            / C_c
        )
        derivatives.append(0.0)
        return tuple(derivatives)

    current = state11[:10]
    step = 25.0 / 8.0
    for _ in range(8):
        k1 = rhs(current)
        k2 = rhs(tuple(x + 0.5 * step * k for x, k in zip(current, k1)))
        k3 = rhs(tuple(x + 0.5 * step * k for x, k in zip(current, k2)))
        k4 = rhs(tuple(x + step * k for x, k in zip(current, k3)))
        current = tuple(
            x + step * (a + 2.0 * b + 2.0 * c + d) / 6.0
            for x, a, b, c, d in zip(current, k1, k2, k3, k4)
        )
    return (*current, residual)


def _compiled_equation_evaluator(
    staged: Path, temporary: Path
) -> EquationEvaluator:
    source = (
        staged
        / "grey_box/pifire_grey_model/pifire_grey_dyn_disc_phi_fun.c"
    )
    if not source.is_file():
        raise RegenerationError(f"staged dynamics source is missing: {source}")
    library_path = temporary / (
        "equation.dylib" if sys.platform == "darwin" else "equation.so"
    )
    subprocess.run(
        (
            os.environ.get("CC", "cc"),
            "-shared",
            "-fPIC",
            "-O2",
            str(source),
            "-lm",
            "-o",
            str(library_path),
        ),
        check=True,
    )
    library = ctypes.CDLL(str(library_path))
    function = library.pifire_grey_dyn_disc_phi_fun
    double_pointer = ctypes.POINTER(ctypes.c_double)
    function.argtypes = [
        ctypes.POINTER(double_pointer),
        ctypes.POINTER(double_pointer),
        ctypes.POINTER(ctypes.c_int),
        double_pointer,
        ctypes.c_int,
    ]
    function.restype = ctypes.c_int

    def evaluate(
        state: tuple[float, ...],
        residual: float,
        parameters: tuple[float, ...],
    ) -> tuple[float, ...]:
        state_array = (ctypes.c_double * 11)(*state)
        residual_array = (ctypes.c_double * 1)(residual)
        parameter_array = (ctypes.c_double * 12)(*parameters)
        output_array = (ctypes.c_double * 11)()
        arguments = (double_pointer * 3)(
            ctypes.cast(state_array, double_pointer),
            ctypes.cast(residual_array, double_pointer),
            ctypes.cast(parameter_array, double_pointer),
        )
        results = (double_pointer * 1)(
            ctypes.cast(output_array, double_pointer)
        )
        status = function(arguments, results, None, None, 0)
        if status != 0:
            raise RegenerationError(
                f"staged dynamics evaluation failed with status {status}"
            )
        return tuple(float(value) for value in output_array)

    return evaluate


def _run_equation_parity(evaluator: EquationEvaluator) -> None:
    parameters = (
        320.0,
        0.5,
        20.0,
        50.0,
        350.0,
        1.4e-9,
        120.0,
        0.25,
        1.0,
        1.0,
        0.1,
        0.0,
    )
    cases = (
        ((0.2,) * 8 + (100.0, 0.0, -0.05), 0.1),
        ((0.4,) * 8 + (145.0, -2.0, 0.15), -0.1),
        ((0.05,) * 8 + (65.0, 3.0, 0.0), 0.3),
    )
    for state, residual in cases:
        expected = _reference_discrete_map(state, residual, parameters)
        actual = evaluator(state, residual, parameters)
        if len(actual) != len(expected) or any(
            not math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-10)
            for left, right in zip(actual, expected)
        ):
            raise RegenerationError("staged generated equation parity mismatch")


def validate_staged_equation_parity(
    staged: Path,
    *,
    evaluator: EquationEvaluator | None = None,
) -> None:
    if evaluator is not None:
        _run_equation_parity(evaluator)
        return
    with tempfile.TemporaryDirectory(
        prefix="acados-equation-parity-"
    ) as temporary:
        resolved = _compiled_equation_evaluator(
            Path(staged).resolve(), Path(temporary)
        )
        _run_equation_parity(resolved)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acados-regenerate",
        description="Deterministically regenerate the checked-in grey solver.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--write",
        action="store_true",
        help="unsupported directly; use ./rebuild-acados.sh once Task 6 lands",
    )
    mode.add_argument("--check", action="store_true", help="compare without modifying files")
    mode.add_argument(
        "--stage",
        type=Path,
        help=argparse.SUPPRESS,
    )
    mode.add_argument(
        "--equation-parity",
        type=Path,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.equation_parity is not None:
            validate_staged_equation_parity(arguments.equation_parity)
            return 0
        if arguments.stage is not None:
            return regenerate("stage", staging=arguments.stage)
        return regenerate("write" if arguments.write else "check")
    except (
        EnvironmentMismatch,
        RegenerationError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        print(f"acados-regenerate: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
