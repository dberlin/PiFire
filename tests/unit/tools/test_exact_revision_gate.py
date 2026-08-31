"""Unit contracts for the exact-revision integration gate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import shutil
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from typing import IO, Literal, cast, final, get_type_hints, override

import pytest
from scripts import exact_revision_gate as gate_module
from scripts.exact_revision_gate import (
    CommandEvidence,
    GateCommand,
    GateEvidence,
    required_commands,
    run_gate,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPOSITORY_ROOT / "scripts" / "exact_revision_gate.py"
_REVISION = "0123456789abcdef0123456789abcdef01234567"
_OTHER_REVISION = "f" * 40


@final
class _RevisionResolver:
    def __init__(
        self,
        *revisions: str | BaseException,
        events: list[str] | None = None,
    ) -> None:
        self._revisions: Iterator[str | BaseException] = iter(revisions)
        self._events = events
        self.calls: int = 0

    def __call__(self) -> str:
        self.calls += 1
        if self._events is not None:
            self._events.append("resolve")
        revision = next(self._revisions)
        if isinstance(revision, BaseException):
            raise revision
        return revision


class _CommandRunner:
    def __init__(
        self,
        *results: tuple[int, bytes, bytes] | BaseException,
        events: list[str] | None = None,
    ) -> None:
        self._results: Iterator[tuple[int, bytes, bytes] | BaseException] = iter(results)
        self._events = events
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        stdout: IO[bytes],
        stderr: IO[bytes],
    ) -> subprocess.CompletedProcess[bytes]:
        assert check is False
        if self._events is not None:
            command = next(command for command in required_commands() if command.argv == argv)
            self._events.append(f"run:{command.name}")
        self.calls.append((argv, cwd))
        result = next(self._results)
        if isinstance(result, BaseException):
            _ = stdout.write(b"partial stdout before interruption\n")
            _ = stderr.write(b"partial stderr before interruption\n")
            raise result
        exit_code, stdout_bytes, stderr_bytes = result
        _ = stdout.write(stdout_bytes)
        _ = stderr.write(stderr_bytes)
        return subprocess.CompletedProcess[bytes](argv, exit_code)


def _success_results() -> tuple[tuple[int, bytes, bytes], ...]:
    return tuple((0, f"stdout-{index}\n".encode(), f"stderr-{index}\n".encode()) for index in range(5))


def _run_gate(
    tmp_path: Path,
    *,
    resolver: _RevisionResolver,
    runner: _CommandRunner,
) -> tuple[GateEvidence, Path]:
    artifact_root = tmp_path / "artifacts"
    evidence = run_gate(
        root=_REPOSITORY_ROOT,
        expected_revision=_REVISION,
        artifact_root=artifact_root,
        resolve_revision=resolver,
        run_command=runner,
    )
    return evidence, artifact_root / _REVISION


def _read_json(path: Path) -> dict[str, object]:
    value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_required_commands_match_release_gate() -> None:
    assert required_commands() == (
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


def test_in_tree_artifact_root_is_ignored_and_supports_a_complete_attempt(tmp_path: Path) -> None:
    ignore_rules = (_REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.artifacts/" in ignore_rules
    artifact_root = (
        _REPOSITORY_ROOT
        / ".artifacts"
        / "exact-revision"
        / f"unit-{os.getpid()}-{tmp_path.name}"
    )
    resolver = _RevisionResolver(*([_REVISION] * 11))
    runner = _CommandRunner(*_success_results())

    try:
        evidence = run_gate(
            root=_REPOSITORY_ROOT,
            expected_revision=_REVISION,
            artifact_root=artifact_root,
            resolve_revision=resolver,
            run_command=runner,
        )
    finally:
        shutil.rmtree(artifact_root, ignore_errors=True)

    assert evidence.status == "passed"
    assert resolver.calls == 11
    assert len(runner.calls) == 5


def test_evidence_records_have_exact_typed_immutable_fields() -> None:
    command_status = Literal[
        "passed",
        "failed",
        "interrupted",
        "revision_changed",
        "not_run",
    ]
    assert get_type_hints(GateCommand) == {
        "name": str,
        "argv": tuple[str, ...],
        "cwd": str,
    }
    assert [field.name for field in fields(GateCommand)] == ["name", "argv", "cwd"]
    assert get_type_hints(CommandEvidence) == {
        "name": str,
        "argv": tuple[str, ...],
        "cwd": str,
        "exit_code": int | None,
        "status": command_status,
        "stdout_log": str | None,
        "stderr_log": str | None,
        "stdout_sha256": str | None,
        "stderr_sha256": str | None,
    }
    assert [field.name for field in fields(CommandEvidence)] == [
        "name",
        "argv",
        "cwd",
        "exit_code",
        "status",
        "stdout_log",
        "stderr_log",
        "stdout_sha256",
        "stderr_sha256",
    ]
    assert get_type_hints(GateEvidence) == {
        "schema_version": Literal[1],
        "revision": str,
        "status": Literal["passed", "failed"],
        "commands": tuple[CommandEvidence, ...],
    }
    assert [field.name for field in fields(GateEvidence)] == [
        "schema_version",
        "revision",
        "status",
        "commands",
    ]

    command = required_commands()[0]
    command_evidence = CommandEvidence(
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
    gate_evidence = GateEvidence(
        schema_version=1,
        revision=_REVISION,
        status="failed",
        commands=(command_evidence,),
    )
    for record, attribute in (
        (command, "name"),
        (command_evidence, "status"),
        (gate_evidence, "status"),
    ):
        assert not hasattr(record, "__dict__")
        with pytest.raises(FrozenInstanceError):
            record.__setattr__(attribute, "changed")


def test_success_checks_revision_around_every_command_and_at_end(tmp_path: Path) -> None:
    events: list[str] = []
    resolver = _RevisionResolver(*([_REVISION] * 11), events=events)
    runner = _CommandRunner(*_success_results(), events=events)

    evidence, revision_dir = _run_gate(tmp_path, resolver=resolver, runner=runner)

    assert evidence.status == "passed"
    assert events == [
        "resolve",
        "run:rebuild-acados",
        "resolve",
        "resolve",
        "run:python-default",
        "resolve",
        "resolve",
        "run:python-slow",
        "resolve",
        "resolve",
        "run:bun-workspaces",
        "resolve",
        "resolve",
        "run:web-react-e2e",
        "resolve",
        "resolve",
    ]
    assert runner.calls == [
        (command.argv, _REPOSITORY_ROOT / command.cwd)
        for command in required_commands()
    ]
    assert [command.status for command in evidence.commands] == ["passed"] * 5
    assert _read_json(revision_dir / "evidence.json")["status"] == "passed"


def test_success_evidence_is_not_published_before_the_final_revision_check(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    evidence_path = artifact_root / _REVISION / "evidence.json"
    resolver = _RevisionResolver(*([_REVISION] * 11))

    class InspectingRunner(_CommandRunner):
        @override
        def __call__(
            self,
            argv: tuple[str, ...],
            *,
            cwd: Path,
            check: bool,
            stdout: IO[bytes],
            stderr: IO[bytes],
        ) -> subprocess.CompletedProcess[bytes]:
            assert not evidence_path.exists() or _read_json(evidence_path)["status"] != "passed"
            return super().__call__(
                argv,
                cwd=cwd,
                check=check,
                stdout=stdout,
                stderr=stderr,
            )

    runner = InspectingRunner(*_success_results())
    evidence = run_gate(
        root=_REPOSITORY_ROOT,
        expected_revision=_REVISION,
        artifact_root=artifact_root,
        resolve_revision=resolver,
        run_command=runner,
    )

    assert evidence.status == "passed"
    assert evidence_path.exists()


def test_prior_success_is_replaced_before_a_new_attempt_runs(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    evidence_path = artifact_root / _REVISION / "evidence.json"
    evidence_path.parent.mkdir(parents=True)
    _ = evidence_path.write_text('{"status": "passed"}\n', encoding="utf-8")
    resolver = _RevisionResolver(*([_REVISION] * 11))

    class InspectingRunner(_CommandRunner):
        @override
        def __call__(
            self,
            argv: tuple[str, ...],
            *,
            cwd: Path,
            check: bool,
            stdout: IO[bytes],
            stderr: IO[bytes],
        ) -> subprocess.CompletedProcess[bytes]:
            assert _read_json(evidence_path)["status"] == "failed"
            return super().__call__(
                argv,
                cwd=cwd,
                check=check,
                stdout=stdout,
                stderr=stderr,
            )

    runner = InspectingRunner(*_success_results())
    evidence = run_gate(
        root=_REPOSITORY_ROOT,
        expected_revision=_REVISION,
        artifact_root=artifact_root,
        resolve_revision=resolver,
        run_command=runner,
    )

    assert evidence.status == "passed"
    assert _read_json(evidence_path)["status"] == "passed"


def test_same_revision_attempts_are_serialized_before_logs_are_touched(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    first_command_started = threading.Event()
    release_first_command = threading.Event()
    second_command_started = threading.Event()
    errors: list[BaseException] = []

    def first_runner(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        stdout: IO[bytes],
        stderr: IO[bytes],
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd
        assert check is False
        if not first_command_started.is_set():
            first_command_started.set()
            assert release_first_command.wait(timeout=2)
        _ = stdout.write(b"first attempt\n")
        _ = stderr.write(b"")
        return subprocess.CompletedProcess[bytes](argv, 0)

    def second_resolver() -> str:
        return _REVISION

    def second_runner(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        stdout: IO[bytes],
        stderr: IO[bytes],
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd
        assert check is False
        second_command_started.set()
        _ = stdout.write(b"second attempt\n")
        _ = stderr.write(b"")
        return subprocess.CompletedProcess[bytes](argv, 0)

    def invoke(
        runner: Callable[..., subprocess.CompletedProcess[bytes]],
        resolver: Callable[[], str],
    ) -> None:
        try:
            _ = run_gate(
                root=_REPOSITORY_ROOT,
                expected_revision=_REVISION,
                artifact_root=artifact_root,
                resolve_revision=resolver,
                run_command=runner,
            )
        except BaseException as error:
            errors.append(error)

    first_thread = threading.Thread(
        target=invoke,
        args=(first_runner, _RevisionResolver(*([_REVISION] * 11))),
    )
    second_thread = threading.Thread(target=invoke, args=(second_runner, second_resolver))
    first_thread.start()
    assert first_command_started.wait(timeout=2)
    second_thread.start()
    second_entered_while_first_running = second_command_started.wait(timeout=0.1)
    release_first_command.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert not errors
    assert not second_entered_while_first_running
    assert second_command_started.is_set()
    revision_dir = artifact_root / _REVISION
    evidence = _read_json(revision_dir / "evidence.json")
    assert evidence["status"] == "passed"
    stdout_log = revision_dir / "01-rebuild-acados.stdout.log"
    assert stdout_log.read_bytes() == b"second attempt\n"


def test_success_writes_separate_logs_hashes_and_atomic_json(tmp_path: Path) -> None:
    resolver = _RevisionResolver(*([_REVISION] * 11))
    runner = _CommandRunner(*_success_results())

    evidence, revision_dir = _run_gate(tmp_path, resolver=resolver, runner=runner)

    payload = _read_json(revision_dir / "evidence.json")
    assert payload == {
        "schema_version": 1,
        "revision": _REVISION,
        "status": "passed",
        "commands": [
            {
                "name": command.name,
                "argv": list(command.argv),
                "cwd": command.cwd,
                "exit_code": 0,
                "status": "passed",
                "stdout_log": f"{index:02d}-{command.name}.stdout.log",
                "stderr_log": f"{index:02d}-{command.name}.stderr.log",
                "stdout_sha256": hashlib.sha256(f"stdout-{index - 1}\n".encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(f"stderr-{index - 1}\n".encode()).hexdigest(),
            }
            for index, command in enumerate(required_commands(), start=1)
        ],
    }
    for command in evidence.commands:
        assert command.stdout_log is not None
        assert command.stderr_log is not None
        assert hashlib.sha256((revision_dir / command.stdout_log).read_bytes()).hexdigest() == command.stdout_sha256
        assert hashlib.sha256((revision_dir / command.stderr_log).read_bytes()).hexdigest() == command.stderr_sha256
    assert not list(revision_dir.glob(".evidence.json.*"))


def test_success_revalidates_ordered_command_entries_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_run_one_command = gate_module._run_one_command

    def corrupting_run_one_command(**arguments: object) -> CommandEvidence:
        result = original_run_one_command(**arguments)
        if arguments["index"] == 2:
            return replace(result, name="out-of-order-command")
        return result

    monkeypatch.setattr(gate_module, "_run_one_command", corrupting_run_one_command)
    resolver = _RevisionResolver(*([_REVISION] * 11))
    runner = _CommandRunner(*_success_results())

    evidence, revision_dir = _run_gate(tmp_path, resolver=resolver, runner=runner)

    assert evidence.status == "failed"
    assert [command.status for command in evidence.commands] == [
        "passed",
        "failed",
        "passed",
        "passed",
        "passed",
    ]
    assert _read_json(revision_dir / "evidence.json")["status"] == "failed"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("mutation", ["remove", "change"])
def test_success_revalidates_both_logs_and_hashes_before_publication(
    tmp_path: Path,
    stream: str,
    mutation: str,
) -> None:
    artifact_root = tmp_path / "artifacts"
    revision_dir = artifact_root / _REVISION
    delegate = _CommandRunner(*_success_results())

    def mutating_runner(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        stdout: IO[bytes],
        stderr: IO[bytes],
    ) -> subprocess.CompletedProcess[bytes]:
        if len(delegate.calls) == 1:
            earlier_log = revision_dir / f"01-rebuild-acados.{stream}.log"
            if mutation == "remove":
                earlier_log.unlink()
            else:
                _ = earlier_log.write_bytes(b"tampered after command completion\n")
        return delegate(
            argv,
            cwd=cwd,
            check=check,
            stdout=stdout,
            stderr=stderr,
        )

    evidence = run_gate(
        root=_REPOSITORY_ROOT,
        expected_revision=_REVISION,
        artifact_root=artifact_root,
        resolve_revision=_RevisionResolver(*([_REVISION] * 11)),
        run_command=mutating_runner,
    )

    assert len(delegate.calls) == 5
    assert evidence.status == "failed"
    assert [command.status for command in evidence.commands] == [
        "failed",
        "passed",
        "passed",
        "passed",
        "passed",
    ]
    assert _read_json(revision_dir / "evidence.json")["status"] == "failed"

def test_evidence_json_replacement_is_atomic_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "artifacts"
    evidence_path = artifact_root / _REVISION / "evidence.json"
    evidence_path.parent.mkdir(parents=True)
    prior_evidence = b'{\"revision\": \"prior\", \"status\": \"passed\"}\n'
    _ = evidence_path.write_bytes(prior_evidence)
    resolver = _RevisionResolver(_REVISION)
    runner = _CommandRunner(*_success_results())

    def fail_replace(*_arguments: object) -> None:
        raise OSError("injected atomic replacement failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected atomic replacement failure"):
        _ = run_gate(
            root=_REPOSITORY_ROOT,
            expected_revision=_REVISION,
            artifact_root=artifact_root,
            resolve_revision=resolver,
            run_command=runner,
        )

    assert evidence_path.read_bytes() == prior_evidence
    assert not list(evidence_path.parent.glob(".evidence.json.*"))
    assert not runner.calls


def test_nonzero_exit_fails_and_marks_remaining_commands_not_run(tmp_path: Path) -> None:
    resolver = _RevisionResolver(_REVISION, _REVISION)
    runner = _CommandRunner((7, b"partial stdout\n", b"failure stderr\n"))

    evidence, revision_dir = _run_gate(tmp_path, resolver=resolver, runner=runner)

    assert evidence.status == "failed"
    assert [command.status for command in evidence.commands] == ["failed"] + ["not_run"] * 4
    failed = evidence.commands[0]
    assert failed.exit_code == 7
    assert failed.stdout_log is not None
    assert failed.stderr_log is not None
    assert (revision_dir / failed.stdout_log).read_bytes() == b"partial stdout\n"
    assert (revision_dir / failed.stderr_log).read_bytes() == b"failure stderr\n"
    assert _read_json(revision_dir / "evidence.json")["status"] == "failed"


@pytest.mark.parametrize(
    "raised",
    [
        pytest.param(KeyboardInterrupt(), id="keyboard-interrupt"),
        pytest.param(subprocess.TimeoutExpired(("command",), timeout=1), id="timeout"),
        pytest.param(RuntimeError("runner broke"), id="exception"),
        pytest.param(SystemExit(0), id="system-exit"),
    ],
)
def test_interruption_or_exception_fails_with_logs_and_remaining_not_run(
    tmp_path: Path,
    raised: BaseException,
) -> None:
    resolver = _RevisionResolver(_REVISION, _REVISION)
    runner = _CommandRunner(raised)

    evidence, revision_dir = _run_gate(tmp_path, resolver=resolver, runner=runner)

    assert evidence.status == "failed"
    assert [command.status for command in evidence.commands] == ["interrupted"] + ["not_run"] * 4
    interrupted = evidence.commands[0]
    assert interrupted.exit_code is None
    assert interrupted.stdout_log is not None
    assert interrupted.stderr_log is not None
    stdout_bytes = b"partial stdout before interruption\n"
    stderr_bytes = b"partial stderr before interruption\n"
    assert (revision_dir / interrupted.stdout_log).read_bytes() == stdout_bytes
    assert (revision_dir / interrupted.stderr_log).read_bytes() == stderr_bytes
    assert interrupted.stdout_sha256 == hashlib.sha256(stdout_bytes).hexdigest()
    assert interrupted.stderr_sha256 == hashlib.sha256(stderr_bytes).hexdigest()
    assert _read_json(revision_dir / "evidence.json")["status"] == "failed"


def test_initial_revision_mismatch_preserves_expected_revision_evidence(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    expected_evidence_path = artifact_root / _REVISION / "evidence.json"
    expected_evidence_path.parent.mkdir(parents=True)
    prior_evidence = b'{\"revision\": \"prior\", \"status\": \"passed\"}\n'
    _ = expected_evidence_path.write_bytes(prior_evidence)
    resolver = _RevisionResolver(_OTHER_REVISION)
    runner = _CommandRunner(*_success_results())

    evidence = run_gate(
        root=_REPOSITORY_ROOT,
        expected_revision=_REVISION,
        artifact_root=artifact_root,
        resolve_revision=resolver,
        run_command=runner,
    )

    assert evidence.revision == _OTHER_REVISION
    assert evidence.status == "failed"
    assert [command.status for command in evidence.commands] == [
        "revision_changed",
        "not_run",
        "not_run",
        "not_run",
        "not_run",
    ]
    assert expected_evidence_path.read_bytes() == prior_evidence
    assert _read_json(artifact_root / _OTHER_REVISION / "evidence.json")["status"] == "failed"
    assert not runner.calls


@pytest.mark.parametrize("failing_check", range(1, 12))
def test_revision_drift_fails_at_every_exact_check(
    tmp_path: Path,
    failing_check: int,
) -> None:
    revisions = [_REVISION] * 11
    revisions[failing_check - 1] = _OTHER_REVISION
    resolver = _RevisionResolver(*revisions)
    runner = _CommandRunner(*_success_results())

    evidence, revision_dir = _run_gate(tmp_path, resolver=resolver, runner=runner)

    affected_command = min((failing_check - 1) // 2, 4)
    expected_statuses = (
        ["passed"] * affected_command
        + ["revision_changed"]
        + ["not_run"] * (4 - affected_command)
    )
    assert resolver.calls == failing_check
    assert len(runner.calls) == failing_check // 2
    assert evidence.status == "failed"
    assert [command.status for command in evidence.commands] == expected_statuses
    artifact_revision_dir = revision_dir.parent / evidence.revision
    assert _read_json(artifact_revision_dir / "evidence.json")["status"] == "failed"


@pytest.mark.parametrize(
    "raised",
    [
        pytest.param(RuntimeError("cannot resolve revision"), id="exception"),
        pytest.param(SystemExit(0), id="system-exit"),
    ],
)
def test_revision_resolution_exception_fails_closed(
    tmp_path: Path,
    raised: BaseException,
) -> None:
    resolver = _RevisionResolver(raised)
    runner = _CommandRunner(*_success_results())

    evidence, _ = _run_gate(tmp_path, resolver=resolver, runner=runner)

    assert evidence.status == "failed"
    assert not runner.calls
    assert [command.status for command in evidence.commands] == ["revision_changed"] + ["not_run"] * 4


@pytest.mark.parametrize(
    "invalid_revision",
    [
        pytest.param("abc123", id="abbreviated"),
        pytest.param("g" * 40, id="non-hex"),
        pytest.param("a" * 39, id="too-short"),
        pytest.param("a" * 41, id="too-long"),
        pytest.param("../" + "a" * 40, id="path-traversal"),
        pytest.param("A" * 40, id="non-canonical-case"),
    ],
)
def test_invalid_expected_revision_is_rejected_before_side_effects(
    tmp_path: Path,
    invalid_revision: str,
) -> None:
    resolver = _RevisionResolver(_REVISION)
    runner = _CommandRunner(*_success_results())

    with pytest.raises(ValueError, match="full 40-character lowercase hexadecimal"):
        _ = run_gate(
            root=_REPOSITORY_ROOT,
            expected_revision=invalid_revision,
            artifact_root=tmp_path / "artifacts",
            resolve_revision=resolver,
            run_command=runner,
        )

    assert resolver.calls == 0
    assert not runner.calls
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize(
    "unsupported_args",
    [
        pytest.param(("push",), id="unsupported-command"),
        pytest.param(
            (
                "verify",
                "--expected-revision",
                _REVISION,
                "--artifact-root",
                "artifacts",
                "--skip",
            ),
            id="skip-flag",
        ),
        pytest.param(
            (
                "verify",
                "--expected-revision",
                _REVISION,
                "--artifact-root",
                "artifacts",
                "--bypass",
            ),
            id="bypass-flag",
        ),
    ],
)
def test_cli_exposes_only_verify_without_skip_or_bypass(unsupported_args: tuple[str, ...]) -> None:
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT), *unsupported_args],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr


def test_cli_reports_invalid_revision_without_running_the_gate(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "verify",
            "--expected-revision",
            "short",
            "--artifact-root",
            str(tmp_path / "artifacts"),
        ],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "full 40-character lowercase hexadecimal" in completed.stderr
    assert "Traceback" not in completed.stderr
    assert not (tmp_path / "artifacts").exists()


def test_cli_reports_artifact_io_error_without_traceback(tmp_path: Path) -> None:
    artifact_root = tmp_path / "not-a-directory"
    _ = artifact_root.write_text("blocking file\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "verify",
            "--expected-revision",
            _REVISION,
            "--artifact-root",
            str(artifact_root),
        ],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "exact-revision gate: could not write artifacts:" in completed.stderr
    assert "Traceback" not in completed.stderr
