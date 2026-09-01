"""Unit contracts for the exact-revision integration gate."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from typing import IO, Literal, cast, final, get_type_hints, override

import pytest

from scripts import exact_revision_gate as gate_module
from scripts.exact_revision_gate import (
    CommandEvidence,
    GateCommand,
    GateEvidence,
    preflight_commands,
    push_verified_bookmark,
    required_commands,
    resolve_bookmark_revision,
    resolve_current_operation_id,
    run_gate,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPOSITORY_ROOT / "scripts" / "exact_revision_gate.py"
_REVISION = "0123456789abcdef0123456789abcdef01234567"
_OTHER_REVISION = "f" * 40
_OPERATION_ID = "a" * 128
_SHORT_OPERATION_ID = "a" * 64


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
        self.environments: list[dict[str, str] | None] = []
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
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        self.environments.append(env)
        assert check is False
        if self._events is not None:
            commands = preflight_commands() + required_commands()
            command = next(command for command in commands if command.argv == argv)
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
    return tuple((0, f"stdout-{index}\n".encode(), f"stderr-{index}\n".encode()) for index in range(6))


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


def _assert_logged_evidence(
    command: CommandEvidence,
    revision_dir: Path,
    *,
    log_index: int,
    stdout_bytes: bytes,
    stderr_bytes: bytes,
) -> None:
    assert command.stdout_log == f"{log_index:02d}-{command.name}.stdout.log"
    assert command.stderr_log == f"{log_index:02d}-{command.name}.stderr.log"
    assert (revision_dir / command.stdout_log).read_bytes() == stdout_bytes
    assert (revision_dir / command.stderr_log).read_bytes() == stderr_bytes
    assert command.stdout_sha256 == hashlib.sha256(stdout_bytes).hexdigest()
    assert command.stderr_sha256 == hashlib.sha256(stderr_bytes).hexdigest()


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


def test_contract_preflight_is_separate_from_the_five_release_commands() -> None:
    assert preflight_commands() == (
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
    assert len(required_commands()) == 5


def test_agents_requires_schema_v2_preflight_evidence_to_authorize_push() -> None:
    rules = (_REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "Only schema v2 evidence with the separate contract preflight can authorize a push." in rules
    assert "Preserved schema v1 evidence is historical-only and cannot authorize a push." in rules


@pytest.mark.parametrize(
    "result,status",
    [
        pytest.param((7, b"preflight stdout\n", b"preflight failure\n"), "failed", id="failed"),
        pytest.param(KeyboardInterrupt(), "interrupted", id="interrupted"),
    ],
)
def test_failed_or_interrupted_preflight_preserves_evidence_and_leaves_release_not_run(
    tmp_path: Path,
    result: tuple[int, bytes, bytes] | BaseException,
    status: str,
) -> None:
    resolver = _RevisionResolver(_REVISION, _REVISION)
    runner = _CommandRunner(result)

    evidence, revision_dir = _run_gate(tmp_path, resolver=resolver, runner=runner)

    assert evidence.status == "failed"
    assert [command.status for command in evidence.preflight] == [status]
    assert [command.status for command in evidence.commands] == ["not_run"] * 5
    preflight = evidence.preflight[0]
    assert preflight.stdout_log == "00-contract-preflight.stdout.log"
    assert preflight.stderr_log == "00-contract-preflight.stderr.log"
    assert preflight.stdout_sha256 == hashlib.sha256((revision_dir / preflight.stdout_log).read_bytes()).hexdigest()
    assert preflight.stderr_sha256 == hashlib.sha256((revision_dir / preflight.stderr_log).read_bytes()).hexdigest()
    assert _read_json(revision_dir / "evidence.json")["preflight"] == [
        {
            "name": preflight.name,
            "argv": list(preflight.argv),
            "cwd": preflight.cwd,
            "exit_code": preflight.exit_code,
            "status": preflight.status,
            "stdout_log": preflight.stdout_log,
            "stderr_log": preflight.stderr_log,
            "stdout_sha256": preflight.stdout_sha256,
            "stderr_sha256": preflight.stderr_sha256,
        }
    ]


def test_revision_drift_during_preflight_fails_before_release_commands(tmp_path: Path) -> None:
    resolver = _RevisionResolver(_REVISION, _OTHER_REVISION)
    runner = _CommandRunner((0, b"preflight stdout\n", b""))

    evidence, _ = _run_gate(tmp_path, resolver=resolver, runner=runner)

    assert evidence.status == "failed"
    assert [command.status for command in evidence.preflight] == ["revision_changed"]
    assert [command.status for command in evidence.commands] == ["not_run"] * 5
    assert len(runner.calls) == 1


def test_in_tree_artifact_root_is_ignored_and_supports_a_complete_attempt(tmp_path: Path) -> None:
    ignore_rules = (_REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.artifacts/" in ignore_rules
    artifact_root = _REPOSITORY_ROOT / ".artifacts" / "exact-revision" / f"unit-{os.getpid()}-{tmp_path.name}"
    resolver = _RevisionResolver(*([_REVISION] * 13))
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
    assert resolver.calls == 13
    assert len(runner.calls) == 6


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
        "schema_version": Literal[2],
        "revision": str,
        "status": Literal["passed", "failed"],
        "preflight": tuple[CommandEvidence, ...],
        "commands": tuple[CommandEvidence, ...],
    }
    assert [field.name for field in fields(GateEvidence)] == [
        "schema_version",
        "revision",
        "status",
        "preflight",
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
        schema_version=2,
        revision=_REVISION,
        status="failed",
        preflight=(command_evidence,),
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
    resolver = _RevisionResolver(*([_REVISION] * 13), events=events)
    runner = _CommandRunner(*_success_results(), events=events)

    evidence, revision_dir = _run_gate(tmp_path, resolver=resolver, runner=runner)

    assert evidence.status == "passed"
    assert events == [
        "resolve",
        "run:contract-preflight",
        "resolve",
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
        (command.argv, _REPOSITORY_ROOT / command.cwd) for command in preflight_commands() + required_commands()
    ]
    assert [command.status for command in evidence.preflight] == ["passed"]
    assert [command.status for command in evidence.commands] == ["passed"] * 5
    assert _read_json(revision_dir / "evidence.json")["status"] == "passed"


def test_success_evidence_is_not_published_before_the_final_revision_check(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    evidence_path = artifact_root / _REVISION / "evidence.json"
    resolver = _RevisionResolver(*([_REVISION] * 13))

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
    resolver = _RevisionResolver(*([_REVISION] * 13))

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
        args=(first_runner, _RevisionResolver(*([_REVISION] * 13))),
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
    resolver = _RevisionResolver(*([_REVISION] * 13))
    runner = _CommandRunner(*_success_results())

    evidence, revision_dir = _run_gate(tmp_path, resolver=resolver, runner=runner)

    payload = _read_json(revision_dir / "evidence.json")
    assert payload == {
        "schema_version": 2,
        "revision": _REVISION,
        "status": "passed",
        "preflight": [
            {
                "name": "contract-preflight",
                "argv": list(preflight_commands()[0].argv),
                "cwd": ".",
                "exit_code": 0,
                "status": "passed",
                "stdout_log": "00-contract-preflight.stdout.log",
                "stderr_log": "00-contract-preflight.stderr.log",
                "stdout_sha256": hashlib.sha256(b"stdout-0\n").hexdigest(),
                "stderr_sha256": hashlib.sha256(b"stderr-0\n").hexdigest(),
            }
        ],
        "commands": [
            {
                "name": command.name,
                "argv": list(command.argv),
                "cwd": command.cwd,
                "exit_code": 0,
                "status": "passed",
                "stdout_log": f"{index:02d}-{command.name}.stdout.log",
                "stderr_log": f"{index:02d}-{command.name}.stderr.log",
                "stdout_sha256": hashlib.sha256(f"stdout-{index}\n".encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(f"stderr-{index}\n".encode()).hexdigest(),
            }
            for index, command in enumerate(required_commands(), start=1)
        ],
    }
    for command in evidence.preflight + evidence.commands:
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
    resolver = _RevisionResolver(*([_REVISION] * 13))
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
        if len(delegate.calls) == 2:
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
        resolve_revision=_RevisionResolver(*([_REVISION] * 13)),
        run_command=mutating_runner,
    )

    assert len(delegate.calls) == 6
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
    prior_evidence = b'{"revision": "prior", "status": "passed"}\n'
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


@pytest.mark.parametrize(
    "failure_kind,expected_status,expected_exit_code,expected_stdout,expected_stderr",
    [
        pytest.param(
            "nonzero",
            "failed",
            7,
            b"nonzero stdout\n",
            b"nonzero stderr\n",
            id="nonzero",
        ),
        pytest.param(
            "timeout",
            "interrupted",
            None,
            b"partial stdout before interruption\n",
            b"partial stderr before interruption\n",
            id="timeout",
        ),
        pytest.param(
            "keyboard-interrupt",
            "interrupted",
            None,
            b"partial stdout before interruption\n",
            b"partial stderr before interruption\n",
            id="keyboard-interrupt",
        ),
        pytest.param(
            "exception",
            "interrupted",
            None,
            b"partial stdout before interruption\n",
            b"partial stderr before interruption\n",
            id="exception",
        ),
    ],
)
@pytest.mark.parametrize(
    "failing_index",
    range(5),
    ids=[command.name for command in required_commands()],
)
def test_release_stage_failure_after_successful_preflight_fails_closed_at_every_index(
    tmp_path: Path,
    failure_kind: Literal["nonzero", "timeout", "keyboard-interrupt", "exception"],
    expected_status: Literal["failed", "interrupted"],
    expected_exit_code: int | None,
    expected_stdout: bytes,
    expected_stderr: bytes,
    failing_index: int,
) -> None:
    commands = required_commands()
    preflight_stdout = b"preflight stdout\n"
    preflight_stderr = b"preflight stderr\n"
    prior_results = tuple(
        (
            0,
            f"{command.name} stdout\n".encode(),
            f"{command.name} stderr\n".encode(),
        )
        for command in commands[:failing_index]
    )
    failed_result: tuple[int, bytes, bytes] | BaseException
    if failure_kind == "nonzero":
        failed_result = (7, expected_stdout, expected_stderr)
    elif failure_kind == "timeout":
        failed_result = subprocess.TimeoutExpired(commands[failing_index].argv, timeout=1)
    elif failure_kind == "keyboard-interrupt":
        failed_result = KeyboardInterrupt()
    else:
        failed_result = RuntimeError("runner broke")
    events: list[str] = []
    resolver = _RevisionResolver(*([_REVISION] * 13), events=events)
    runner = _CommandRunner(
        (0, preflight_stdout, preflight_stderr),
        *prior_results,
        failed_result,
        events=events,
    )

    evidence, revision_dir = _run_gate(tmp_path, resolver=resolver, runner=runner)

    assert evidence.status == "failed"
    assert [command.status for command in evidence.preflight] == ["passed"]
    assert [command.name for command in evidence.commands] == [command.name for command in commands]
    assert [command.status for command in evidence.commands] == (
        ["passed"] * failing_index + [expected_status] + ["not_run"] * (len(commands) - failing_index - 1)
    )
    assert runner.calls == [
        (command.argv, _REPOSITORY_ROOT / command.cwd)
        for command in preflight_commands() + commands[: failing_index + 1]
    ]
    expected_events = ["resolve", "run:contract-preflight", "resolve"]
    for command in commands[: failing_index + 1]:
        expected_events.extend(["resolve", f"run:{command.name}", "resolve"])
    assert events == expected_events

    preflight = evidence.preflight[0]
    assert preflight.exit_code == 0
    _assert_logged_evidence(
        preflight,
        revision_dir,
        log_index=0,
        stdout_bytes=preflight_stdout,
        stderr_bytes=preflight_stderr,
    )
    for release_index, passed in enumerate(evidence.commands[:failing_index]):
        assert passed.exit_code == 0
        _assert_logged_evidence(
            passed,
            revision_dir,
            log_index=release_index + 1,
            stdout_bytes=f"{passed.name} stdout\n".encode(),
            stderr_bytes=f"{passed.name} stderr\n".encode(),
        )

    failed = evidence.commands[failing_index]
    assert failed.exit_code == expected_exit_code
    _assert_logged_evidence(
        failed,
        revision_dir,
        log_index=failing_index + 1,
        stdout_bytes=expected_stdout,
        stderr_bytes=expected_stderr,
    )
    for not_run in evidence.commands[failing_index + 1 :]:
        assert (
            not_run.exit_code,
            not_run.stdout_log,
            not_run.stderr_log,
            not_run.stdout_sha256,
            not_run.stderr_sha256,
        ) == (None, None, None, None, None)

    payload = _read_json(revision_dir / "evidence.json")
    persisted_preflight = cast(list[dict[str, object]], payload["preflight"])
    persisted_commands = cast(list[dict[str, object]], payload["commands"])
    assert payload["schema_version"] == 2
    assert payload["status"] == "failed"
    assert [entry["status"] for entry in persisted_preflight] == ["passed"]
    assert [entry["name"] for entry in persisted_commands] == [command.name for command in commands]
    assert [entry["status"] for entry in persisted_commands] == [command.status for command in evidence.commands]


def test_initial_revision_mismatch_preserves_expected_revision_evidence(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    expected_evidence_path = artifact_root / _REVISION / "evidence.json"
    expected_evidence_path.parent.mkdir(parents=True)
    prior_evidence = b'{"revision": "prior", "status": "passed"}\n'
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
    assert [command.status for command in evidence.preflight] == ["revision_changed"]
    assert [command.status for command in evidence.commands] == ["not_run"] * 5
    assert expected_evidence_path.read_bytes() == prior_evidence
    assert _read_json(artifact_root / _OTHER_REVISION / "evidence.json")["status"] == "failed"
    assert not runner.calls


@pytest.mark.parametrize("failing_check", range(1, 14))
def test_revision_drift_fails_at_every_exact_check(
    tmp_path: Path,
    failing_check: int,
) -> None:
    revisions = [_REVISION] * 13
    revisions[failing_check - 1] = _OTHER_REVISION
    resolver = _RevisionResolver(*revisions)
    runner = _CommandRunner(*_success_results())

    evidence, revision_dir = _run_gate(tmp_path, resolver=resolver, runner=runner)

    if failing_check <= 2:
        expected_preflight = ["revision_changed"]
        expected_commands = ["not_run"] * 5
    else:
        affected_command = min((failing_check - 3) // 2, 4)
        expected_preflight = ["passed"]
        expected_commands = ["passed"] * affected_command + ["revision_changed"] + ["not_run"] * (4 - affected_command)
    assert resolver.calls == failing_check
    assert len(runner.calls) == failing_check // 2
    assert evidence.status == "failed"
    assert [command.status for command in evidence.preflight] == expected_preflight
    assert [command.status for command in evidence.commands] == expected_commands
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
    assert [command.status for command in evidence.preflight] == ["revision_changed"]
    assert [command.status for command in evidence.commands] == ["not_run"] * 5


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
        pytest.param(("unknown-operation",), id="unsupported-command"),
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


def test_resolve_bookmark_revision_uses_one_exact_jj_query(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[str, ...], Path]] = []

    def completed_run(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert capture_output is True
        assert text is True
        calls.append((argv, cwd))
        return subprocess.CompletedProcess(argv, 0, stdout=f"{_REVISION}\n", stderr="")

    monkeypatch.setattr(gate_module.subprocess, "run", completed_run)

    assert resolve_bookmark_revision(_REPOSITORY_ROOT, "cumulative-mpc-learning") == _REVISION
    assert calls == [
        (
            (
                "jj",
                "--no-pager",
                "log",
                "--no-graph",
                "-r",
                "cumulative-mpc-learning",
                "-T",
                'commit_id ++ "\\n"',
            ),
            _REPOSITORY_ROOT,
        )
    ]


def test_resolve_current_operation_id_uses_one_exact_jj_query(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[str, ...], Path]] = []

    def completed_run(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert capture_output is True
        assert text is True
        calls.append((argv, cwd))
        return subprocess.CompletedProcess(argv, 0, stdout=f"{_OPERATION_ID}\n", stderr="")

    monkeypatch.setattr(gate_module.subprocess, "run", completed_run)

    assert resolve_current_operation_id(_REPOSITORY_ROOT) == _OPERATION_ID
    assert calls == [
        (
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
            _REPOSITORY_ROOT,
        )
    ]


def test_resolve_bookmark_revision_can_pin_query_to_exact_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[str, ...], Path]] = []

    def completed_run(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((argv, cwd))
        return subprocess.CompletedProcess(argv, 0, stdout=f"{_REVISION}\n", stderr="")

    monkeypatch.setattr(gate_module.subprocess, "run", completed_run)

    assert (
        resolve_bookmark_revision(
            _REPOSITORY_ROOT,
            "@",
            operation_id=_OPERATION_ID,
        )
        == _REVISION
    )
    assert calls == [
        (
            (
                "jj",
                "--at-operation",
                _OPERATION_ID,
                "--no-pager",
                "log",
                "--no-graph",
                "-r",
                "@",
                "-T",
                'commit_id ++ "\\n"',
            ),
            _REPOSITORY_ROOT,
        )
    ]


def test_resolve_bookmark_revision_rejects_short_operation_prefix_before_jj_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def completed_run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=f"{_REVISION}\n", stderr="")

    monkeypatch.setattr(gate_module.subprocess, "run", completed_run)

    with pytest.raises(ValueError, match="full 128-character lowercase hexadecimal"):
        _ = resolve_bookmark_revision(
            _REPOSITORY_ROOT,
            "@",
            operation_id=_SHORT_OPERATION_ID,
        )

    assert calls == []


class _PushRunner(_CommandRunner):
    def __init__(
        self,
        *results: tuple[int, bytes, bytes] | BaseException,
        push_exit_code: int = 0,
        events: list[str] | None = None,
    ) -> None:
        super().__init__(*results, events=events)
        self.push_exit_code = push_exit_code
        self.push_calls: list[tuple[tuple[str, ...], Path]] = []

    @override
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        **kwargs: object,
    ) -> subprocess.CompletedProcess:
        if len(argv) >= 6 and argv[:2] == ("jj", "--at-operation") and argv[3:6] == ("git", "push", "-b"):
            assert check is False
            assert kwargs == {"capture_output": True, "text": True}
            self.push_calls.append((argv, cwd))
            return subprocess.CompletedProcess(argv, self.push_exit_code, stdout="", stderr="push failed")
        stdout = kwargs.get("stdout")
        stderr = kwargs.get("stderr")
        assert hasattr(stdout, "write")
        assert hasattr(stderr, "write")
        return super().__call__(
            argv,
            cwd=cwd,
            check=check,
            stdout=cast(IO[bytes], stdout),
            stderr=cast(IO[bytes], stderr),
            env=cast(dict[str, str] | None, kwargs.get("env")),
        )


def _bookmark_resolver(
    monkeypatch: pytest.MonkeyPatch,
    revisions: dict[str, list[str]],
) -> list[str]:
    calls: list[str] = []

    def resolve(_root: Path, bookmark: str, *, operation_id: str | None = None) -> str:
        assert operation_id is None or operation_id == _OPERATION_ID
        calls.append(bookmark)
        return revisions[bookmark].pop(0)

    monkeypatch.setattr(gate_module, "resolve_bookmark_revision", resolve)

    @contextmanager
    def no_live_runtime(_root: Path) -> Iterator[None]:
        yield

    monkeypatch.setattr(gate_module, "_isolated_live_pifire", no_live_runtime, raising=False)
    return calls


def test_only_playwright_receives_the_live_runtime_datastore_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_db = str(tmp_path / "live" / "pifire.db")
    live_logs = str(tmp_path / "live" / "logs")
    monkeypatch.setenv("PIFIRE_GATE_LIVE_DB_PATH", live_db)
    monkeypatch.setenv("PIFIRE_GATE_LIVE_LOG_DIR", live_logs)
    monkeypatch.setenv("PIFIRE_DB_PATH", str(tmp_path / "pytest" / "pifire.db"))
    monkeypatch.setenv("PIFIRE_LOG_DIR", str(tmp_path / "pytest" / "logs"))
    runner = _CommandRunner(*_success_results())

    evidence = run_gate(
        root=_REPOSITORY_ROOT,
        expected_revision=_REVISION,
        artifact_root=tmp_path / "artifacts",
        resolve_revision=_RevisionResolver(*([_REVISION] * 13)),
        run_command=runner,
    )
    assert evidence.status == "passed"

    for command_environment in runner.environments[:5]:
        assert command_environment is not None
        assert "PIFIRE_GATE_LIVE_DB_PATH" not in command_environment
        assert "PIFIRE_GATE_LIVE_LOG_DIR" not in command_environment
        assert "PIFIRE_DB_PATH" not in command_environment
        assert "PIFIRE_LOG_DIR" not in command_environment
    playwright_environment = runner.environments[5]
    assert playwright_environment is not None
    assert playwright_environment["PIFIRE_DB_PATH"] == live_db
    assert playwright_environment["PIFIRE_LOG_DIR"] == live_logs
    assert os.environ["PIFIRE_DB_PATH"] == str(tmp_path / "pytest" / "pifire.db")
    assert os.environ["PIFIRE_LOG_DIR"] == str(tmp_path / "pytest" / "logs")
    assert "PIFIRE_GATE_LIVE_DB_PATH" not in playwright_environment
    assert "PIFIRE_GATE_LIVE_LOG_DIR" not in playwright_environment


def test_local_live_runtime_copies_code_but_replaces_mutable_folders(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    _ = (root / "app.py").write_text("application = True\n", encoding="utf-8")
    for mutable_folder in ("backups", "history", "logs"):
        folder = root / mutable_folder
        folder.mkdir()
        _ = (folder / "operator-data").write_text("must not be copied\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    source_root, log_dir = gate_module._prepare_isolated_source(root, runtime)

    assert (source_root / "app.py").read_text(encoding="utf-8") == "application = True\n"
    assert list((source_root / "backups").iterdir()) == []
    assert list((source_root / "history").iterdir()) == []
    assert (source_root / "logs").is_symlink()
    assert (source_root / "logs").resolve() == log_dir.resolve()
    assert list(log_dir.iterdir()) == []


def test_local_live_runtime_clears_browser_backend_overrides_for_entire_scope_and_restores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inherited_overrides = {
        "PUBLIC_PIFIRE_URL": "http://real-grill.example:5000",
        "PUBLIC_PIFIRE_TARGET": "http://other-real-grill.example:5000",
    }
    for name, value in inherited_overrides.items():
        monkeypatch.setenv(name, value)

    source_root = tmp_path / "source"
    log_dir = tmp_path / "runtime-logs"
    service_environments: list[dict[str, str]] = []

    def assert_overrides_cleared(environment: dict[str, str] | None = None) -> None:
        for name in inherited_overrides:
            assert name not in os.environ
            if environment is not None:
                assert name not in environment

    def prepare_source(_root: Path, _runtime: Path) -> tuple[Path, Path]:
        assert_overrides_cleared()
        source_root.mkdir()
        log_dir.mkdir()
        return source_root, log_dir

    def initialize(
        _argv: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == source_root
        assert check is False
        assert capture_output is True
        assert text is True
        assert_overrides_cleared(env)
        assert _argv[:2] == (sys.executable, "-c")
        assert "settings['globals']['first_time_setup'] = False" in _argv[2]
        service_environments.append(env)
        return subprocess.CompletedProcess((), 0, stdout="", stderr="")

    class RunningProcess:
        def __init__(self, _argv: tuple[str, ...], **kwargs: object) -> None:
            environment = cast(dict[str, str], kwargs["env"])
            assert_overrides_cleared(environment)
            service_environments.append(environment)
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

        def wait(self, *, timeout: int) -> int:
            assert timeout == 5
            assert self.returncode is not None
            return self.returncode

    @contextmanager
    def backend_ready(_url: str, *, timeout: int) -> Iterator[object]:
        assert timeout == 1
        assert_overrides_cleared()

        class Response:
            status = 201

        yield Response()

    monkeypatch.setattr(gate_module, "_prepare_isolated_source", prepare_source)
    monkeypatch.setattr(gate_module, "_available_local_ports", lambda _count: (5100, 5101, 5102))
    monkeypatch.setattr(gate_module.subprocess, "run", initialize)
    monkeypatch.setattr(gate_module.subprocess, "Popen", RunningProcess)
    monkeypatch.setattr(gate_module.urllib.request, "urlopen", backend_ready)

    with gate_module._isolated_live_pifire(_REPOSITORY_ROOT):
        assert_overrides_cleared()
        playwright_environment = gate_module._command_environment(required_commands()[-1])
        assert playwright_environment is not None
        assert_overrides_cleared(playwright_environment)

    assert service_environments
    for name, value in inherited_overrides.items():
        assert os.environ[name] == value


def test_verify_bookmark_runs_gate_inside_an_isolated_live_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _bookmark_resolver(
        monkeypatch,
        {
            "cumulative-mpc-learning": [_REVISION],
            "@": [_REVISION] * 14,
        },
    )

    @contextmanager
    def live_runtime(_root: Path) -> Iterator[None]:
        events.append("runtime:start")
        yield
        events.append("runtime:stop")

    monkeypatch.setattr(gate_module, "_isolated_live_pifire", live_runtime, raising=False)
    runner = _PushRunner(*_success_results(), events=events)

    evidence = gate_module._verify_bookmark(
        root=_REPOSITORY_ROOT,
        bookmark="cumulative-mpc-learning",
        artifact_root=tmp_path / "artifacts",
        run_command=runner,
    )

    assert evidence.status == "passed"
    assert events == [
        "runtime:start",
        "run:contract-preflight",
        "run:rebuild-acados",
        "run:python-default",
        "run:python-slow",
        "run:bun-workspaces",
        "run:web-react-e2e",
        "runtime:stop",
    ]


def test_push_runs_a_fresh_gate_and_only_the_authorized_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _bookmark_resolver(
        monkeypatch,
        {
            "cumulative-mpc-learning": [_REVISION, _REVISION],
            "@": [_REVISION] * 15,
            "cumulative-mpc-learning@origin": [_REVISION],
        },
    )
    runner = _PushRunner(*_success_results())

    evidence = push_verified_bookmark(
        root=_REPOSITORY_ROOT,
        bookmark="cumulative-mpc-learning",
        artifact_root=tmp_path / "artifacts",
        run_command=runner,
        resolve_operation=lambda: _OPERATION_ID,
    )

    assert evidence.status == "passed"
    assert len(runner.calls) == 6
    assert runner.push_calls == [
        (
            ("jj", "--at-operation", _OPERATION_ID, "git", "push", "-b", "cumulative-mpc-learning"),
            _REPOSITORY_ROOT,
        )
    ]
    assert calls[:2] == ["cumulative-mpc-learning", "@"]
    assert calls[-3:] == ["@", "cumulative-mpc-learning", "cumulative-mpc-learning@origin"]


def test_push_stays_pinned_when_live_bookmark_moves_before_command_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_bookmark = {"revision": _REVISION}
    race_events: list[str] = []

    def resolve(root: Path, bookmark: str, *, operation_id: str | None = None) -> str:
        assert root == _REPOSITORY_ROOT
        if bookmark == "cumulative-mpc-learning@origin":
            return _REVISION
        if operation_id is not None:
            assert operation_id == _OPERATION_ID
            race_events.append(f"pinned:{bookmark}")
            return _REVISION
        if bookmark == "@":
            return _REVISION
        return live_bookmark["revision"]

    monkeypatch.setattr(gate_module, "resolve_bookmark_revision", resolve)

    @contextmanager
    def no_live_runtime(_root: Path) -> Iterator[None]:
        yield

    monkeypatch.setattr(gate_module, "_isolated_live_pifire", no_live_runtime)

    class BookmarkRaceRunner(_PushRunner):
        @override
        def __call__(
            self,
            argv: tuple[str, ...],
            *,
            cwd: Path,
            check: bool,
            **kwargs: object,
        ) -> subprocess.CompletedProcess:
            if len(argv) >= 6 and argv[:2] == ("jj", "--at-operation") and argv[3:5] == ("git", "push"):
                assert race_events == ["pinned:@", "pinned:cumulative-mpc-learning"]
                live_bookmark["revision"] = _OTHER_REVISION
                race_events.append("live-bookmark:moved")
            return super().__call__(argv, cwd=cwd, check=check, **kwargs)

    runner = BookmarkRaceRunner(*_success_results())

    evidence = push_verified_bookmark(
        root=_REPOSITORY_ROOT,
        bookmark="cumulative-mpc-learning",
        artifact_root=tmp_path / "artifacts",
        run_command=runner,
        resolve_operation=lambda: _OPERATION_ID,
    )

    assert evidence.status == "passed"
    assert live_bookmark["revision"] == _OTHER_REVISION
    assert race_events == ["pinned:@", "pinned:cumulative-mpc-learning", "live-bookmark:moved"]
    assert runner.push_calls == [
        (
            ("jj", "--at-operation", _OPERATION_ID, "git", "push", "-b", "cumulative-mpc-learning"),
            _REPOSITORY_ROOT,
        )
    ]


def test_push_owns_artifact_lock_until_remote_and_durable_evidence_are_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "artifacts"
    _bookmark_resolver(
        monkeypatch,
        {
            "cumulative-mpc-learning": [_REVISION, _REVISION],
            "@": [_REVISION] * 15,
            "cumulative-mpc-learning@origin": [_REVISION],
        },
    )
    competing_command_started = threading.Event()
    final_validation_started = threading.Event()
    release_final_validation = threading.Event()
    errors: list[BaseException] = []
    competing_threads: list[threading.Thread] = []

    class CompetingRunner(_CommandRunner):
        @override
        def __call__(
            self,
            argv: tuple[str, ...],
            *,
            cwd: Path,
            check: bool,
            stdout: IO[bytes],
            stderr: IO[bytes],
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess[bytes]:
            competing_command_started.set()
            return super().__call__(
                argv,
                cwd=cwd,
                check=check,
                stdout=stdout,
                stderr=stderr,
                env=env,
            )

    def run_competing_gate() -> None:
        try:
            _ = run_gate(
                root=_REPOSITORY_ROOT,
                expected_revision=_REVISION,
                artifact_root=artifact_root,
                resolve_revision=lambda: _REVISION,
                run_command=CompetingRunner(*_success_results()),
            )
        except BaseException as error:
            errors.append(error)

    class ConcurrentPushRunner(_PushRunner):
        @override
        def __call__(
            self,
            argv: tuple[str, ...],
            *,
            cwd: Path,
            check: bool,
            **kwargs: object,
        ) -> subprocess.CompletedProcess:
            if len(argv) >= 6 and argv[:2] == ("jj", "--at-operation") and argv[3:5] == ("git", "push"):
                competing_thread = threading.Thread(target=run_competing_gate)
                competing_threads.append(competing_thread)
                competing_thread.start()
            return super().__call__(argv, cwd=cwd, check=check, **kwargs)

    def validate_durable_evidence(*, evidence: GateEvidence, revision_dir: Path) -> None:
        assert evidence.status == "passed"
        assert revision_dir == artifact_root / _REVISION
        final_validation_started.set()
        assert not competing_command_started.wait(timeout=0.1)
        assert release_final_validation.wait(timeout=2)

    monkeypatch.setattr(
        gate_module,
        "_validate_durable_pass_evidence",
        validate_durable_evidence,
        raising=False,
    )
    runner = ConcurrentPushRunner(*_success_results())

    def invoke_push() -> None:
        try:
            _ = push_verified_bookmark(
                root=_REPOSITORY_ROOT,
                bookmark="cumulative-mpc-learning",
                artifact_root=artifact_root,
                run_command=runner,
                resolve_operation=lambda: _OPERATION_ID,
            )
        except BaseException as error:
            errors.append(error)

    push_thread = threading.Thread(target=invoke_push)
    push_thread.start()
    assert final_validation_started.wait(timeout=2)
    assert not competing_command_started.is_set()
    release_final_validation.set()
    push_thread.join(timeout=2)
    for competing_thread in competing_threads:
        competing_thread.join(timeout=2)

    assert not push_thread.is_alive()
    assert competing_threads
    assert all(not thread.is_alive() for thread in competing_threads)
    assert not errors
    assert competing_command_started.is_set()
    assert len(runner.push_calls) == 1


def test_push_rejects_every_other_bookmark_before_resolving_or_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_resolve(_root: Path, _bookmark: str) -> str:
        pytest.fail("an unauthorized bookmark must not be resolved")

    monkeypatch.setattr(gate_module, "resolve_bookmark_revision", unexpected_resolve)
    runner = _PushRunner(*_success_results())

    with pytest.raises(ValueError, match="restricted to bookmark 'cumulative-mpc-learning'"):
        push_verified_bookmark(
            root=_REPOSITORY_ROOT,
            bookmark="some-other-bookmark",
            artifact_root=tmp_path / "artifacts",
            run_command=runner,
        )

    assert runner.calls == []
    assert runner.push_calls == []


def test_push_is_not_invoked_when_local_bookmark_differs_from_current_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bookmark_resolver(
        monkeypatch,
        {
            "cumulative-mpc-learning": [_REVISION],
            "@": [_OTHER_REVISION],
        },
    )
    runner = _PushRunner(*_success_results())

    with pytest.raises(RuntimeError, match="does not equal the current revision"):
        push_verified_bookmark(
            root=_REPOSITORY_ROOT,
            bookmark="cumulative-mpc-learning",
            artifact_root=tmp_path / "artifacts",
            run_command=runner,
        )

    assert runner.calls == []
    assert runner.push_calls == []
    assert not (tmp_path / "artifacts").exists()


def test_push_is_not_invoked_when_fresh_gate_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bookmark_resolver(
        monkeypatch,
        {
            "cumulative-mpc-learning": [_REVISION],
            "@": [_REVISION] * 3,
        },
    )
    runner = _PushRunner((1, b"", b"failed\n"))

    evidence = push_verified_bookmark(
        root=_REPOSITORY_ROOT,
        bookmark="cumulative-mpc-learning",
        artifact_root=tmp_path / "artifacts",
        run_command=runner,
    )

    assert evidence.status == "failed"
    assert evidence.preflight[0].status == "failed"
    assert [command.status for command in evidence.commands] == ["not_run"] * 5
    assert runner.push_calls == []


@pytest.mark.parametrize(
    ("completed", "message"),
    [
        pytest.param(
            subprocess.CompletedProcess((), 0, stdout="not-a-full-operation-id\n", stderr=""),
            "full 128-character",
            id="invalid",
        ),
        pytest.param(
            subprocess.CompletedProcess((), 0, stdout=f"{_SHORT_OPERATION_ID}\n", stderr=""),
            "full 128-character",
            id="shortened-prefix",
        ),
        pytest.param(
            subprocess.CompletedProcess((), 1, stdout="", stderr="operation resolver failed\n"),
            "operation resolver failed",
            id="failed",
        ),
    ],
)
def test_push_is_not_invoked_when_operation_resolution_is_invalid_or_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed: subprocess.CompletedProcess[str],
    message: str,
) -> None:
    _bookmark_resolver(
        monkeypatch,
        {
            "cumulative-mpc-learning": [_REVISION],
            "@": [_REVISION] * 14,
        },
    )

    def resolve_operation(
        _argv: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return completed

    monkeypatch.setattr(gate_module.subprocess, "run", resolve_operation)
    runner = _PushRunner(*_success_results())

    with pytest.raises(RuntimeError, match=message):
        push_verified_bookmark(
            root=_REPOSITORY_ROOT,
            bookmark="cumulative-mpc-learning",
            artifact_root=tmp_path / "artifacts",
            run_command=runner,
        )

    assert len(runner.calls) == 6
    assert runner.push_calls == []


@pytest.mark.parametrize("drifting_identity", ["bookmark", "current"])
def test_push_is_not_invoked_when_revision_drifts_after_the_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drifting_identity: str,
) -> None:
    bookmark_revisions = [_REVISION, _REVISION]
    current_revisions = [_REVISION] * 15
    if drifting_identity == "bookmark":
        bookmark_revisions[-1] = _OTHER_REVISION
    else:
        current_revisions[-1] = _OTHER_REVISION
    _bookmark_resolver(
        monkeypatch,
        {
            "cumulative-mpc-learning": bookmark_revisions,
            "@": current_revisions,
        },
    )
    runner = _PushRunner(*_success_results())

    with pytest.raises(RuntimeError, match="changed after the gate"):
        push_verified_bookmark(
            root=_REPOSITORY_ROOT,
            bookmark="cumulative-mpc-learning",
            artifact_root=tmp_path / "artifacts",
            run_command=runner,
            resolve_operation=lambda: _OPERATION_ID,
        )

    assert len(runner.calls) == 6
    assert runner.push_calls == []


def test_post_push_remote_mismatch_fails_after_exactly_one_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bookmark_resolver(
        monkeypatch,
        {
            "cumulative-mpc-learning": [_REVISION, _REVISION],
            "@": [_REVISION] * 15,
            "cumulative-mpc-learning@origin": [_OTHER_REVISION],
        },
    )
    runner = _PushRunner(*_success_results())

    with pytest.raises(RuntimeError, match="remote-tracking bookmark"):
        push_verified_bookmark(
            root=_REPOSITORY_ROOT,
            bookmark="cumulative-mpc-learning",
            artifact_root=tmp_path / "artifacts",
            run_command=runner,
            resolve_operation=lambda: _OPERATION_ID,
        )

    assert runner.push_calls == [
        (
            ("jj", "--at-operation", _OPERATION_ID, "git", "push", "-b", "cumulative-mpc-learning"),
            _REPOSITORY_ROOT,
        )
    ]


def test_push_fails_if_durable_evidence_changes_before_final_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "artifacts"
    resolver_calls: list[str] = []

    def resolve(_root: Path, bookmark: str, *, operation_id: str | None = None) -> str:
        assert operation_id is None or operation_id == _OPERATION_ID
        resolver_calls.append(bookmark)
        if bookmark == "cumulative-mpc-learning@origin":
            evidence_path = artifact_root / _REVISION / "evidence.json"
            _ = evidence_path.write_text('{"status": "passed"}\n', encoding="utf-8")
        return _REVISION

    monkeypatch.setattr(gate_module, "resolve_bookmark_revision", resolve)

    @contextmanager
    def no_live_runtime(_root: Path) -> Iterator[None]:
        yield

    monkeypatch.setattr(gate_module, "_isolated_live_pifire", no_live_runtime)
    runner = _PushRunner(*_success_results())

    with pytest.raises(RuntimeError, match="durable evidence"):
        push_verified_bookmark(
            root=_REPOSITORY_ROOT,
            bookmark="cumulative-mpc-learning",
            artifact_root=artifact_root,
            run_command=runner,
            resolve_operation=lambda: _OPERATION_ID,
        )

    assert resolver_calls[-1] == "cumulative-mpc-learning@origin"
    assert len(runner.push_calls) == 1


def test_ci_entrypoint_and_exact_revision_workflow_are_absent(tmp_path: Path) -> None:
    workflow_path = _REPOSITORY_ROOT / ".github" / "workflows" / "integration-gate.yml"
    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "verify-ci",
            "--expected-revision",
            _REVISION,
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert not workflow_path.exists()
    assert completed.returncode == 2
    assert "invalid choice: 'verify-ci'" in completed.stderr
    assert not (tmp_path / ".artifacts").exists()


def test_prek_pre_push_hook_verifies_the_cumulative_bookmark() -> None:
    config = tomllib.loads((_REPOSITORY_ROOT / "prek.toml").read_text(encoding="utf-8"))
    hooks = [hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]]

    assert hooks == [
        {
            "id": "exact-revision-gate",
            "name": "Exact revision integration gate",
            "entry": (
                "uv run python scripts/exact_revision_gate.py verify-bookmark --bookmark cumulative-mpc-learning"
            ),
            "language": "system",
            "pass_filenames": False,
            "stages": ["pre-push"],
        }
    ]
