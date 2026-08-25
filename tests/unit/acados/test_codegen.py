from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Callable

import pytest

from controller.acados.codegen import cli as cli_module
from controller.acados.codegen.cli import RegenerationError, _atomic_replace_tree, regenerate
from controller.acados.codegen.grey_box_ocp import normalize_generated_tree
from controller.acados.codegen.manifest import (
    PINNED_ENVIRONMENT,
    EnvironmentMismatch,
    create_manifest,
    validate_environment,
)


Generator = Callable[[Path], Path]
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_ACADOS_URL = "https://github.com/acados/acados.git"
_ACADOS_REVISION = "503364817c872d474ab5bed219c26760ac267769"
_RECURSIVE_DEPENDENCIES = {
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
}
_PYTHON_GENERATOR_DEPENDENCIES = {
    "casadi": "3.7.2",
    "Cython": "3.2.9",
    "Deprecated": "1.3.1",
    "matplotlib": "3.11.1",
    "numpy": "2.5.1",
    "scipy": "1.18.0",
    "setuptools-scm": "8.3.1",
}
_EXPECTED_ENVIRONMENT = {
    "acados": {
        "url": _CANONICAL_ACADOS_URL,
        "revision": _ACADOS_REVISION,
        "tag": "v0.6.0",
        "recursive_dependencies": _RECURSIVE_DEPENDENCIES,
    },
    "python_generator_dependencies": _PYTHON_GENERATOR_DEPENDENCIES,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _write_solver(directory: Path, payload: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "solver.c").write_bytes(payload)
    (directory / "pifire_grey.json").write_text(
        json.dumps(
            {
                "dims": {"N": 24, "np": 12, "nu": 1, "nx": 11},
                "solver_options": {
                    "integrator_type": "DISCRETE",
                    "nlp_solver_type": "SQP",
                    "qp_solver": "PARTIAL_CONDENSING_HPIPM",
                },
                "code_gen_options": {
                    "code_export_directory": "<GENERATED_DIRECTORY>",
                    "json_file": "<GENERATED_DIRECTORY>/pifire_grey.json",
                },
            },
            sort_keys=True,
        )
        + "\n"
    )
    return directory


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    definition = repository / "controller/acados/codegen/grey_box_ocp.py"
    definition.parent.mkdir(parents=True)
    definition.write_text("GREY = 1\n")
    generated = repository / "native/generated"
    _write_solver(generated / "grey_box", b"old grey\n")
    (generated / "manifest.json").write_text('{"schema": 0}\n')
    return repository


def _generators(calls: list[str] | None = None) -> dict[str, Generator]:
    def grey(directory: Path) -> Path:
        if calls is not None:
            calls.append("grey_box")
        return _write_solver(directory, b"new grey\n")

    return {"grey_box": grey}


def test_pinned_environment_uses_canonical_source_and_complete_exact_revisions() -> None:
    assert PINNED_ENVIRONMENT == _EXPECTED_ENVIRONMENT


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("url", "https://example.invalid/acados.git", "acados URL"),
        ("revision", "0" * 40, "acados revision"),
    ],
)
def test_manifest_rejects_wrong_acados_source_pin(field: str, replacement: str, message: str) -> None:
    actual = deepcopy(_EXPECTED_ENVIRONMENT)
    actual["acados"][field] = replacement

    with pytest.raises(EnvironmentMismatch, match=message):
        validate_environment(actual)


def test_manifest_rejects_incomplete_recursive_dependency_provenance() -> None:
    actual = deepcopy(_EXPECTED_ENVIRONMENT)
    del actual["acados"]["recursive_dependencies"]["external/blasfeo"]

    with pytest.raises(EnvironmentMismatch, match="recursive dependency"):
        validate_environment(actual)


def test_manifest_rejects_unlocked_python_generator_version() -> None:
    actual = deepcopy(_EXPECTED_ENVIRONMENT)
    actual["python_generator_dependencies"]["casadi"] = "3.7.1"

    with pytest.raises(EnvironmentMismatch, match="casadi"):
        validate_environment(actual)


