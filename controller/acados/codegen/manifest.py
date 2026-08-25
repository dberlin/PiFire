"""Pinned, checkout-independent manifest for generated acados solvers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from copy import deepcopy
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PINNED_ENVIRONMENT: dict[str, Any] = {
    "acados": {
        "url": "https://github.com/acados/acados.git",
        "revision": "503364817c872d474ab5bed219c26760ac267769",
        "tag": "v0.6.0",
        "recursive_dependencies": {
            "examples/acados_python/tests/test_data": "c95aa6fd2d1ae4ee202dbafe935d8987aa62b73c",
            "external/Clarabel.cpp": "c73eb64352f17fc18bc10fe3c082fcbf9a3c0487",
            "external/Clarabel.cpp/Clarabel.rs": "25540f559592068d0c8a80e46ded1b21760212a1",
            "external/blasfeo": "d6251233923c9b475fe894fb729fb63ab693e301",
            "external/catch": "f3da715b4a4d075ea2f03b5908b360c64bff8cb0",
            "external/daqp": "dc13508052b658b97c672a8c223029fd9c0d42b5",
            "external/hpipm": "e3a56c1caddd7f12d125d84f337b9a9e5c186271",
            "external/hpmpc": "da0f9791034cb09bb0904d73504e948d8ea4d0a5",
            "external/jsonlab": "288dc922ab94bec42405541e7f50036d9716c5d7",
            "external/osqp": "0dd00a578cf1c2691c5c379965d504c75bf6cfad",
            "external/osqp/lin_sys/direct/qdldl/qdldl_sources": "7d16b70a10a152682204d745d814b6eb63dc5cd2",
            "external/qpdunes": "4f5bdb4ff19a4a2896abe5bfd8fbde5710f34950",
            "external/qpoases": "125e94fa638f00350608871fc165789b1d1762f1",
            "interfaces/acados_template/tera_renderer": "a480a64b0a2cc15d4b1e6146e986388709ac0716",
        },
    },
    "python_generator_dependencies": {
        "casadi": "3.7.2",
        "Cython": "3.2.9",
        "Deprecated": "1.3.1",
        "matplotlib": "3.11.1",
        "numpy": "2.5.1",
        "scipy": "1.18.0",
        "setuptools-scm": "8.3.1",
    },
}

_SOLVERS = {
    "grey_box": {
        "metadata": "grey_box/pifire_grey.json",
        "model_definition": "controller/acados/codegen/grey_box_ocp.py",
    },
}


class EnvironmentMismatch(RuntimeError):
    """The active generator environment does not match the supported pins."""


def _run_git(arguments: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise EnvironmentMismatch(f"unable to inspect acados revision: {detail}")
    return completed.stdout.rstrip("\r\n")


def _require_clean_worktree(repository: Path, label: str) -> None:
    status = _run_git(
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=all",
        ],
        cwd=repository,
    )
    if status:
        raise EnvironmentMismatch(f"{label} worktree is dirty; regeneration attribution is invalid:\n{status}")


def _require_clean_acados_worktrees(
    acados: Path,
    dependency_paths: tuple[str, ...],
) -> None:
    _require_clean_worktree(acados, "fetched acados")
    for relative_path in dependency_paths:
        _require_clean_worktree(
            acados / relative_path,
            f"fetched acados/{relative_path}",
        )


def collect_environment(repository_root: str | Path) -> dict[str, Any]:
    """Read exact fetched acados, recursive dependency, and Python versions."""
    Path(repository_root).resolve()
    configured_source = os.environ.get("ACADOS_SOURCE_DIR")
    if not configured_source:
        raise EnvironmentMismatch("ACADOS_SOURCE_DIR is unset; configure CMake and export its fetched source")
    acados = Path(configured_source).resolve()
    dependencies: dict[str, str] = {}
    output = _run_git(["submodule", "status", "--recursive"], cwd=acados)
    for line in output.splitlines():
        status = line[:1]
        fields = line[1:].split()
        if status != " " or len(fields) < 2:
            path = fields[1] if len(fields) >= 2 else line
            raise EnvironmentMismatch(f"acados recursive dependency is not at its initialized pin: {path}")
        dependencies[Path(fields[1]).as_posix()] = fields[0]
    _require_clean_acados_worktrees(acados, tuple(dependencies))
    source_url = _run_git(["remote", "get-url", "origin"], cwd=acados).strip()
    revision = _run_git(["rev-parse", "HEAD"], cwd=acados).strip()
    tag = _run_git(["describe", "--tags", "--exact-match", "HEAD"], cwd=acados).strip()
    versions = {
        distribution: importlib_metadata.version(distribution)
        for distribution in PINNED_ENVIRONMENT["python_generator_dependencies"]
    }
    return {
        "acados": {
            "url": source_url,
            "revision": revision,
            "tag": tag,
            "recursive_dependencies": dependencies,
        },
        "python_generator_dependencies": versions,
    }


def validate_environment(environment: Mapping[str, Any]) -> None:
    """Reject any revision or version that differs from the supported pins."""
    actual_acados = environment.get("acados", {})
    expected_acados = PINNED_ENVIRONMENT["acados"]
    for field, label in (
        ("url", "acados URL"),
        ("revision", "acados revision"),
        ("tag", "acados tag"),
    ):
        actual = actual_acados.get(field)
        expected = expected_acados[field]
        if actual != expected:
            raise EnvironmentMismatch(f"{label}: expected {expected}, got {actual}")

    actual_dependencies = actual_acados.get("recursive_dependencies")
    expected_dependencies = expected_acados["recursive_dependencies"]
    if actual_dependencies != expected_dependencies:
        raise EnvironmentMismatch("acados recursive dependency commits differ from the supported pins")

    actual_versions = environment.get("python_generator_dependencies")
    expected_versions = PINNED_ENVIRONMENT["python_generator_dependencies"]
    if actual_versions != expected_versions:
        mismatches = []
        actual_mapping = actual_versions if isinstance(actual_versions, Mapping) else {}
        for name in sorted(set(expected_versions) | set(actual_mapping)):
            if actual_mapping.get(name) != expected_versions.get(name):
                mismatches.append(f"{name}: expected {expected_versions.get(name)}, got {actual_mapping.get(name)}")
        raise EnvironmentMismatch("Python generator dependency versions differ: " + "; ".join(mismatches))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_manifest(
    repository_root: str | Path,
    generated_root: str | Path,
    *,
    environment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create schema 1 data from normalized solver trees."""
    repository = Path(repository_root).resolve()
    generated = Path(generated_root).resolve()
    resolved_environment = collect_environment(repository) if environment is None else deepcopy(environment)
    validate_environment(resolved_environment)

    solvers: dict[str, Any] = {}
    model_definitions: dict[str, str] = {}
    for solver, specification in _SOLVERS.items():
        metadata_relative = specification["metadata"]
        metadata_path = generated / metadata_relative
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        model_relative = specification["model_definition"]
        model_path = repository / model_relative
        solvers[solver] = {
            "metadata": metadata_relative,
            "dimensions": metadata["dims"],
            "options": metadata["solver_options"],
        }
        model_definitions[model_relative] = _sha256(model_path)

    files = {
        path.relative_to(generated).as_posix(): _sha256(path)
        for path in sorted(generated.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    return {
        "schema": SCHEMA_VERSION,
        "acados": resolved_environment["acados"],
        "python_generator_dependencies": resolved_environment["python_generator_dependencies"],
        "model_definitions": model_definitions,
        "solvers": solvers,
        "files": files,
    }


def write_manifest(path: str | Path, manifest: Mapping[str, Any]) -> None:
    """Write canonical UTF-8 JSON with sorted keys and a final newline."""
    Path(path).write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
