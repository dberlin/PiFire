#!/usr/bin/env python3
"""Run the complete integration gate against one unchanged revision."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import IO, Literal, Protocol, cast


@dataclass(frozen=True, slots=True)
class GateCommand:
    """One mandatory command in the release gate."""

    name: str
    argv: tuple[str, ...]
    cwd: str


@dataclass(frozen=True, slots=True)
class CommandEvidence:
    """Durable result and logs for one gate command."""

    name: str
    argv: tuple[str, ...]
    cwd: str
    exit_code: int | None
    status: Literal["passed", "failed", "interrupted", "revision_changed", "not_run"]
    stdout_log: str | None
    stderr_log: str | None
    stdout_sha256: str | None
    stderr_sha256: str | None


@dataclass(frozen=True, slots=True)
class GateEvidence:
    """Complete evidence for an attempted exact-revision gate."""

    schema_version: Literal[2]
    revision: str
    status: Literal["passed", "failed"]
    preflight: tuple[CommandEvidence, ...]
    commands: tuple[CommandEvidence, ...]


class _CompletedCommand(Protocol):
    returncode: int


_RunCommand = Callable[..., _CompletedCommand]
_ResolveRevision = Callable[[], str]

_PREFLIGHT_COMMANDS = (
    GateCommand(
        "contract-preflight",
        (
            "uv",
            "run",
            "pytest",
            "tests/unit/test_no_cross_test_imports.py",
            "tests/unit/mpc/test_mutation_score.py",
            "tests/unit/common/test_current_contract_fixtures.py",
            "-q",
        ),
        ".",
    ),
)
_REQUIRED_COMMANDS = (
    GateCommand("rebuild-acados", ("./rebuild-acados.sh", "--if-needed"), "."),
    GateCommand("python-default", ("uv", "run", "pytest", "tests/"), "."),
    GateCommand(
        "python-slow",
        ("uv", "run", "pytest", "-m", "slow", "tests/"),
        ".",
    ),
    GateCommand("bun-workspaces", ("bun", "run", "test"), "."),
    GateCommand("web-react-e2e", ("bun", "run", "test:e2e"), "web-react"),
)
_FULL_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_FULL_OPERATION_ID = re.compile(r"[0-9a-f]{128}\Z")
_VERIFIED_BOOKMARK = "cumulative-mpc-learning"
_DEFAULT_ARTIFACT_ROOT = Path(".artifacts/exact-revision")
_BROWSER_BACKEND_OVERRIDES = ("PUBLIC_PIFIRE_URL", "PUBLIC_PIFIRE_TARGET")


def preflight_commands() -> tuple[GateCommand, ...]:
    """Return prerequisite contract checks that do not count as release commands."""

    return _PREFLIGHT_COMMANDS


def required_commands() -> tuple[GateCommand, ...]:
    """Return the mandatory release commands in their authoritative order."""

    return _REQUIRED_COMMANDS


def _not_run(command: GateCommand) -> CommandEvidence:
    return CommandEvidence(
        name=command.name,
        argv=command.argv,
        cwd=command.cwd,
        exit_code=None,
        status="not_run",
        stdout_log=None,
        stderr_log=None,
        stdout_sha256=None,
        stderr_sha256=None,
    )


def _resolve_revision_safely(resolve_revision: _ResolveRevision) -> str | None:
    try:
        return resolve_revision()
    except (KeyboardInterrupt, SystemExit, Exception):
        return None


def _revision_is_expected(resolve_revision: _ResolveRevision, expected_revision: str) -> bool:
    resolved = _resolve_revision_safely(resolve_revision)
    return resolved is not None and _FULL_REVISION.fullmatch(resolved) is not None and resolved == expected_revision


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write_evidence(path: Path, evidence: GateEvidence) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(asdict(evidence), temporary_file, indent=2)
            _ = temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _failed_evidence(
    *,
    expected_revision: str,
    preflight: list[CommandEvidence],
    commands: list[CommandEvidence],
    evidence_path: Path,
) -> GateEvidence:
    evidence = GateEvidence(
        schema_version=2,
        revision=expected_revision,
        status="failed",
        preflight=tuple(preflight),
        commands=tuple(commands),
    )
    _write_evidence(evidence_path, evidence)
    return evidence


def _invalid_stage_evidence_index(
    *,
    expected: tuple[GateCommand, ...],
    actual: list[CommandEvidence],
    revision_dir: Path,
    first_log_index: int,
) -> int | None:
    if len(actual) != len(expected):
        return 0

    for position, (required, evidence) in enumerate(zip(expected, actual, strict=True)):
        log_index = first_log_index + position
        stdout_name = f"{log_index:02d}-{required.name}.stdout.log"
        stderr_name = f"{log_index:02d}-{required.name}.stderr.log"
        if (
            evidence.name != required.name
            or evidence.argv != required.argv
            or evidence.cwd != required.cwd
            or evidence.exit_code != 0
            or evidence.status != "passed"
            or evidence.stdout_log != stdout_name
            or evidence.stderr_log != stderr_name
            or evidence.stdout_sha256 is None
            or evidence.stderr_sha256 is None
        ):
            return position

        try:
            stdout_sha256 = _sha256(revision_dir / stdout_name)
            stderr_sha256 = _sha256(revision_dir / stderr_name)
        except OSError:
            return position
        if (
            stdout_sha256 != evidence.stdout_sha256
            or stderr_sha256 != evidence.stderr_sha256
        ):
            return position

    return None


def _command_environment(command: GateCommand) -> dict[str, str] | None:
    database_path = os.environ.get("PIFIRE_GATE_LIVE_DB_PATH")
    log_dir = os.environ.get("PIFIRE_GATE_LIVE_LOG_DIR")
    if database_path is None and log_dir is None:
        return None
    if database_path is None or log_dir is None:
        raise RuntimeError("live gate database and log paths must be configured together")
    environment = dict(os.environ)
    environment.pop("PIFIRE_GATE_LIVE_DB_PATH", None)
    environment.pop("PIFIRE_GATE_LIVE_LOG_DIR", None)
    environment.pop("PIFIRE_DB_PATH", None)
    environment.pop("PIFIRE_LOG_DIR", None)
    if command.name == "web-react-e2e":
        environment["PIFIRE_DB_PATH"] = database_path
        environment["PIFIRE_LOG_DIR"] = log_dir
    return environment


def _run_one_command(
    *,
    root: Path,
    revision_dir: Path,
    index: int,
    command: GateCommand,
    run_command: _RunCommand,
) -> CommandEvidence:
    stdout_name = f"{index:02d}-{command.name}.stdout.log"
    stderr_name = f"{index:02d}-{command.name}.stderr.log"
    stdout_path = revision_dir / stdout_name
    stderr_path = revision_dir / stderr_name
    exit_code: int | None = None
    interrupted = False

    try:
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            runner_arguments: dict[str, object] = {
                "cwd": root / command.cwd,
                "check": False,
                "stdout": cast(IO[bytes], stdout_file),
                "stderr": cast(IO[bytes], stderr_file),
            }
            environment = _command_environment(command)
            if environment is not None:
                runner_arguments["env"] = environment
            completed = run_command(command.argv, **runner_arguments)
            returncode = completed.returncode
            if type(returncode) is not int:
                raise TypeError("command runner returned an invalid exit code")
            exit_code = returncode
    except (KeyboardInterrupt, SystemExit, Exception):
        interrupted = True

    return CommandEvidence(
        name=command.name,
        argv=command.argv,
        cwd=command.cwd,
        exit_code=exit_code,
        status="interrupted" if interrupted else ("passed" if exit_code == 0 else "failed"),
        stdout_log=stdout_name,
        stderr_log=stderr_name,
        stdout_sha256=_sha256(stdout_path),
        stderr_sha256=_sha256(stderr_path),
    )


def _run_gate_attempt(
    *,
    root: Path,
    expected_revision: str,
    resolved_revision: str | None,
    resolve_revision: _ResolveRevision,
    revision_dir: Path,
    preflight: tuple[GateCommand, ...],
    commands: tuple[GateCommand, ...],
    run_command: _RunCommand,
) -> GateEvidence:
    evidence_path = revision_dir / "evidence.json"
    preflight_evidence = [_not_run(command) for command in _PREFLIGHT_COMMANDS]
    command_evidence = [_not_run(command) for command in _REQUIRED_COMMANDS]

    if resolved_revision != expected_revision:
        preflight_evidence[0] = replace(preflight_evidence[0], status="revision_changed")
        return _failed_evidence(
            expected_revision=revision_dir.name,
            preflight=preflight_evidence,
            commands=command_evidence,
            evidence_path=evidence_path,
        )

    attempt_evidence = GateEvidence(
        schema_version=2,
        revision=expected_revision,
        status="failed",
        preflight=tuple(preflight_evidence),
        commands=tuple(command_evidence),
    )
    _write_evidence(evidence_path, attempt_evidence)

    if preflight != _PREFLIGHT_COMMANDS or commands != _REQUIRED_COMMANDS:
        return attempt_evidence

    gate_commands = preflight + commands
    for log_index, command in enumerate(gate_commands):
        is_preflight = log_index < len(preflight)
        evidence_index = log_index if is_preflight else log_index - len(preflight)
        stage_evidence = preflight_evidence if is_preflight else command_evidence

        if log_index > 0 and not _revision_is_expected(resolve_revision, expected_revision):
            stage_evidence[evidence_index] = replace(
                stage_evidence[evidence_index],
                status="revision_changed",
            )
            return _failed_evidence(
                expected_revision=expected_revision,
                preflight=preflight_evidence,
                commands=command_evidence,
                evidence_path=evidence_path,
            )

        result = _run_one_command(
            root=root,
            revision_dir=revision_dir,
            index=log_index,
            command=command,
            run_command=run_command,
        )
        stage_evidence[evidence_index] = result

        if not _revision_is_expected(resolve_revision, expected_revision):
            stage_evidence[evidence_index] = replace(result, status="revision_changed")
            return _failed_evidence(
                expected_revision=expected_revision,
                preflight=preflight_evidence,
                commands=command_evidence,
                evidence_path=evidence_path,
            )
        if result.status != "passed":
            return _failed_evidence(
                expected_revision=expected_revision,
                preflight=preflight_evidence,
                commands=command_evidence,
                evidence_path=evidence_path,
            )

    if not _revision_is_expected(resolve_revision, expected_revision):
        command_evidence[-1] = replace(command_evidence[-1], status="revision_changed")
        return _failed_evidence(
            expected_revision=expected_revision,
            preflight=preflight_evidence,
            commands=command_evidence,
            evidence_path=evidence_path,
        )

    invalid_preflight_index = _invalid_stage_evidence_index(
        expected=_PREFLIGHT_COMMANDS,
        actual=preflight_evidence,
        revision_dir=revision_dir,
        first_log_index=0,
    )
    if invalid_preflight_index is not None:
        preflight_evidence[invalid_preflight_index] = replace(
            preflight_evidence[invalid_preflight_index],
            status="failed",
        )
        return _failed_evidence(
            expected_revision=expected_revision,
            preflight=preflight_evidence,
            commands=command_evidence,
            evidence_path=evidence_path,
        )

    invalid_command_index = _invalid_stage_evidence_index(
        expected=_REQUIRED_COMMANDS,
        actual=command_evidence,
        revision_dir=revision_dir,
        first_log_index=len(_PREFLIGHT_COMMANDS),
    )
    if invalid_command_index is not None:
        command_evidence[invalid_command_index] = replace(
            command_evidence[invalid_command_index],
            status="failed",
        )
        return _failed_evidence(
            expected_revision=expected_revision,
            preflight=preflight_evidence,
            commands=command_evidence,
            evidence_path=evidence_path,
        )

    evidence = GateEvidence(
        schema_version=2,
        revision=expected_revision,
        status="passed",
        preflight=tuple(preflight_evidence),
        commands=tuple(command_evidence),
    )
    _write_evidence(evidence_path, evidence)
    return evidence


@contextmanager
def _artifact_lock(artifact_root: Path) -> Iterator[None]:
    artifact_root.mkdir(parents=True, exist_ok=True)
    with (artifact_root / ".gate.lock").open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield


def _run_gate_with_lock_held(
    *,
    root: Path,
    expected_revision: str,
    resolve_revision: _ResolveRevision,
    artifact_root: Path,
    run_command: _RunCommand,
) -> GateEvidence:
    preflight = preflight_commands()
    commands = required_commands()
    resolved_revision = _resolve_revision_safely(resolve_revision)
    artifact_revision = (
        resolved_revision
        if resolved_revision is not None and _FULL_REVISION.fullmatch(resolved_revision) is not None
        else expected_revision
    )
    revision_dir = artifact_root / artifact_revision
    revision_dir.mkdir(parents=True, exist_ok=True)
    return _run_gate_attempt(
        root=root,
        expected_revision=expected_revision,
        resolved_revision=resolved_revision,
        resolve_revision=resolve_revision,
        revision_dir=revision_dir,
        preflight=preflight,
        commands=commands,
        run_command=run_command,
    )


def _validate_durable_pass_evidence(*, evidence: GateEvidence, revision_dir: Path) -> None:
    evidence_path = revision_dir / "evidence.json"
    try:
        persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("durable evidence could not be revalidated after push") from error

    expected = json.loads(json.dumps(asdict(evidence)))
    invalid_preflight_index = _invalid_stage_evidence_index(
        expected=_PREFLIGHT_COMMANDS,
        actual=list(evidence.preflight),
        revision_dir=revision_dir,
        first_log_index=0,
    )
    invalid_command_index = _invalid_stage_evidence_index(
        expected=_REQUIRED_COMMANDS,
        actual=list(evidence.commands),
        revision_dir=revision_dir,
        first_log_index=len(_PREFLIGHT_COMMANDS),
    )
    if (
        evidence.schema_version != 2
        or evidence.status != "passed"
        or evidence.revision != revision_dir.name
        or persisted != expected
        or invalid_preflight_index is not None
        or invalid_command_index is not None
    ):
        raise RuntimeError("durable evidence could not be revalidated after push")


def run_gate(
    *,
    root: Path,
    expected_revision: str,
    resolve_revision: Callable[[], str],
    artifact_root: Path,
    run_command: _RunCommand = subprocess.run,
) -> GateEvidence:
    """Run every gate command only while the exact revision remains checked out."""

    if _FULL_REVISION.fullmatch(expected_revision) is None:
        raise ValueError("expected revision must be a full 40-character lowercase hexadecimal revision")

    with _artifact_lock(artifact_root):
        return _run_gate_with_lock_held(
            root=root,
            expected_revision=expected_revision,
            resolve_revision=resolve_revision,
            artifact_root=artifact_root,
            run_command=run_command,
        )


def resolve_bookmark_revision(
    root: Path,
    bookmark: str,
    *,
    operation_id: str | None = None,
) -> str:
    """Resolve one Jujutsu bookmark or revset to its full Git revision."""

    if not bookmark or bookmark != bookmark.strip():
        raise ValueError("bookmark must be non-empty and contain no surrounding whitespace")
    if operation_id is not None and _FULL_OPERATION_ID.fullmatch(operation_id) is None:
        raise ValueError("operation ID must be a full 128-character lowercase hexadecimal ID")
    operation_argv = () if operation_id is None else ("--at-operation", operation_id)
    completed = subprocess.run(
        (
            "jj",
            *operation_argv,
            "--no-pager",
            "log",
            "--no-graph",
            "-r",
            bookmark,
            "-T",
            'commit_id ++ "\\n"',
        ),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "revision resolver exited without an error message"
        raise RuntimeError(f"could not resolve {bookmark!r}: {detail}")
    revision = completed.stdout.strip()
    if _FULL_REVISION.fullmatch(revision) is None:
        raise RuntimeError(f"{bookmark!r} did not resolve to exactly one full revision")
    return revision


def resolve_current_operation_id(root: Path) -> str:
    """Resolve the current Jujutsu operation to its full immutable ID."""

    completed = subprocess.run(
        (
            "jj",
            "--no-pager",
            "op",
            "log",
            "--no-graph",
            "--limit",
            "1",
            "-T",
            'id ++ "\\n"',
        ),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "operation resolver exited without an error message"
        raise RuntimeError(f"could not resolve current operation: {detail}")
    operation_id = completed.stdout.strip()
    if _FULL_OPERATION_ID.fullmatch(operation_id) is None:
        raise RuntimeError(
            "current operation did not resolve to one full 128-character lowercase hexadecimal ID"
        )
    return operation_id


def _resolve_current_revision(root: Path) -> str:
    return resolve_bookmark_revision(root, "@")


def _available_local_ports(count: int) -> tuple[int, ...]:
    ports: list[int] = []
    while len(ports) < count:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = cast(tuple[str, int], listener.getsockname())[1]
        if port not in ports:
            ports.append(port)
    return tuple(ports)


def _runtime_failure_detail(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        return "runtime exited without a readable error log"


def _prepare_isolated_source(root: Path, runtime: Path) -> tuple[Path, Path]:
    source_root = runtime / "source"
    shutil.copytree(
        root,
        source_root,
        ignore=shutil.ignore_patterns(
            ".artifacts",
            ".git",
            ".jj",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "backups",
            "build",
            "history",
            "logs",
            "node_modules",
            "pifire.db",
            "pifire.db-*",
            "test-results",
            "*.pyc",
        ),
    )
    log_dir = runtime / "logs"
    log_dir.mkdir()
    (source_root / "backups").mkdir()
    (source_root / "history").mkdir()
    (source_root / "logs").symlink_to(log_dir, target_is_directory=True)
    return source_root, log_dir


@contextmanager
def _isolated_live_pifire(root: Path) -> Iterator[None]:
    """Run a disposable non-hardware backend/controller for the local gate."""

    isolated_names = (
        "PIFIRE_BACKEND_URL",
        "PIFIRE_GATE_LIVE_DB_PATH",
        "PIFIRE_GATE_LIVE_LOG_DIR",
        "PORT",
        "DEMO_PORT",
        "PIFIRE_DB_PATH",
        "PIFIRE_LOG_DIR",
        *_BROWSER_BACKEND_OVERRIDES,
    )
    prior_environment = {name: os.environ.get(name) for name in isolated_names}
    for name in _BROWSER_BACKEND_OVERRIDES:
        os.environ.pop(name, None)

    try:
        with tempfile.TemporaryDirectory(prefix="pifire-exact-revision-") as temporary:
            runtime = Path(temporary)
            source_root, log_dir = _prepare_isolated_source(root, runtime)
            backend_port, app_port, demo_port = _available_local_ports(3)
            live_database_path = str(runtime / "pifire.db")
            live_log_dir = str(log_dir)
            gate_environment = {
                "PIFIRE_BACKEND_URL": f"http://127.0.0.1:{backend_port}",
                "PIFIRE_GATE_LIVE_DB_PATH": live_database_path,
                "PIFIRE_GATE_LIVE_LOG_DIR": live_log_dir,
                "PORT": str(app_port),
                "DEMO_PORT": str(demo_port),
            }
            service_environment = {
                **os.environ,
                **gate_environment,
                "PIFIRE_DB_PATH": live_database_path,
                "PIFIRE_LOG_DIR": live_log_dir,
            }
            os.environ.update(gate_environment)
            os.environ.pop("PIFIRE_DB_PATH", None)
            os.environ.pop("PIFIRE_LOG_DIR", None)

            control_stdout_path = log_dir / "control.stdout.log"
            control_stderr_path = log_dir / "control.stderr.log"
            web_stdout_path = log_dir / "web.stdout.log"
            web_stderr_path = log_dir / "web.stderr.log"
            processes: list[subprocess.Popen[bytes]] = []
            try:
                initialization = subprocess.run(
                    (
                        sys.executable,
                        "-c",
                        "from common import datastore; "
                        "from common.persistence.runtime import read_settings, write_settings; "
                        "datastore.init(); settings = read_settings(); "
                        "settings['platform']['real_hw'] = False; write_settings(settings)",
                    ),
                    cwd=source_root,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=service_environment,
                )
                if initialization.returncode != 0:
                    detail = initialization.stderr.strip() or "datastore initialization failed without an error message"
                    raise RuntimeError(f"could not initialize isolated PiFire: {detail}")

                with (
                    control_stdout_path.open("wb") as control_stdout,
                    control_stderr_path.open("wb") as control_stderr,
                    web_stdout_path.open("wb") as web_stdout,
                    web_stderr_path.open("wb") as web_stderr,
                ):
                    control = subprocess.Popen(
                        (sys.executable, "control.py"),
                        cwd=source_root,
                        env=service_environment,
                        stdout=control_stdout,
                        stderr=control_stderr,
                    )
                    processes.append(control)
                    web = subprocess.Popen(
                        (
                            sys.executable,
                            "-m",
                            "gunicorn",
                            "-k",
                            "gthread",
                            "--threads",
                            "25",
                            "-b",
                            f"127.0.0.1:{backend_port}",
                            "-w",
                            "1",
                            "app:app",
                        ),
                        cwd=source_root,
                        env=service_environment,
                        stdout=web_stdout,
                        stderr=web_stderr,
                    )
                    processes.append(web)

                    deadline = time.monotonic() + 60
                    while True:
                        if control.poll() is not None:
                            control_stderr.flush()
                            raise RuntimeError(
                                "isolated PiFire controller exited before readiness: "
                                f"{_runtime_failure_detail(control_stderr_path)}"
                            )
                        if web.poll() is not None:
                            web_stderr.flush()
                            raise RuntimeError(
                                "isolated PiFire backend exited before readiness: "
                                f"{_runtime_failure_detail(web_stderr_path)}"
                            )
                        try:
                            with urllib.request.urlopen(
                                f"http://127.0.0.1:{backend_port}/api/get/mode",
                                timeout=1,
                            ) as response:
                                if response.status == 200:
                                    break
                        except OSError:
                            pass
                        if time.monotonic() >= deadline:
                            web_stderr.flush()
                            raise RuntimeError(
                                "isolated PiFire backend did not become ready: "
                                f"{_runtime_failure_detail(web_stderr_path)}"
                            )
                        time.sleep(0.25)

                    yield
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                for process in reversed(processes):
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        _ = process.wait(timeout=5)
    finally:
        for name, value in prior_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _verify_bookmark(
    *,
    root: Path,
    bookmark: str,
    artifact_root: Path,
    run_command: _RunCommand = subprocess.run,
) -> GateEvidence:
    bookmark_revision = resolve_bookmark_revision(root, bookmark)
    current_revision = _resolve_current_revision(root)
    if bookmark_revision != current_revision:
        raise RuntimeError(
            f"local bookmark {bookmark!r} does not equal the current revision: "
            f"{bookmark_revision} != {current_revision}"
        )
    with _isolated_live_pifire(root):
        return run_gate(
            root=root,
            expected_revision=bookmark_revision,
            resolve_revision=lambda: _resolve_current_revision(root),
            artifact_root=artifact_root,
            run_command=run_command,
        )


def push_verified_bookmark(
    *,
    root: Path,
    bookmark: str,
    artifact_root: Path,
    run_command: _RunCommand = subprocess.run,
    resolve_operation: Callable[[], str] | None = None,
) -> GateEvidence:
    """Gate and push the one authorized bookmark without reusing evidence."""

    if bookmark != _VERIFIED_BOOKMARK:
        raise ValueError(f"push is restricted to bookmark {_VERIFIED_BOOKMARK!r}")

    bookmark_revision = resolve_bookmark_revision(root, bookmark)
    current_revision = _resolve_current_revision(root)
    if bookmark_revision != current_revision:
        raise RuntimeError(
            f"local bookmark {bookmark!r} does not equal the current revision: "
            f"{bookmark_revision} != {current_revision}"
        )

    with _artifact_lock(artifact_root):
        with _isolated_live_pifire(root):
            evidence = _run_gate_with_lock_held(
                root=root,
                expected_revision=bookmark_revision,
                resolve_revision=lambda: _resolve_current_revision(root),
                artifact_root=artifact_root,
                run_command=run_command,
            )
        if evidence.status != "passed":
            return evidence

        operation_id = (
            resolve_current_operation_id(root)
            if resolve_operation is None
            else resolve_operation()
        )
        if _FULL_OPERATION_ID.fullmatch(operation_id) is None:
            raise RuntimeError(
                "current operation did not resolve to one full 128-character lowercase hexadecimal ID"
            )
        post_gate_current = resolve_bookmark_revision(
            root,
            "@",
            operation_id=operation_id,
        )
        post_gate_bookmark = resolve_bookmark_revision(
            root,
            bookmark,
            operation_id=operation_id,
        )
        if post_gate_bookmark != evidence.revision or post_gate_current != evidence.revision:
            raise RuntimeError(
                "local bookmark or current revision changed after the gate: "
                f"evidence={evidence.revision}, bookmark={post_gate_bookmark}, current={post_gate_current}"
            )

        push_argv = (
            "jj",
            "--at-operation",
            operation_id,
            "git",
            "push",
            "-b",
            bookmark,
        )
        completed = run_command(
            push_argv,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = getattr(completed, "stderr", "")
            detail = detail.strip() if isinstance(detail, str) else ""
            raise RuntimeError(f"push failed: {detail or 'jj exited without an error message'}")

        remote_revision = resolve_bookmark_revision(root, f"{bookmark}@origin")
        if remote_revision != evidence.revision:
            raise RuntimeError(
                f"remote-tracking bookmark {bookmark!r} does not equal the evidence revision after push: "
                f"{remote_revision} != {evidence.revision}"
            )
        _validate_durable_pass_evidence(
            evidence=evidence,
            revision_dir=artifact_root / evidence.revision,
        )
        return evidence


@dataclass(frozen=True, slots=True)
class _Arguments:
    operation: Literal["verify", "verify-bookmark", "push", "verify-ci"]
    expected_revision: str | None
    bookmark: str | None
    artifact_root: Path


def _parse_args(argv: list[str] | None = None) -> _Arguments:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    verify = subparsers.add_parser("verify", help="run the exact-revision integration gate")
    _ = verify.add_argument("--expected-revision", required=True)
    _ = verify.add_argument("--artifact-root", required=True, type=Path)

    verify_bookmark = subparsers.add_parser(
        "verify-bookmark",
        help="gate the revision named by a local bookmark",
    )
    _ = verify_bookmark.add_argument("--bookmark", required=True)
    _ = verify_bookmark.add_argument("--artifact-root", type=Path, default=_DEFAULT_ARTIFACT_ROOT)

    push = subparsers.add_parser("push", help="gate and push the verified bookmark")
    _ = push.add_argument("--bookmark", required=True)
    _ = push.add_argument("--artifact-root", type=Path, default=_DEFAULT_ARTIFACT_ROOT)

    verify_ci = subparsers.add_parser("verify-ci", help="run the gate for the exact GitHub Actions revision")
    _ = verify_ci.add_argument("--expected-revision", required=True)
    _ = verify_ci.add_argument("--artifact-root", type=Path, default=_DEFAULT_ARTIFACT_ROOT)

    values = cast(dict[str, object], vars(parser.parse_args(argv)))
    operation = values["operation"]
    expected_revision = values.get("expected_revision")
    bookmark = values.get("bookmark")
    artifact_root = values.get("artifact_root")
    if (
        operation not in {"verify", "verify-bookmark", "push", "verify-ci"}
        or (expected_revision is not None and not isinstance(expected_revision, str))
        or (bookmark is not None and not isinstance(bookmark, str))
        or not isinstance(artifact_root, Path)
    ):
        parser.error("invalid exact-revision gate arguments")
    return _Arguments(
        operation=cast(Literal["verify", "verify-bookmark", "push", "verify-ci"], operation),
        expected_revision=expected_revision,
        bookmark=bookmark,
        artifact_root=artifact_root,
    )


def _validate_ci_environment(expected_revision: str) -> None:
    if os.environ.get("CI") != "true":
        raise ValueError("verify-ci requires CI=true")
    github_revision = os.environ.get("GITHUB_SHA")
    if github_revision is None:
        raise ValueError("verify-ci requires GITHUB_SHA")
    if _FULL_REVISION.fullmatch(github_revision) is None:
        raise ValueError("GITHUB_SHA must be a full 40-character lowercase hexadecimal revision")
    if github_revision != expected_revision:
        raise ValueError("GITHUB_SHA must exactly match --expected-revision")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path.cwd()
    try:
        if args.operation == "verify-ci":
            assert args.expected_revision is not None
            _validate_ci_environment(args.expected_revision)
            evidence = run_gate(
                root=root,
                expected_revision=args.expected_revision,
                artifact_root=args.artifact_root,
                resolve_revision=lambda: _resolve_current_revision(root),
            )
        elif args.operation == "verify":
            assert args.expected_revision is not None
            evidence = run_gate(
                root=root,
                expected_revision=args.expected_revision,
                artifact_root=args.artifact_root,
                resolve_revision=lambda: _resolve_current_revision(root),
            )
        elif args.operation == "verify-bookmark":
            assert args.bookmark is not None
            evidence = _verify_bookmark(
                root=root,
                bookmark=args.bookmark,
                artifact_root=args.artifact_root,
            )
        else:
            assert args.bookmark is not None
            evidence = push_verified_bookmark(
                root=root,
                bookmark=args.bookmark,
                artifact_root=args.artifact_root,
            )
    except ValueError as error:
        print(f"exact-revision gate: {error}", file=sys.stderr)
        return 2
    except RuntimeError as error:
        print(f"exact-revision gate: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"exact-revision gate: could not write artifacts: {error}", file=sys.stderr)
        return 1

    evidence_path = args.artifact_root / evidence.revision / "evidence.json"
    if evidence.status == "passed":
        print(f"Exact-revision gate passed; evidence: {evidence_path}")
        return 0
    print(f"Exact-revision gate failed; evidence: {evidence_path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
