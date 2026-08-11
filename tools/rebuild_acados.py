"""Canonical acados rebuild orchestration and crash-safe publication."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
import ctypes
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import Any, Protocol
from uuid import uuid4

HORIZONS = tuple(range(5, 25))
SOLVES_PER_HORIZON = 1_000
GENERATED_GATES = ("provenance", "equation", "parity")
RUNTIME_GATES = (
    "abi",
    "loader",
    "horizons-5-through-24",
    "invalid-horizons",
    "cost-scaling",
    "cold-solves",
    "warm-solves",
)


class BuildMode(Enum):
    FULL = "full"
    IF_NEEDED = "if-needed"


class StalenessClass(Enum):
    ACADOS_SOURCE = "acados-source"
    ACADOS_DEPENDENCIES = "acados-dependencies"
    GENERATOR_DEPENDENCIES = "generator-dependencies"
    GENERATED_PROVENANCE = "generated-provenance"
    GENERATED_TREE = "generated-tree"
    NATIVE_WRAPPER = "native-wrapper"
    PUBLIC_HEADER = "public-header"
    EXPORT_CONTROLS = "export-controls"
    CMAKE_SOURCES = "cmake-sources"
    PLATFORM_MAPPING = "platform-mapping"
    ABI = "abi"
    HOST_PLATFORM = "host-platform"
    COMPILER = "compiler"
    CMAKE_CONFIGURATION = "cmake-configuration"
    LOADER = "loader"
    LIBRARY = "library"
    MISSING_RELEASE = "missing-release"
    INVALID_MANIFEST = "invalid-manifest"


class RebuildError(RuntimeError):
    pass


class TimingGateError(RebuildError):
    pass


class PublicationError(RebuildError):
    pass


class PublicationCollisionError(PublicationError):
    pass


@dataclass(frozen=True)
class BuildInputIdentity:
    generated_manifest: Mapping[str, Any]
    native_source_sha256: Mapping[str, str]
    abi_version: int
    host: Mapping[str, Any]
    compiler: Mapping[str, Any]
    cmake: Mapping[str, Any]
    loader: Mapping[str, Any]


@dataclass(frozen=True)
class BuildPaths:
    repository: Path
    generated_target: Path
    generated_staging_root: Path
    runtime_root: Path
    runtime_releases: Path
    runtime_staging_root: Path
    selector: Path
    lock_file: Path
    build_root: Path

    @classmethod
    def for_repository(cls, value: str | Path) -> "BuildPaths":
        root = Path(value).resolve()
        generated = root / "native/generated"
        runtime = root / "controller/_native"
        return cls(
            root,
            generated,
            generated.parent / ".generated-staging",
            runtime,
            runtime / "releases",
            runtime / ".staging",
            runtime / "current",
            runtime / "rebuild.lock",
            root / "build/acados-rebuild",
        )


@dataclass(frozen=True)
class SolveSample:
    status: int
    elapsed_seconds: float
    result_is_finite: bool


@dataclass(frozen=True)
class NativeEvidence:
    status: int
    finite: bool
    objective_matches: bool
    warm_started: bool


@dataclass(frozen=True)
class TimingEvidence:
    horizon: int
    solve_count: int
    failure_count: int
    consecutive_failure_count: int
    recovery_result: str
    p99_seconds: float
    maximum_seconds: float
    p99_threshold_seconds: float
    maximum_threshold_seconds: float


@dataclass(frozen=True)
class RebuildResult:
    changed: bool
    build_digest: str | None
    stale_reasons: tuple[StalenessClass, ...] = ()


def _identity(value: BuildInputIdentity) -> dict[str, Any]:
    raw = {
        "generated_manifest": value.generated_manifest,
        "native_source_sha256": value.native_source_sha256,
        "abi_version": value.abi_version,
        "host": value.host,
        "compiler": value.compiler,
        "cmake": value.cmake,
        "loader": value.loader,
    }
    return json.loads(json.dumps(raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def _build_digest(inputs: Mapping[str, Any], library: str) -> str:
    raw = json.dumps(
        {"build_inputs": inputs, "library_sha256": library}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def canonical_build_manifest(
    identity: BuildInputIdentity, *, library_sha256: str, built_at: str | None = None
) -> dict[str, Any]:
    inputs = _identity(identity)
    return {
        "schema": 1,
        "build_digest": _build_digest(inputs, library_sha256),
        "build_inputs": inputs,
        "library_sha256": library_sha256,
        "built_at": built_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def _valid_manifest(value: Mapping[str, Any]) -> bool:
    inputs, library = value.get("build_inputs"), value.get("library_sha256")
    return (
        isinstance(inputs, Mapping)
        and isinstance(library, str)
        and re.fullmatch(r"[0-9a-f]{64}", library) is not None
        and value.get("build_digest") == _build_digest(inputs, library)
    )


def _changed(old: Mapping[str, str], new: Mapping[str, str], predicate: Callable[[str], bool]) -> bool:
    keys = {key for key in set(old) | set(new) if predicate(key)}
    return any(old.get(key) != new.get(key) for key in keys)


def classify_staleness(
    installed: Mapping[str, Any] | None, expected_identity: BuildInputIdentity, *, actual_library_sha256: str | None
) -> tuple[StalenessClass, ...]:
    if installed is None:
        return (StalenessClass.MISSING_RELEASE,)
    if not isinstance(installed, Mapping) or not _valid_manifest(installed):
        return (StalenessClass.INVALID_MANIFEST,)
    old, new = installed["build_inputs"], _identity(expected_identity)
    og, ng = old.get("generated_manifest"), new["generated_manifest"]
    osources, nsources = old.get("native_source_sha256"), new["native_source_sha256"]
    if not isinstance(og, Mapping) or not isinstance(osources, Mapping):
        return (StalenessClass.INVALID_MANIFEST,)
    oa, na = og.get("acados", {}), ng.get("acados", {})
    result: list[StalenessClass] = []
    if not isinstance(oa, Mapping) or any(oa.get(k) != na.get(k) for k in ("url", "tag", "revision")):
        result.append(StalenessClass.ACADOS_SOURCE)
    if not isinstance(oa, Mapping) or oa.get("recursive_dependencies") != na.get("recursive_dependencies"):
        result.append(StalenessClass.ACADOS_DEPENDENCIES)
    if og.get("python_generator_dependencies") != ng.get("python_generator_dependencies"):
        result.append(StalenessClass.GENERATOR_DEPENDENCIES)
    if any(og.get(k) != ng.get(k) for k in ("schema", "model_definitions", "solvers")):
        result.append(StalenessClass.GENERATED_PROVENANCE)
    if og.get("files") != ng.get("files"):
        result.append(StalenessClass.GENERATED_TREE)
    for reason, predicate in (
        (StalenessClass.NATIVE_WRAPPER, lambda p: p.startswith("native/src/")),
        (StalenessClass.PUBLIC_HEADER, lambda p: p.startswith("native/include/")),
        (StalenessClass.EXPORT_CONTROLS, lambda p: p.endswith((".exports", ".version-script"))),
        (
            StalenessClass.CMAKE_SOURCES,
            lambda p: p in {"CMakeLists.txt", "native/CMakeLists.txt", "native/AcadosPifireExports.cmake"},
        ),
        (StalenessClass.PLATFORM_MAPPING, lambda p: p.startswith("cmake/")),
    ):
        if _changed(osources, nsources, predicate):
            result.append(reason)
    for reason, key in (
        (StalenessClass.ABI, "abi_version"),
        (StalenessClass.HOST_PLATFORM, "host"),
        (StalenessClass.COMPILER, "compiler"),
        (StalenessClass.CMAKE_CONFIGURATION, "cmake"),
        (StalenessClass.LOADER, "loader"),
    ):
        if old.get(key) != new[key]:
            result.append(reason)
    if actual_library_sha256 != installed.get("library_sha256"):
        result.append(StalenessClass.LIBRARY)
    return tuple(result)


def validate_timing_gate(
    matrix: Mapping[int, Sequence[SolveSample]],
    *,
    configured_control_period_seconds: float | None,
    catalog_minimum_control_period_seconds: float,
) -> tuple[TimingEvidence, ...]:
    if tuple(sorted(matrix)) != HORIZONS:
        raise TimingGateError("timing evidence must cover every integer horizon 5 through 24")
    period = (
        catalog_minimum_control_period_seconds
        if configured_control_period_seconds is None
        else configured_control_period_seconds
    )
    if not math.isfinite(period) or period <= 0:
        raise TimingGateError("control period must be finite and positive")
    result = []
    for horizon in HORIZONS:
        samples = tuple(matrix[horizon])
        if len(samples) != SOLVES_PER_HORIZON:
            raise TimingGateError(f"horizon {horizon} requires exactly 1,000 solves")
        if any(not math.isfinite(s.elapsed_seconds) or s.elapsed_seconds < 0 for s in samples):
            raise TimingGateError(f"horizon {horizon} timing must be finite")
        failures = [i for i, s in enumerate(samples) if s.status != 0]
        if len(failures) > 5:
            raise TimingGateError(f"horizon {horizon} permits at most five failures")
        consecutive = sum(b == a + 1 for a, b in zip(failures, failures[1:], strict=False))
        if consecutive:
            raise TimingGateError(f"horizon {horizon} has consecutive failures")
        for index in failures:
            if index + 1 == len(samples):
                raise TimingGateError(f"horizon {horizon} failure has no recovery")
            recovery = samples[index + 1]
            if recovery.status != 0 or not recovery.result_is_finite:
                raise TimingGateError(f"horizon {horizon} requires finite successful recovery")
        if any(s.status == 0 and not s.result_is_finite for s in samples):
            raise TimingGateError(f"horizon {horizon} successful result must be finite")
        elapsed = sorted(s.elapsed_seconds for s in samples)
        p99 = elapsed[math.ceil(0.99 * len(elapsed)) - 1]
        maximum = elapsed[-1]
        if maximum >= period:
            raise TimingGateError(f"horizon {horizon} maximum missed strict threshold")
        if p99 >= 0.2 * period:
            raise TimingGateError(f"horizon {horizon} p99 missed strict threshold")
        result.append(
            TimingEvidence(
                horizon, len(samples), len(failures), consecutive, "finite-success", p99, maximum, 0.2 * period, period
            )
        )
    return tuple(result)


def format_timing_evidence(row: TimingEvidence | Mapping[str, Any]) -> str:
    v = asdict(row) if isinstance(row, TimingEvidence) else row
    return f"horizon={v['horizon']} solves={v['solve_count']} failures={v['failure_count']} consecutive_failures={v['consecutive_failure_count']} recovery={v['recovery_result']} p99_seconds={v['p99_seconds']:.6f} maximum_seconds={v['maximum_seconds']:.6f} p99_threshold_seconds={v['p99_threshold_seconds']:.6f} maximum_threshold_seconds={v['maximum_threshold_seconds']:.6f}"


class PublicationFilesystem(Protocol):
    def device_id(self, path: Path) -> int: ...
    def verify_complete_contained(self, stage: Path) -> tuple[str, bytes]: ...
    def fsync_staged_files(self, stage: Path) -> None: ...
    def fsync_directory(self, path: Path) -> None: ...
    def rename_noreplace(self, stage: Path, release: Path) -> None: ...
    def existing_release_bytes(self, release: Path) -> bytes: ...
    def seal_and_verify_release(self, release: Path, expected: bytes) -> None: ...
    def discard_stage(self, stage: Path) -> None: ...
    def atomic_replace_selector(self, selector: Path, digest: str) -> None: ...


def publish_immutable_release(
    staging: Path, runtime_root: Path, *, filesystem: PublicationFilesystem, select: bool = True
) -> Path:
    releases = runtime_root / "releases"
    if filesystem.device_id(staging) != filesystem.device_id(releases):
        raise PublicationError("staging and releases must be on the same filesystem")
    try:
        digest, content = filesystem.verify_complete_contained(staging)
        release = releases / digest
        filesystem.fsync_staged_files(staging)
        filesystem.fsync_directory(staging)
        try:
            filesystem.rename_noreplace(staging, release)
        except FileExistsError as error:
            if filesystem.existing_release_bytes(release) != content:
                raise PublicationCollisionError(f"conflicting immutable release: {release}") from error
            filesystem.discard_stage(staging)
        filesystem.seal_and_verify_release(release, content)
        filesystem.fsync_directory(releases)
        if select:
            filesystem.atomic_replace_selector(runtime_root / "current", digest)
            filesystem.fsync_directory(runtime_root)
        return release
    except PublicationCollisionError:
        raise
    except Exception as error:
        raise PublicationError(f"immutable release publication failed: {error}") from error


class Operations(Protocol):
    paths: BuildPaths
    current_release: str | None

    def build_lock(self) -> Any: ...
    def inspect_staleness(self) -> tuple[StalenessClass, ...]: ...
    def configure_and_fetch(self) -> None: ...
    def create_generated_staging(self) -> Path: ...
    def generate(self, command: tuple[str, ...], destination: Path) -> None: ...
    def validate_generated(self, gate: str, destination: Path) -> None: ...
    def create_runtime_staging(self) -> Path: ...
    def compile_native(self, generated: Path, destination: Path) -> None: ...
    def validate_runtime(self, gate: str, destination: Path) -> None: ...
    def run_timing_gate(
        self, destination: Path, *, horizons: tuple[int, ...], solves_per_horizon: int
    ) -> tuple[Mapping[str, Any], ...]: ...
    def publish_generated(self, destination: Path) -> None: ...
    def publish_runtime(self, destination: Path) -> str: ...
    def replace_selector(self, digest: str) -> None: ...
    def emit(self, line: str) -> None: ...


def run_rebuild(mode: BuildMode, *, operations: Operations) -> RebuildResult:
    phase = "lock"
    stale: tuple[StalenessClass, ...] = ()
    try:
        with operations.build_lock():
            if mode is BuildMode.IF_NEEDED:
                phase = "inspect"
                stale = operations.inspect_staleness()
                if not stale:
                    operations.emit("acados build inputs exactly match; no rebuild needed")
                    return RebuildResult(False, operations.current_release, ())
            phase = "configure-fetch"
            operations.configure_and_fetch()
            generated = operations.paths.generated_target
            if mode is BuildMode.FULL:
                phase = "stage:generated"
                generated = operations.create_generated_staging()
                command = (
                    "uv",
                    "run",
                    "--no-default-groups",
                    "--group",
                    "codegen",
                    "python",
                    "-m",
                    "controller.acados.codegen.cli",
                    "--stage",
                    str(generated),
                )
                phase = "generate"
                operations.generate(command, generated)
                for gate in GENERATED_GATES:
                    phase = f"gate:{gate}"
                    operations.validate_generated(gate, generated)
            phase = "stage:runtime"
            runtime = operations.create_runtime_staging()
            phase = "compile"
            operations.compile_native(generated, runtime)
            for gate in RUNTIME_GATES:
                phase = f"gate:{gate}"
                operations.validate_runtime(gate, runtime)
            phase = "gate:timing"
            for row in operations.run_timing_gate(runtime, horizons=HORIZONS, solves_per_horizon=SOLVES_PER_HORIZON):
                operations.emit(format_timing_evidence(row))
            if mode is BuildMode.FULL:
                phase = "publish:generated"
                operations.publish_generated(generated)
            phase = "publish:runtime"
            digest = operations.publish_runtime(runtime)
            phase = "selector:replace"
            operations.replace_selector(digest)
            return RebuildResult(True, digest, stale)
    except RebuildError:
        raise
    except BaseException as error:
        raise RebuildError(f"{phase}: {error}") from error


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _library_filename() -> str:
    if sys.platform.startswith("linux"):
        return "libacados_pifire.so"
    if sys.platform == "darwin":
        return "libacados_pifire.dylib"
    raise RebuildError(f"unsupported native platform: {sys.platform}")


def _stream(
    command: Sequence[str],
    *,
    cwd: Path,
    emit: Callable[[str], None],
    environment: Mapping[str, str] | None = None,
) -> None:
    env = os.environ.copy()
    env.update(environment or {})
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for raw in process.stdout:
        emit(raw[:-1].removesuffix("\r") if raw.endswith("\n") else raw)
    code = process.wait()
    if code:
        raise RebuildError(f"command exited {code}: {' '.join(command)}")


def _contained_relative(path: str) -> Path:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise RebuildError(f"manifest path escapes its root: {path}")
    return relative


def _validated_generated_manifest(root: Path, generated: Path) -> dict[str, Any]:
    raw = json.loads((generated / "manifest.json").read_text(encoding="utf-8"))
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema") != 1
        or not isinstance(raw.get("model_definitions"), Mapping)
        or not isinstance(raw.get("files"), Mapping)
    ):
        raise RebuildError("generated manifest schema is invalid")
    manifest = json.loads(json.dumps(raw))
    model_hashes: dict[str, str] = {}
    for name in manifest["model_definitions"]:
        relative = _contained_relative(name)
        model = root / relative
        if not model.is_file() or not model.resolve().is_relative_to(root.resolve()):
            raise RebuildError(f"generated model definition is missing: {name}")
        model_hashes[name] = _sha256(model)
    actual_files = {
        path.relative_to(generated).as_posix(): _sha256(path)
        for path in sorted(generated.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    for name in actual_files:
        _contained_relative(name)
    manifest["model_definitions"] = model_hashes
    manifest["files"] = actual_files
    return manifest


def _selector_manifest_is_consistent(release: Path, manifest: Mapping[str, Any]) -> bool:
    return _valid_manifest(manifest) and release.name == manifest.get("build_digest")


def _control_periods(catalog_path: Path, database_path: Path) -> tuple[float | None, float]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = catalog["metadata"]["mpc"]["config"]
    period_row = next(row for row in rows if row.get("option_name") == "control_period")
    minimum = float(period_row["option_min"])
    configured: float | None = None
    if database_path.is_file():
        try:
            with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    "SELECT value FROM kv WHERE key = ?",
                    ("settings:general",),
                ).fetchone()
            if row is not None:
                payload = json.loads(row[0])
                settings = payload.get("current", payload)
                candidate = float(settings["controller"]["config"]["mpc"]["control_period"])
                if math.isfinite(candidate) and candidate > 0.0:
                    configured = candidate
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            sqlite3.Error,
        ):
            configured = None
    return configured, minimum


def _settings_database_path(repository: Path) -> Path:
    configured = os.environ.get("PIFIRE_DB_PATH")
    if configured is None:
        return repository / "pifire.db"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else repository / path


def _normalize_compiler_id(value: str) -> str:
    return "Clang" if value in {"Clang", "AppleClang"} else value


def _command_compiler_identity(executable: str | Path) -> dict[str, str]:
    resolved = Path(executable).resolve()
    line = subprocess.run(
        [str(resolved), "--version"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()[0]
    version = re.search(r"\d+(?:\.\d+)+", line)
    version_text = version.group(0) if version else line
    if "apple clang" in line.lower():
        apple_build = re.search(r"\(clang-([^)]+)\)", line, flags=re.IGNORECASE)
        if apple_build:
            version_text = f"{version_text}+apple.{apple_build.group(1)}"
    target = subprocess.run(
        [str(resolved), "-dumpmachine"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {
        "id": _normalize_compiler_id("Clang" if "clang" in line.lower() else "GNU"),
        "version": version_text,
        "target": target,
        "executable": str(resolved),
    }


def _configured_compiler_identity(build_root: Path) -> dict[str, str]:
    files = tuple((build_root / "CMakeFiles").glob("*/CMakeCCompiler.cmake"))
    if len(files) != 1:
        raise RebuildError("CMake compiler identity file is missing or ambiguous")
    text = files[0].read_text(encoding="utf-8")

    def setting(name: str) -> str:
        match = re.search(rf'^set\({re.escape(name)} "([^"]*)"\)', text, re.MULTILINE)
        if match is None:
            raise RebuildError(f"CMake compiler identity lacks {name}")
        return match.group(1)

    command_identity = _command_compiler_identity(setting("CMAKE_C_COMPILER"))
    target_match = re.search(
        r'^set\(CMAKE_C_COMPILER_TARGET "([^"]*)"\)',
        text,
        re.MULTILINE,
    )
    return {
        "id": _normalize_compiler_id(setting("CMAKE_C_COMPILER_ID")),
        "version": command_identity["version"],
        "target": (
            target_match.group(1) if target_match is not None and target_match.group(1) else command_identity["target"]
        ),
        "executable": command_identity["executable"],
    }


def _requested_compiler_identity() -> dict[str, str]:
    compiler = shutil.which(os.environ.get("CC", "cc"))
    if compiler is None:
        raise RebuildError("C compiler is unavailable")
    return _command_compiler_identity(compiler)


def _git(source: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RebuildError(f"unable to verify fetched acados: {detail}")
    return completed.stdout.rstrip("\r\n")


def _verify_fetched_source(source: Path, expected_manifest: Mapping[str, Any]) -> None:
    expected = expected_manifest.get("acados")
    if not isinstance(expected, Mapping):
        raise RebuildError("generated manifest lacks the acados source pin")
    if _git(source, "rev-parse", "HEAD") != expected.get("revision"):
        raise RebuildError("fetched acados revision differs from the pin")
    if _git(source, "remote", "get-url", "origin") != expected.get("url"):
        raise RebuildError("fetched acados origin differs from the pin")
    if _git(source, "describe", "--tags", "--exact-match", "HEAD") != expected.get("tag"):
        raise RebuildError("fetched acados tag differs from the pin")
    actual_dependencies: dict[str, str] = {}
    for line in _git(source, "submodule", "status", "--recursive").splitlines():
        if not line.startswith(" "):
            raise RebuildError("fetched acados dependency is not at a clean pin")
        fields = line[1:].split()
        if len(fields) < 2:
            raise RebuildError("malformed recursive dependency status")
        actual_dependencies[fields[1]] = fields[0]
    if actual_dependencies != expected.get("recursive_dependencies"):
        raise RebuildError("fetched acados recursive dependency pins differ")
    worktrees = (
        source,
        *(source / relative for relative in actual_dependencies),
    )
    for worktree in worktrees:
        if _git(
            worktree,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=all",
        ):
            raise RebuildError(f"fetched acados worktree is dirty: {worktree}")


def _current_identity(
    root: Path,
    generated: Path,
    *,
    compiler_identity: Mapping[str, str] | None = None,
) -> BuildInputIdentity:
    generated_manifest = _validated_generated_manifest(root, generated)
    inputs = [
        *sorted((root / "native/src").glob("*.c")),
        *sorted((root / "native/include").glob("*.h")),
        root / "native/acados_pifire.exports",
        root / "native/acados_pifire.version-script",
        root / "CMakeLists.txt",
        root / "native/CMakeLists.txt",
        root / "native/AcadosPifireExports.cmake",
        root / "cmake/AcadosPifirePlatform.cmake",
    ]
    if any(not path.is_file() for path in inputs):
        raise RebuildError("canonical native build input is missing")
    resolved_compiler = _requested_compiler_identity() if compiler_identity is None else dict(compiler_identity)
    cmake_output = subprocess.run(["cmake", "--version"], capture_output=True, text=True, check=True).stdout
    cmake_match = re.search(r"cmake version (\S+)", cmake_output)
    header = (root / "native/include/acados_pifire.h").read_text(encoding="utf-8")
    abi_match = re.search(r"ACADOS_PIFIRE_ABI_VERSION\s+(\d+)", header)
    if abi_match is None:
        raise RebuildError("public header lacks an ABI version")
    return BuildInputIdentity(
        generated_manifest=generated_manifest,
        native_source_sha256={path.relative_to(root).as_posix(): _sha256(path) for path in inputs},
        abi_version=int(abi_match.group(1)),
        host={"system": platform.system(), "machine": platform.machine()},
        compiler=resolved_compiler,
        cmake={
            "version": cmake_match.group(1) if cmake_match else cmake_output,
            "generator": "Unix Makefiles",
            "flags": ["CMAKE_BUILD_TYPE=Release", "ACADOS_WITH_OPENMP=ON"],
        },
        loader={
            "platform": sys.platform,
            "library_filename": _library_filename(),
            "python_implementation": platform.python_implementation().lower(),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        },
    )


def _tree_bytes(root: Path) -> bytes:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            name = path.relative_to(root).as_posix().encode()
            if path.name == "build-manifest.json":
                manifest = json.loads(path.read_text(encoding="utf-8"))
                manifest.pop("built_at", None)
                content = json.dumps(
                    manifest,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            else:
                content = path.read_bytes()
            digest.update(len(name).to_bytes(4, "big"))
            digest.update(name)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    return digest.digest()


def _rename_noreplace(source: Path, destination: Path) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        function = library.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = function(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin":
        function = library.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = function(os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise OSError("atomic no-replace rename is unavailable")
    if result:
        number = ctypes.get_errno()
        if number == 17:
            raise FileExistsError(number, os.strerror(number), destination)
        raise OSError(number, os.strerror(number), destination)


class LocalPublicationFilesystem:
    def device_id(self, path: Path) -> int:
        while not path.exists():
            path = path.parent
        return path.stat().st_dev

    def verify_complete_contained(self, stage: Path) -> tuple[str, bytes]:
        entries = tuple(stage.rglob("*"))
        files = {path.relative_to(stage).as_posix(): path for path in entries if path.is_file()}
        if (
            stage.is_symlink()
            or set(files)
            != {
                _library_filename(),
                "build-manifest.json",
            }
            or any(path.is_symlink() for path in entries)
        ):
            raise PublicationError("release is incomplete or not contained")
        manifest = json.loads(files["build-manifest.json"].read_text(encoding="utf-8"))
        if not isinstance(manifest, Mapping) or not _valid_manifest(manifest):
            raise PublicationError("release manifest is invalid")
        if manifest["library_sha256"] != _sha256(files[_library_filename()]):
            raise PublicationError("release library digest mismatch")
        return str(manifest["build_digest"]), _tree_bytes(stage)

    def fsync_staged_files(self, stage: Path) -> None:
        for path in stage.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
                descriptor = os.open(path, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)

    def fsync_directory(self, path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def rename_noreplace(self, stage: Path, release: Path) -> None:
        _rename_noreplace(stage, release)

    def seal_and_verify_release(self, release: Path, expected: bytes) -> None:
        if not release.is_dir() or release.is_symlink():
            raise PublicationError("published release is not a contained directory")
        entries = tuple(release.rglob("*"))
        if any(path.is_symlink() for path in entries):
            raise PublicationError("published release contains a symbolic link")
        for path in entries:
            if path.is_file():
                path.chmod(0o444)
                descriptor = os.open(path, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            elif path.is_dir():
                path.chmod(0o555)
        release.chmod(0o555)
        self.fsync_directory(release)
        _, actual = self.verify_complete_contained(release)
        if actual != expected:
            raise PublicationCollisionError(f"published release changed before selection: {release}")
        if release.stat().st_mode & 0o222 or any(path.stat().st_mode & 0o222 for path in entries):
            raise PublicationError("published release is not immutable")

    def existing_release_bytes(self, release: Path) -> bytes:
        return _tree_bytes(release)

    def discard_stage(self, stage: Path) -> None:
        shutil.rmtree(stage)

    def atomic_replace_selector(self, selector: Path, digest: str) -> None:
        temporary = selector.parent / f".current-{uuid4().hex}"
        try:
            temporary.symlink_to(Path("releases") / digest, target_is_directory=True)
            os.replace(temporary, selector)
        finally:
            temporary.unlink(missing_ok=True)


class _GreyHandle(ctypes.Structure):
    pass


GreyHandlePointer = ctypes.POINTER(_GreyHandle)


class _GreyConfig(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("horizon_steps", ctypes.c_uint32),
        ("C_c", ctypes.c_double),
        ("h_amb", ctypes.c_double),
        ("T_amb", ctypes.c_double),
        ("theta", ctypes.c_double),
        ("K_Q", ctypes.c_double),
        ("sigma", ctypes.c_double),
        ("temperature_weight", ctypes.c_double),
        ("terminal_weight", ctypes.c_double),
        ("move_weight", ctypes.c_double),
        ("residual_weight", ctypes.c_double),
        ("max_iterations", ctypes.c_int32),
    ]


class _GreyInput(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("state", ctypes.c_double * 10),
        ("setpoint_c", ctypes.c_double),
        ("q_previous", ctypes.c_double),
        ("equilibrium_q", ctypes.c_double),
    ]


class _GreyDiagnostics(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("status", ctypes.c_int32),
        ("backend_status", ctypes.c_int32),
        ("iterations", ctypes.c_int32),
        ("solve_time_s", ctypes.c_double),
        ("objective", ctypes.c_double),
        ("kkt_residual", ctypes.c_double),
        ("constraint_residual", ctypes.c_double),
        ("warm_started", ctypes.c_int32),
    ]


class _GreyOutput(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("sequence_length", ctypes.c_uint32),
        ("sequence_q", ctypes.c_double * 24),
        ("sequence_residual", ctypes.c_double * 24),
        ("objective", ctypes.c_double),
        ("diagnostics", _GreyDiagnostics),
    ]


def _reference_discrete_map(
    state: Sequence[float],
    previous_residual: float,
    residual: float,
    parameters: Sequence[float],
) -> tuple[float, ...]:
    del previous_residual

    def rhs(current: Sequence[float]) -> tuple[float, ...]:
        C_c, h_amb, T_amb, theta, K_Q, sigma = parameters[:6]
        equilibrium_q = parameters[7]
        derivatives = [(equilibrium_q + residual - current[0]) / (theta / 8.0)]
        derivatives.extend((current[index - 1] - current[index]) / (theta / 8.0) for index in range(1, 8))
        derivatives.append(
            (
                K_Q * current[7]
                - h_amb * (current[8] - T_amb)
                - sigma * ((current[8] + 273.15) ** 4 - (T_amb + 273.15) ** 4)
                + current[9]
            )
            / C_c
        )
        derivatives.append(0.0)
        return tuple(derivatives)

    current = tuple(float(value) for value in state)
    step = 25.0 / 8.0
    for _ in range(8):
        k1 = rhs(current)
        k2 = rhs(tuple(x + 0.5 * step * k for x, k in zip(current, k1)))
        k3 = rhs(tuple(x + 0.5 * step * k for x, k in zip(current, k2)))
        k4 = rhs(tuple(x + step * k for x, k in zip(current, k3)))
        current = tuple(x + step * (a + 2.0 * b + 2.0 * c + d) / 6.0 for x, a, b, c, d in zip(current, k1, k2, k3, k4))
    return (*current, float(residual))


def _native_output_is_finite(output: _GreyOutput, *, expected_horizon: int) -> bool:
    return (
        output.sequence_length == expected_horizon
        and math.isfinite(output.objective)
        and all(
            math.isfinite(output.sequence_q[index]) and math.isfinite(output.sequence_residual[index])
            for index in range(expected_horizon)
        )
    )


def _objective_matches(solve_input: _GreyInput, output: _GreyOutput, horizon: int) -> bool:
    state = tuple(float(value) for value in solve_input.state)
    previous = solve_input.q_previous - solve_input.equilibrium_q
    parameters = (
        320.0,
        0.5,
        20.0,
        50.0,
        350.0,
        0.0,
        solve_input.setpoint_c,
        solve_input.equilibrium_q,
        1.0,
        1.0,
        0.1,
        0.0,
    )
    running = 0.0
    for index in range(horizon):
        residual = float(output.sequence_residual[index])
        running += (state[8] - solve_input.setpoint_c) ** 2
        running += 0.1 * (residual - previous) ** 2
        state = _reference_discrete_map(state, previous, residual, parameters)[:10]
        previous = residual
    expected = 0.5 * (running + (state[8] - solve_input.setpoint_c) ** 2)
    return math.isclose(output.objective, expected, rel_tol=1e-8, abs_tol=1e-8)


def _validate_native_behavior(gate: str, cold: NativeEvidence, warm: NativeEvidence) -> None:
    if gate == "cost-scaling" and not cold.objective_matches:
        raise RebuildError("cost scaling objective mismatch")
    if gate == "cold-solves" and cold.warm_started:
        raise RebuildError("cold solve was reported as warm")
    if gate == "warm-solves" and not warm.warm_started:
        raise RebuildError("warm solve did not use the retained iterate")


class NativeSmoke:
    def __init__(self, library_path: Path) -> None:
        self.library = ctypes.CDLL(str(library_path))
        self.library.acados_pifire_abi_version.restype = ctypes.c_int
        self.library.acados_pifire_grey_create.argtypes = [
            ctypes.POINTER(_GreyConfig),
            ctypes.POINTER(GreyHandlePointer),
        ]
        self.library.acados_pifire_grey_create.restype = ctypes.c_int32
        self.library.acados_pifire_grey_solve.argtypes = [
            GreyHandlePointer,
            ctypes.POINTER(_GreyInput),
            ctypes.POINTER(_GreyOutput),
        ]
        self.library.acados_pifire_grey_solve.restype = ctypes.c_int32
        self.library.acados_pifire_grey_destroy.argtypes = [GreyHandlePointer]
        self.library.acados_pifire_grey_destroy.restype = None

    def create(self, horizon: int) -> tuple[int, Any]:
        config = _GreyConfig(
            ctypes.sizeof(_GreyConfig),
            horizon,
            320.0,
            0.5,
            20.0,
            50.0,
            350.0,
            0.0,
            1.0,
            1.0,
            0.1,
            0.0,
            10,
        )
        handle = GreyHandlePointer()
        status = self.library.acados_pifire_grey_create(ctypes.byref(config), ctypes.byref(handle))
        return int(status), handle

    def solve_evidence(self, handle: Any, index: int, expected_horizon: int) -> tuple[SolveSample, NativeEvidence]:
        fraction = ((index * 1103515245 + 12345) & 0xFFFF) / 65535.0
        value = _GreyInput()
        value.struct_size = ctypes.sizeof(_GreyInput)
        value.state[:] = [0.15 + 0.2 * fraction] * 8 + [
            96.0 + 12.0 * fraction,
            2.0 * fraction - 1.0,
        ]
        value.setpoint_c = 118.0 + 8.0 * fraction
        value.q_previous = 0.15 + 0.25 * fraction
        value.equilibrium_q = 0.2 + 0.15 * fraction
        output = _GreyOutput()
        output.struct_size = ctypes.sizeof(_GreyOutput)
        started = time.perf_counter()
        status = int(self.library.acados_pifire_grey_solve(handle, ctypes.byref(value), ctypes.byref(output)))
        elapsed = time.perf_counter() - started
        finite = _native_output_is_finite(output, expected_horizon=expected_horizon)
        sample = SolveSample(status, elapsed, finite)
        evidence = NativeEvidence(
            status=status,
            finite=finite,
            objective_matches=finite and _objective_matches(value, output, expected_horizon),
            warm_started=bool(output.diagnostics.warm_started),
        )
        return sample, evidence

    def solve(self, handle: Any, index: int, expected_horizon: int) -> SolveSample:
        return self.solve_evidence(handle, index, expected_horizon)[0]

    def destroy(self, handle: Any) -> None:
        if handle:
            self.library.acados_pifire_grey_destroy(handle)


class LocalOperations:
    def __init__(self, repository: Path) -> None:
        self.paths = BuildPaths.for_repository(repository)
        self.current_release: str | None = None
        self._source: Path | None = None
        self._compiler_identity: Mapping[str, str] | None = None
        self._filesystem = LocalPublicationFilesystem()

    def emit(self, line: str) -> None:
        print(line, flush=True)

    @contextmanager
    def build_lock(self) -> Iterator[None]:
        self.paths.runtime_releases.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.paths.lock_file, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def inspect_staleness(self) -> tuple[StalenessClass, ...]:
        identity = _current_identity(self.paths.repository, self.paths.generated_target)
        try:
            release = self.paths.selector.resolve(strict=True)
            if release.parent != self.paths.runtime_releases.resolve(strict=True):
                raise ValueError("current selector escapes releases")
            self.current_release = release.name
            manifest = json.loads((release / "build-manifest.json").read_text(encoding="utf-8"))
            if not _selector_manifest_is_consistent(release, manifest):
                manifest = {"invalid": True}
            library = release / _library_filename()
            library_digest = _sha256(library) if library.is_file() and not library.is_symlink() else None
        except FileNotFoundError:
            manifest, library_digest = None, None
        except OSError, ValueError, json.JSONDecodeError:
            manifest, library_digest = {"invalid": True}, None
        return classify_staleness(manifest, identity, actual_library_sha256=library_digest)

    def configure_and_fetch(self) -> None:
        self.paths.build_root.mkdir(parents=True, exist_ok=True)
        requested_compiler = _requested_compiler_identity()
        _stream(
            (
                "cmake",
                "-S",
                str(self.paths.repository),
                "-B",
                str(self.paths.build_root),
                "-G",
                "Unix Makefiles",
                "-DCMAKE_BUILD_TYPE=Release",
                f"-DCMAKE_C_COMPILER={requested_compiler['executable']}",
                f"-DACADOS_PIFIRE_GENERATED_ROOT={self.paths.generated_target}",
                (f"-DACADOS_PIFIRE_LIBRARY_OUTPUT_DIRECTORY={self.paths.build_root / 'bootstrap-output'}"),
            ),
            cwd=self.paths.repository,
            emit=self.emit,
        )
        source_file = self.paths.build_root / "acados-source-dir.txt"
        self._source = Path(source_file.read_text(encoding="utf-8").strip()).resolve(strict=True)
        configured_compiler = _configured_compiler_identity(self.paths.build_root)
        if configured_compiler != requested_compiler:
            raise RebuildError("CMake configured compiler differs from requested compiler")
        self._compiler_identity = configured_compiler
        _verify_fetched_source(
            self._source,
            _validated_generated_manifest(self.paths.repository, self.paths.generated_target),
        )

    def create_generated_staging(self) -> Path:
        self.paths.generated_staging_root.mkdir(parents=True, exist_ok=True)
        destination = self.paths.generated_staging_root / uuid4().hex
        destination.mkdir()
        return destination

    def generate(self, command: tuple[str, ...], destination: Path) -> None:
        if self._source is None:
            raise RebuildError("configure/fetch did not resolve acados")
        _stream(
            command,
            cwd=self.paths.repository,
            emit=self.emit,
            environment={"ACADOS_SOURCE_DIR": str(self._source)},
        )

    def validate_generated(self, gate: str, destination: Path) -> None:
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        if gate == "provenance":
            for relative, digest in manifest.get("files", {}).items():
                if _sha256(destination / relative) != digest:
                    raise RebuildError(f"generated provenance mismatch: {relative}")
            for relative, digest in manifest.get("model_definitions", {}).items():
                if _sha256(self.paths.repository / relative) != digest:
                    raise RebuildError(f"model provenance mismatch: {relative}")
        elif gate == "equation":
            metadata = json.loads((destination / "grey_box/pifire_grey.json").read_text(encoding="utf-8"))
            dimensions = metadata.get("dims", {})
            actual = {key: dimensions.get(key) for key in ("N", "nx", "nu", "np")}
            if actual != {"N": 24, "nx": 11, "nu": 1, "np": 12}:
                raise RebuildError("generated equation dimensions mismatch")
            if self._source is None:
                raise RebuildError("configure/fetch did not establish acados source")
            _stream(
                (
                    "uv",
                    "run",
                    "--no-default-groups",
                    "--group",
                    "codegen",
                    "python",
                    "-m",
                    "controller.acados.codegen.cli",
                    "--equation-parity",
                    str(destination),
                ),
                cwd=self.paths.repository,
                emit=self.emit,
                environment={"ACADOS_SOURCE_DIR": str(self._source)},
            )
        elif gate == "parity":
            actual_files = {
                path.relative_to(destination).as_posix()
                for path in destination.rglob("*")
                if path.is_file() and path.name != "manifest.json"
            }
            if actual_files != set(manifest.get("files", {})):
                raise RebuildError("generated tree parity mismatch")
        else:
            raise ValueError(gate)

    def create_runtime_staging(self) -> Path:
        self.paths.runtime_staging_root.mkdir(parents=True, exist_ok=True)
        destination = self.paths.runtime_staging_root / uuid4().hex
        destination.mkdir()
        return destination

    def compile_native(self, generated: Path, destination: Path) -> None:
        if self._compiler_identity is None:
            raise RebuildError("configure/fetch did not establish a compiler")
        _stream(
            (
                "cmake",
                "-S",
                str(self.paths.repository),
                "-B",
                str(self.paths.build_root),
                "-G",
                "Unix Makefiles",
                "-DCMAKE_BUILD_TYPE=Release",
                f"-DCMAKE_C_COMPILER={self._compiler_identity['executable']}",
                f"-DACADOS_PIFIRE_GENERATED_ROOT={generated}",
                f"-DACADOS_PIFIRE_LIBRARY_OUTPUT_DIRECTORY={destination}",
            ),
            cwd=self.paths.repository,
            emit=self.emit,
        )
        if _configured_compiler_identity(self.paths.build_root) != self._compiler_identity:
            raise RebuildError("CMake compiler identity changed before build")
        _stream(
            (
                "cmake",
                "--build",
                str(self.paths.build_root),
                "--target",
                "acados_pifire",
                "--parallel",
                str(max(1, os.cpu_count() or 1)),
            ),
            cwd=self.paths.repository,
            emit=self.emit,
        )
        library = destination / _library_filename()
        manifest = canonical_build_manifest(
            _current_identity(
                self.paths.repository,
                generated,
                compiler_identity=self._compiler_identity,
            ),
            library_sha256=_sha256(library),
        )
        (destination / "build-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def validate_runtime(self, gate: str, destination: Path) -> None:
        smoke = NativeSmoke(destination / _library_filename())
        if gate == "abi":
            if smoke.library.acados_pifire_abi_version() != 2:
                raise RebuildError("native ABI mismatch")
        elif gate == "loader":
            self._filesystem.verify_complete_contained(destination)
        elif gate == "horizons-5-through-24":
            for horizon in HORIZONS:
                status, handle = smoke.create(horizon)
                try:
                    sample = smoke.solve(handle, horizon, horizon) if handle else None
                    if status != 0 or sample is None or sample.status != 0 or not sample.result_is_finite:
                        raise RebuildError(f"horizon {horizon} smoke failed")
                finally:
                    smoke.destroy(handle)
        elif gate == "invalid-horizons":
            for horizon in (4, 25):
                status, handle = smoke.create(horizon)
                try:
                    if status != 1 or handle:
                        raise RebuildError(f"invalid horizon {horizon} was accepted")
                finally:
                    smoke.destroy(handle)
        elif gate in {"cost-scaling", "cold-solves", "warm-solves"}:
            status, handle = smoke.create(24)
            try:
                first, cold = smoke.solve_evidence(handle, 1, 24)
                second, warm = smoke.solve_evidence(handle, 2, 24)
                if (
                    status != 0
                    or first.status != 0
                    or second.status != 0
                    or not first.result_is_finite
                    or not second.result_is_finite
                ):
                    raise RebuildError(f"{gate} smoke failed")
                _validate_native_behavior(gate, cold, warm)
            finally:
                smoke.destroy(handle)
        else:
            raise ValueError(gate)

    def run_timing_gate(
        self,
        destination: Path,
        *,
        horizons: tuple[int, ...],
        solves_per_horizon: int,
    ) -> tuple[Mapping[str, Any], ...]:
        if horizons != HORIZONS or solves_per_horizon != SOLVES_PER_HORIZON:
            raise TimingGateError("timing matrix must be horizons 5-24 x 1,000")
        smoke = NativeSmoke(destination / _library_filename())
        matrix: dict[int, tuple[SolveSample, ...]] = {}
        for horizon in horizons:
            status, handle = smoke.create(horizon)
            if status != 0 or not handle:
                raise TimingGateError(f"timing create failed at horizon {horizon}")
            try:
                matrix[horizon] = tuple(
                    smoke.solve(
                        handle,
                        horizon * solves_per_horizon + index,
                        horizon,
                    )
                    for index in range(solves_per_horizon)
                )
            finally:
                smoke.destroy(handle)
        configured, minimum = _control_periods(
            self.paths.repository / "controller/controllers.json",
            _settings_database_path(self.paths.repository),
        )
        return tuple(
            asdict(row)
            for row in validate_timing_gate(
                matrix,
                configured_control_period_seconds=configured,
                catalog_minimum_control_period_seconds=minimum,
            )
        )

    def publish_generated(self, destination: Path) -> None:
        from controller.acados.codegen.cli import _atomic_replace_tree

        _atomic_replace_tree(destination, self.paths.generated_target)
        self._filesystem.fsync_directory(self.paths.generated_target.parent)
        shutil.rmtree(destination, ignore_errors=True)

    def publish_runtime(self, destination: Path) -> str:
        release = publish_immutable_release(
            destination,
            self.paths.runtime_root,
            filesystem=self._filesystem,
            select=False,
        )
        return release.name

    def replace_selector(self, digest: str) -> None:
        self._filesystem.atomic_replace_selector(self.paths.selector, digest)
        self._filesystem.fsync_directory(self.paths.runtime_root)
        self.current_release = digest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rebuild-acados.sh")
    parser.add_argument("--if-needed", action="store_true")
    arguments = parser.parse_args(argv)
    operations = LocalOperations(Path(__file__).resolve().parents[1])
    mode = BuildMode.IF_NEEDED if arguments.if_needed else BuildMode.FULL
    try:
        result = run_rebuild(mode, operations=operations)
    except (OSError, RebuildError, ValueError, subprocess.SubprocessError) as error:
        print(f"rebuild-acados: {error}", file=sys.stderr)
        return 1
    if result.changed:
        print(f"acados runtime selected release {result.build_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