def test_manifest_is_exactly_grey_only_and_hashes_every_generated_file(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    generated = tmp_path / "generated"
    _write_solver(generated / "grey_box", b"grey\n")

    manifest = create_manifest(
        repository,
        generated,
        environment=deepcopy(_EXPECTED_ENVIRONMENT),
    )

    metadata = generated / "grey_box/pifire_grey.json"
    solver = generated / "grey_box/solver.c"
    definition = repository / "controller/acados/codegen/grey_box_ocp.py"
    assert set(manifest) == {
        "schema",
        "acados",
        "python_generator_dependencies",
        "model_definitions",
        "solvers",
        "files",
    }
    assert manifest["schema"] == 1
    assert manifest["acados"] == _EXPECTED_ENVIRONMENT["acados"]
    assert manifest["python_generator_dependencies"] == (_EXPECTED_ENVIRONMENT["python_generator_dependencies"])
    assert manifest["model_definitions"] == {"controller/acados/codegen/grey_box_ocp.py": _sha256(definition)}
    assert manifest["solvers"] == {
        "grey_box": {
            "metadata": "grey_box/pifire_grey.json",
            "dimensions": {"N": 24, "np": 12, "nu": 1, "nx": 11},
            "options": {
                "integrator_type": "DISCRETE",
                "nlp_solver_type": "SQP",
                "qp_solver": "PARTIAL_CONDENSING_HPIPM",
            },
        }
    }
    assert manifest["files"] == {
        "grey_box/pifire_grey.json": _sha256(metadata),
        "grey_box/solver.c": _sha256(solver),
    }
    assert all(not Path(path).is_absolute() for path in manifest["files"])
    assert not any("linear" in path for path in manifest["files"])


def test_checked_in_manifest_hashes_the_complete_grey_only_tree() -> None:
    generated = _REPOSITORY_ROOT / "native/generated"
    manifest = json.loads((generated / "manifest.json").read_text())
    actual_files = {
        path.relative_to(generated).as_posix(): _sha256(path)
        for path in sorted(generated.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }

    assert actual_files
    assert manifest["acados"] == _EXPECTED_ENVIRONMENT["acados"]
    assert manifest["python_generator_dependencies"] == (_EXPECTED_ENVIRONMENT["python_generator_dependencies"])
    assert set(manifest["solvers"]) == {"grey_box"}
    assert manifest["solvers"]["grey_box"]["metadata"] == ("grey_box/pifire_grey.json")
    assert manifest["model_definitions"] == {
        "controller/acados/codegen/grey_box_ocp.py": _sha256(
            _REPOSITORY_ROOT / "controller/acados/codegen/grey_box_ocp.py"
        )
    }
    assert manifest["files"] == actual_files
    assert all(path.startswith("grey_box/") for path in manifest["files"])


def test_regeneration_and_check_mode_are_deterministic_and_grey_only(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    calls: list[str] = []

    assert (
        regenerate(
            "write",
            repository_root=repository,
            generators=_generators(calls),
            gate=lambda *_: None,
            environment=deepcopy(_EXPECTED_ENVIRONMENT),
        )
        == 0
    )
    generated = repository / "native/generated"
    first = _tree_bytes(generated)

    assert (
        regenerate(
            "check",
            repository_root=repository,
            generators=_generators(calls),
            gate=lambda *_: pytest.fail("check mode must not run write gates"),
            environment=deepcopy(_EXPECTED_ENVIRONMENT),
        )
        == 0
    )
    assert (
        regenerate(
            "check",
            repository_root=repository,
            generators=_generators(calls),
            gate=lambda *_: pytest.fail("check mode must not run write gates"),
            environment=deepcopy(_EXPECTED_ENVIRONMENT),
        )
        == 0
    )

    assert calls == ["grey_box", "grey_box", "grey_box"]
    assert _tree_bytes(generated) == first
    assert not (generated / "linear").exists()


def test_check_is_nonmutating_and_reports_added_removed_and_changed_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = _repository(tmp_path)
    assert (
        regenerate(
            "write",
            repository_root=repository,
            generators=_generators(),
            gate=lambda *_: None,
            environment=deepcopy(_EXPECTED_ENVIRONMENT),
        )
        == 0
    )
    generated = repository / "native/generated"
    (generated / "grey_box/solver.c").write_bytes(b"deliberately changed\n")
    (generated / "grey_box/pifire_grey.json").unlink()
    (generated / "unexpected.txt").write_text("unexpected\n")
    before = _tree_bytes(generated)

    assert (
        regenerate(
            "check",
            repository_root=repository,
            generators=_generators(),
            gate=lambda *_: pytest.fail("check mode must not run write gates"),
            environment=deepcopy(_EXPECTED_ENVIRONMENT),
        )
        == 1
    )

    assert _tree_bytes(generated) == before
    output = capsys.readouterr().out
    assert "changed: grey_box/solver.c" in output
    assert "removed: grey_box/pifire_grey.json" in output
    assert "added: unexpected.txt" in output


def _write_generated_path_fixture(
    directory: Path,
    *,
    checkout: str,
    acados_source: str,
    acados_build: str,
    python_prefix: str,
) -> None:
    directory.mkdir(parents=True)
    export = f"{checkout}/native/generated/grey_box"
    (directory / "CMakeLists.txt").write_text(
        "\n".join(
            (
                'if(CMAKE_CXX_COMPILER_ID MATCHES "MSVC")',
                f"    set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELEASE {export})",
                f"    set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY_RELEASE {export})",
                f"    set(CMAKE_LIBRARY_OUTPUT_DIRECTORY_RELEASE {export})",
                "endif()",
                f'set(ACADOS_INCLUDE_PATH {acados_source}/include CACHE PATH "include")',
                f'set(ACADOS_LIB_PATH {acados_build}/lib CACHE PATH "lib")',
                "",
            )
        )
    )
    (directory / "Makefile").write_text(
        "\n".join(
            (
                f"INCLUDE_PATH = {acados_source}/include",
                f"LIB_PATH = {acados_build}/lib",
                f"\t-I {export} \\",
                f"\t-I {checkout}/.venv/lib/python3.14/site-packages/numpy/_core/include \\",
                f"\t-I {python_prefix}/include/python3.14 \\",
                "",
            )
        )
    )
    (directory / "pifire_grey.json").write_text(
        json.dumps(
            {
                "code_gen_options": {
                    "acados_include_path": f"{acados_source}/include",
                    "acados_lib_path": f"{acados_build}/lib",
                    "code_export_directory": export,
                    "cython_include_dirs": [
                        f"{checkout}/.venv/lib/python3.14/site-packages/numpy/_core/include",
                        f"{python_prefix}/include/python3.14",
                    ],
                    "json_file": f"{export}/pifire_grey.json",
                },
                "hash": "checkout-dependent",
            },
            indent=4,
        )
        + "\n"
    )


def test_generated_path_normalization_removes_checkout_and_fetchcontent_paths(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_generated_path_fixture(
        first,
        checkout="/work/checkout-one",
        acados_source="/work/checkout-one/build/_deps/acados-src",
        acados_build="/work/checkout-one/build/_deps/acados-build",
        python_prefix="/opt/python-one",
    )
    _write_generated_path_fixture(
        second,
        checkout="/other/checkout-two",
        acados_source="/other/fetch-state/acados-source",
        acados_build="/other/fetch-state/acados-binary",
        python_prefix="/tools/python-two",
    )
    for directory, openmp in ((first, "-fopenmp"), (second, "")):
        metadata_path = directory / "pifire_grey.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["code_gen_options"]["acados_link_libs"] = {"openmp": openmp}
        metadata_path.write_text(json.dumps(metadata, indent=4) + "\n")

    normalize_generated_tree(first, solver_name="pifire_grey")
    normalize_generated_tree(second, solver_name="pifire_grey")
    first_normalized = _tree_bytes(first)
    normalize_generated_tree(first, solver_name="pifire_grey")

    assert _tree_bytes(first) == first_normalized
    assert _tree_bytes(first) == _tree_bytes(second)
    normalized = b"".join(first_normalized.values())
    assert b"/work/checkout-one" not in normalized
    assert b"/opt/python-one" not in normalized
    assert b"_deps" not in normalized
    assert b"vendor/acados" not in normalized
    options = json.loads((first / "pifire_grey.json").read_text())["code_gen_options"]
    assert options == {
        "acados_include_path": "<ACADOS_SOURCE_DIRECTORY>/include",
        "acados_lib_path": "<ACADOS_BUILD_DIRECTORY>/lib",
        "code_export_directory": "<GENERATED_DIRECTORY>",
        "cython_include_dirs": ["<NUMPY_INCLUDE>", "<PYTHON_INCLUDE>"],
        "acados_link_libs": {"openmp": ""},
        "json_file": "<GENERATED_DIRECTORY>/pifire_grey.json",
    }


def test_normalizer_rejects_unknown_absolute_paths(tmp_path: Path) -> None:
    generated = tmp_path / "grey_box"
    _write_generated_path_fixture(
        generated,
        checkout="/work/checkout",
        acados_source="/work/fetch/acados-source",
        acados_build="/work/fetch/acados-build",
        python_prefix="/opt/python",
    )
    (generated / "solver.c").write_text('const char *secret = "/secret/input";\n')

    with pytest.raises(ValueError, match="unrecognized absolute path.*solver.c"):
        normalize_generated_tree(generated, solver_name="pifire_grey")


def test_direct_write_refuses_publication_without_complete_gate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    generated = _REPOSITORY_ROOT / "native/generated"
    before = _tree_bytes(generated)

    assert cli_module.main(["--write"]) == 2

    assert _tree_bytes(generated) == before
    error = capsys.readouterr().err
    assert "direct --write is unsupported" in error
    assert "./rebuild-acados.sh" in error


def test_internal_stage_mode_populates_only_caller_owned_destination(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    generated = repository / "native/generated"
    before = _tree_bytes(generated)
    staging = repository / "native/.generated-staging/candidate"

    assert (
        regenerate(
            "stage",
            repository_root=repository,
            generators=_generators(),
            environment=deepcopy(_EXPECTED_ENVIRONMENT),
            staging=staging,
        )
        == 0
    )

    assert (staging / "manifest.json").is_file()
    assert (staging / "grey_box/solver.c").is_file()
    assert _tree_bytes(generated) == before


def test_write_does_not_mutate_generated_tree_when_a_gate_fails(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    generated = repository / "native/generated"
    before = _tree_bytes(generated)

    def fail_gate(*_: object) -> None:
        raise RuntimeError("compile failed")

    with pytest.raises(RuntimeError, match="compile failed"):
        regenerate(
            "write",
            repository_root=repository,
            generators=_generators(),
            gate=fail_gate,
            environment=deepcopy(_EXPECTED_ENVIRONMENT),
        )

    assert _tree_bytes(generated) == before


def test_staged_equation_parity_uses_compiled_equation_and_nonzero_sigma(
    tmp_path: Path,
) -> None:
    seen_sigma: list[float] = []

    def matching(
        state: tuple[float, ...],
        residual: float,
        parameters: tuple[float, ...],
    ) -> tuple[float, ...]:
        seen_sigma.append(parameters[5])
        return cli_module._reference_discrete_map(state, residual, parameters)

    cli_module.validate_staged_equation_parity(tmp_path, evaluator=matching)
    assert seen_sigma and all(value > 0.0 for value in seen_sigma)

    def wrong(
        state: tuple[float, ...],
        residual: float,
        parameters: tuple[float, ...],
    ) -> tuple[float, ...]:
        result = list(cli_module._reference_discrete_map(state, residual, parameters))
        result[8] += parameters[5] * 1e12
        return tuple(result)

    with pytest.raises(RegenerationError, match="equation parity"):
        cli_module.validate_staged_equation_parity(tmp_path, evaluator=wrong)


def test_atomic_replacement_uses_one_whole_tree_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "generated"
    staged = tmp_path / "staged"
    _write_solver(target / "grey_box", b"old\n")
    _write_solver(staged / "grey_box", b"new\n")
    exchanges: list[tuple[Path, Path]] = []
    monkeypatch.setattr(cli_module, "_probe_directory_exchange", lambda _: None)

    def exchange(left: Path, right: Path) -> None:
        exchanges.append((left, right))
        temporary = tmp_path / "fake-exchange"
        os.rename(left, temporary)
        os.rename(right, left)
        os.rename(temporary, right)

    monkeypatch.setattr(cli_module, "_platform_directory_exchange", exchange)

    _atomic_replace_tree(staged, target)

    assert len(exchanges) == 1
    assert exchanges[0][1] == target
    assert (target / "grey_box/solver.c").read_bytes() == b"new\n"


def test_atomic_replacement_fsyncs_copied_tree_before_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "generated"
    staged = tmp_path / "staged"
    _write_solver(target / "grey_box", b"old\n")
    _write_solver(staged / "grey_box", b"new\n")
    events: list[str] = []
    monkeypatch.setattr(cli_module, "_probe_directory_exchange", lambda _: None)
    monkeypatch.setattr(
        cli_module,
        "_fsync_tree",
        lambda _: events.append("fsync-tree"),
    )
    monkeypatch.setattr(
        cli_module,
        "_platform_directory_exchange",
        lambda _left, _right: events.append("exchange"),
    )

    _atomic_replace_tree(staged, target)

    assert events == ["fsync-tree", "exchange"]


def test_atomic_replacement_fails_before_target_mutation_when_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "generated"
    staged = tmp_path / "staged"
    _write_solver(target / "grey_box", b"old\n")
    _write_solver(staged / "grey_box", b"new\n")
    before = _tree_bytes(target)
    exchanges: list[tuple[Path, Path]] = []

    def unsupported(_: Path) -> None:
        raise RegenerationError("atomic directory exchange is unsupported")

    monkeypatch.setattr(cli_module, "_probe_directory_exchange", unsupported)
    monkeypatch.setattr(
        cli_module,
        "_platform_directory_exchange",
        lambda left, right: exchanges.append((left, right)),
    )

    with pytest.raises(RegenerationError, match="unsupported"):
        _atomic_replace_tree(staged, target)

    assert exchanges == []
    assert _tree_bytes(target) == before


def test_atomic_exchange_failure_leaves_target_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "generated"
    staged = tmp_path / "staged"
    _write_solver(target / "grey_box", b"old\n")
    _write_solver(staged / "grey_box", b"new\n")
    before = _tree_bytes(target)
    calls = 0
    monkeypatch.setattr(cli_module, "_probe_directory_exchange", lambda _: None)

    def fail_exchange(_left: Path, _right: Path) -> None:
        nonlocal calls
        calls += 1
        raise OSError("simulated exchange failure")

    monkeypatch.setattr(cli_module, "_platform_directory_exchange", fail_exchange)

    with pytest.raises(OSError, match="simulated exchange failure"):
        _atomic_replace_tree(staged, target)

    assert calls == 1
    assert _tree_bytes(target) == before
