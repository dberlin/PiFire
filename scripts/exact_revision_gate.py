#!/usr/bin/env python3
"""Run the complete integration gate against one unchanged revision."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
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
    """Durable result and logs for one mandatory command."""

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

    schema_version: Literal[1]
    revision: str
    status: Literal["passed", "failed"]
    commands: tuple[CommandEvidence, ...]


class _CompletedCommand(Protocol):
    returncode: int


_RunCommand = Callable[..., _CompletedCommand]
_ResolveRevision = Callable[[], str]

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
    commands: list[CommandEvidence],
    evidence_path: Path,
) -> GateEvidence:
    evidence = GateEvidence(
        schema_version=1,
        revision=expected_revision,
        status="failed",
        commands=tuple(commands),
    )
    _write_evidence(evidence_path, evidence)
    return evidence


def _invalid_pass_evidence_index(
    commands: list[CommandEvidence],
    revision_dir: Path,
) -> int | None:
    if len(commands) != len(_REQUIRED_COMMANDS):
        return 0

    for index, (required, evidence) in enumerate(
        zip(_REQUIRED_COMMANDS, commands, strict=True),
        start=1,
    ):
        stdout_name = f"{index:02d}-{required.name}.stdout.log"
        stderr_name = f"{index:02d}-{required.name}.stderr.log"
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
            return index - 1

        try:
            stdout_sha256 = _sha256(revision_dir / stdout_name)
            stderr_sha256 = _sha256(revision_dir / stderr_name)
        except OSError:
            return index - 1
        if (
            stdout_sha256 != evidence.stdout_sha256
            or stderr_sha256 != evidence.stderr_sha256
        ):
            return index - 1

    return None


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
            completed = run_command(
                command.argv,
                cwd=root / command.cwd,
                check=False,
                stdout=cast(IO[bytes], stdout_file),
                stderr=cast(IO[bytes], stderr_file),
            )
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
    commands: tuple[GateCommand, ...],
    run_command: _RunCommand,
) -> GateEvidence:
    evidence_path = revision_dir / "evidence.json"
    command_evidence = [_not_run(command) for command in _REQUIRED_COMMANDS]

    if resolved_revision != expected_revision:
        command_evidence[0] = replace(command_evidence[0], status="revision_changed")
        return _failed_evidence(
            expected_revision=revision_dir.name,
            commands=command_evidence,
            evidence_path=evidence_path,
        )

    attempt_evidence = GateEvidence(
        schema_version=1,
        revision=expected_revision,
        status="failed",
        commands=tuple(command_evidence),
    )
    _write_evidence(evidence_path, attempt_evidence)

    if commands != _REQUIRED_COMMANDS:
        return attempt_evidence

    for index, command in enumerate(commands, start=1):
        if index > 1 and not _revision_is_expected(resolve_revision, expected_revision):
            command_evidence[index - 1] = replace(command_evidence[index - 1], status="revision_changed")
            return _failed_evidence(
                expected_revision=expected_revision,
                commands=command_evidence,
                evidence_path=evidence_path,
            )

        result = _run_one_command(
            root=root,
            revision_dir=revision_dir,
            index=index,
            command=command,
            run_command=run_command,
        )
        command_evidence[index - 1] = result

        if not _revision_is_expected(resolve_revision, expected_revision):
            command_evidence[index - 1] = replace(result, status="revision_changed")
            return _failed_evidence(
                expected_revision=expected_revision,
                commands=command_evidence,
                evidence_path=evidence_path,
            )
        if result.status != "passed":
            return _failed_evidence(
                expected_revision=expected_revision,
                commands=command_evidence,
                evidence_path=evidence_path,
            )

    if not _revision_is_expected(resolve_revision, expected_revision):
        command_evidence[-1] = replace(command_evidence[-1], status="revision_changed")
        return _failed_evidence(
            expected_revision=expected_revision,
            commands=command_evidence,
            evidence_path=evidence_path,
        )

    invalid_index = _invalid_pass_evidence_index(command_evidence, revision_dir)
    if invalid_index is not None:
        command_evidence[invalid_index] = replace(
            command_evidence[invalid_index],
            status="failed",
        )
        return _failed_evidence(
            expected_revision=expected_revision,
            commands=command_evidence,
            evidence_path=evidence_path,
        )

    evidence = GateEvidence(
        schema_version=1,
        revision=expected_revision,
        status="passed",
        commands=tuple(command_evidence),
    )
    _write_evidence(evidence_path, evidence)
    return evidence


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

    artifact_root.mkdir(parents=True, exist_ok=True)
    with (artifact_root / ".gate.lock").open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
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
            commands=commands,
            run_command=run_command,
        )


def _resolve_current_revision(root: Path) -> str:
    completed = subprocess.run(
        (
            "jj",
            "--no-pager",
            "log",
            "--no-graph",
            "-r",
            "@",
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
        raise RuntimeError(f"could not resolve the current revision: {detail}")
    return completed.stdout.strip()


@dataclass(frozen=True, slots=True)
class _VerifyArguments:
    expected_revision: str
    artifact_root: Path


def _parse_args(argv: list[str] | None = None) -> _VerifyArguments:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    verify = subparsers.add_parser("verify", help="run the exact-revision integration gate")
    _ = verify.add_argument("--expected-revision", required=True)
    _ = verify.add_argument("--artifact-root", required=True, type=Path)
    values = cast(dict[str, object], vars(parser.parse_args(argv)))
    expected_revision = values.get("expected_revision")
    artifact_root = values.get("artifact_root")
    if not isinstance(expected_revision, str) or not isinstance(artifact_root, Path):
        parser.error("verify requires --expected-revision and --artifact-root")
    return _VerifyArguments(
        expected_revision=expected_revision,
        artifact_root=artifact_root,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path.cwd()
    try:
        evidence = run_gate(
            root=root,
            expected_revision=args.expected_revision,
            artifact_root=args.artifact_root,
            resolve_revision=lambda: _resolve_current_revision(root),
        )
    except ValueError as error:
        print(f"exact-revision gate: {error}", file=sys.stderr)
        return 2
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
