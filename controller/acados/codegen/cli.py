"""Supported deterministic regeneration command for the checked-in grey solver."""

from __future__ import annotations

import argparse
import ctypes
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
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
        _platform_directory_exchange(transaction, target)
    finally:
        if transaction.exists():
            shutil.rmtree(transaction)


def regenerate(
    mode: str,
    *,
    repository_root: str | Path = _REPOSITORY_ROOT,
    generators: Mapping[str, Generator] | None = None,
    gate: Gate | None = None,
    environment: Mapping[str, Any] | None = None,
    output: Callable[[str], None] = print,
) -> int:
    """Generate the grey solver in temporary storage, then check or commit it."""
    if mode not in {"check", "write"}:
        raise ValueError(f"unsupported regeneration mode: {mode}")
    if mode == "write" and gate is None:
        raise RegenerationError(_DIRECT_WRITE_ERROR)
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

    with tempfile.TemporaryDirectory(prefix="acados-regenerate-") as temporary:
        temporary_root = Path(temporary).resolve()
        staged = temporary_root / "generated"
        staged.mkdir()
        if staged == target or target in staged.parents:
            raise RegenerationError("temporary generation must be outside native/generated")

        for solver in _SOLVER_NAMES:
            destination = staged / solver
            result = Path(resolved_generators[solver](destination)).resolve()
            if result != destination.resolve():
                raise RegenerationError(
                    f"{solver} generator returned {result}, expected {destination.resolve()}"
                )
        manifest = create_manifest(
            repository,
            staged,
            environment=resolved_environment,
        )
        write_manifest(staged / "manifest.json", manifest)

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return regenerate("write" if arguments.write else "check")
    except (EnvironmentMismatch, RegenerationError, OSError, ValueError) as error:
        print(f"acados-regenerate: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
