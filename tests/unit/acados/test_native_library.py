from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import controller.acados._library as library_module
from controller.acados import _ffi
from controller.acados._library import load_native
from tools.rebuild_acados import BuildInputIdentity, canonical_build_manifest, classify_staleness

REBUILD_COMMAND = "./rebuild-acados.sh --if-needed"
PUBLIC_ABI_SYMBOLS = {
    "acados_pifire_abi_version",
    "acados_pifire_grey_create",
    "acados_pifire_grey_destroy",
    "acados_pifire_grey_reset",
    "acados_pifire_grey_solve",
}

TEST_BUILD_IDENTITY = BuildInputIdentity(
    generated_manifest={
        "schema": 1,
        "acados": {
            "url": "https://github.com/acados/acados.git",
            "tag": "v0.6.0",
            "revision": "50" * 20,
            "recursive_dependencies": {},
        },
        "python_generator_dependencies": {},
        "model_definitions": {},
        "solvers": {},
        "files": {},
    },
    native_source_sha256={"native/src/grey_box.c": "31" * 32},
    abi_version=2,
    host={"system": "test", "machine": "test"},
    compiler={"id": "test", "version": "1", "target": "test"},
    cmake={"version": "test", "generator": "test", "flags": []},
    loader={
        "platform": sys.platform,
        "library_filename": library_module._library_filename(),
        "python_implementation": "cpython",
        "python_version": "test",
    },
)


class _FakeFunction:
    def __init__(self, result: int) -> None:
        self.argtypes: list[object] | None = None
        self.restype: object | None = None
        self._result = result

    def __call__(self) -> int:
        return self._result


class _FakeLibrary:
    def __init__(self, abi_version: int) -> None:
        self.requested_symbols: list[str] = []
        self._abi_version = _FakeFunction(abi_version)

    def __getattr__(self, name: str) -> object:
        self.requested_symbols.append(name)
        if name == "acados_pifire_abi_version":
            return self._abi_version
        raise AssertionError(f"unexpected native symbol access before ABI validation: {name}")


@pytest.fixture
def published_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path, Path]]:
    controller_root = tmp_path / "checkout" / "controller"
    package = controller_root / "acados"
    runtime_root = controller_root / "_native"
    library_payload = b"unit-test native library payload"
    library_digest = hashlib.sha256(library_payload).hexdigest()
    manifest = canonical_build_manifest(
        TEST_BUILD_IDENTITY,
        library_sha256=library_digest,
        built_at="2026-08-31T00:00:00Z",
    )
    build_digest = manifest["build_digest"]
    assert isinstance(build_digest, str)
    release = runtime_root / "releases" / build_digest
    release.mkdir(parents=True)
    package.mkdir(parents=True)

    library_path = release / library_module._library_filename()
    library_path.write_bytes(library_payload)
    manifest_path = release / "build-manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    _ = (runtime_root / "current").symlink_to(release, target_is_directory=True)

    monkeypatch.setattr(library_module, "__file__", str(package / "_library.py"))
    _ffi.load_grey_api.cache_clear()
    load_native.cache_clear()
    try:
        yield runtime_root, library_path, manifest_path
    finally:
        _ffi.load_grey_api.cache_clear()
        load_native.cache_clear()


def test_published_release_uses_canonical_current_manifest(
    published_release: tuple[Path, Path, Path],
) -> None:
    _, library_path, manifest_path = published_release
    library_digest = hashlib.sha256(library_path.read_bytes()).hexdigest()

    assert (
        classify_staleness(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            TEST_BUILD_IDENTITY,
            actual_library_sha256=library_digest,
        )
        == ()
    )




def test_fixture_uses_validated_library_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    sentinel_bytes = b"validated immutable release"
    sentinel_manifest = canonical_build_manifest(
        TEST_BUILD_IDENTITY,
        library_sha256=hashlib.sha256(sentinel_bytes).hexdigest(),
        built_at="2026-08-31T00:00:00Z",
    )
    build_digest = sentinel_manifest["build_digest"]
    assert isinstance(build_digest, str)
    source_release = tmp_path / "validated-source" / build_digest
    source_release.mkdir(parents=True)
    sentinel_library = source_release / "sentinel-native-library"
    sentinel_library.write_bytes(sentinel_bytes)
    (source_release / "build-manifest.json").write_text(
        json.dumps(sentinel_manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    def unavailable_selector(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("checkout selector paths are unavailable")

    unavailable_package = tmp_path / "unavailable-checkout" / "controller" / "acados"
    monkeypatch.setattr(library_module, "__file__", str(unavailable_package / "_library.py"))
    monkeypatch.setattr(library_module, "_validated_library_path", lambda: sentinel_library)
    monkeypatch.setattr(Path, "symlink_to", unavailable_selector)

    isolated_library = request.getfixturevalue("built_native_release")

    assert not unavailable_package.exists()
    assert isolated_library != sentinel_library
    assert isolated_library.read_bytes() == sentinel_bytes


def test_missing_validated_native_release_fails_instead_of_skipping(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    def missing_release() -> Path:
        raise RuntimeError("missing native evidence")

    monkeypatch.setattr(library_module, "_validated_library_path", missing_release)

    with pytest.raises(pytest.fail.Exception, match="missing native evidence"):
        request.getfixturevalue("built_native_release")


def test_runtime_loader_resolves_library_through_controller_native_current(
    published_release: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, library_path, _ = published_release
    fake_library = _FakeLibrary(abi_version=2)
    loaded_paths: list[str] = []

    def fake_cdll(path: str) -> Any:
        loaded_paths.append(path)
        return fake_library

    monkeypatch.setattr(library_module.ctypes, "CDLL", fake_cdll)

    loaded = load_native()

    assert loaded is fake_library
    assert len(loaded_paths) == 1
    assert Path(loaded_paths[0]).resolve() == library_path.resolve()
    assert fake_library.requested_symbols == ["acados_pifire_abi_version"]


@pytest.mark.parametrize("missing", ["current", "manifest", "library"])
def test_missing_runtime_publication_names_conditional_rebuild_command(
    missing: str,
    published_release: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root, library_path, manifest_path = published_release
    if missing == "current":
        (runtime_root / "current").unlink()
    elif missing == "manifest":
        manifest_path.unlink()
    else:
        library_path.unlink()

    loaded_paths: list[str] = []
    monkeypatch.setattr(
        library_module.ctypes,
        "CDLL",
        lambda path: loaded_paths.append(path),
    )
    load_native.cache_clear()

    with pytest.raises(RuntimeError, match="rebuild-acados") as raised:
        load_native()

    assert REBUILD_COMMAND in str(raised.value)
    assert loaded_paths == []
    assert load_native.cache_info().currsize == 0


@pytest.mark.parametrize("artifact_name", ["manifest", "library"])
def test_symlinked_release_artifacts_are_rejected_before_read_or_load(
    artifact_name: str,
    published_release: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root, library_path, manifest_path = published_release
    artifact_path = manifest_path if artifact_name == "manifest" else library_path
    external_path = runtime_root.parent / f"external-{artifact_path.name}"
    artifact_path.replace(external_path)
    artifact_path.symlink_to(external_path)
    loaded_paths: list[str] = []
    monkeypatch.setattr(
        library_module.ctypes,
        "CDLL",
        lambda path: loaded_paths.append(path),
    )
    load_native.cache_clear()

    with pytest.raises(RuntimeError, match="immutable release") as raised:
        load_native()

    assert REBUILD_COMMAND in str(raised.value)
    assert loaded_paths == []


def test_manifest_build_digest_must_match_selected_release(
    published_release: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, manifest_path = published_release
    manifest = json.loads(manifest_path.read_text())
    manifest["build_digest"] = "b" * 64
    manifest_path.write_text(json.dumps(manifest) + "\n")
    loaded_paths: list[str] = []
    monkeypatch.setattr(
        library_module.ctypes,
        "CDLL",
        lambda path: loaded_paths.append(path),
    )
    load_native.cache_clear()

    with pytest.raises(RuntimeError, match="digest") as raised:
        load_native()

    assert REBUILD_COMMAND in str(raised.value)
    assert loaded_paths == []


def test_manifest_library_digest_must_match_selected_library(
    published_release: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, library_path, _ = published_release
    library_path.write_bytes(b"tampered library bytes")
    loaded_paths: list[str] = []
    monkeypatch.setattr(
        library_module.ctypes,
        "CDLL",
        lambda path: loaded_paths.append(path),
    )
    load_native.cache_clear()

    with pytest.raises(RuntimeError, match="digest") as raised:
        load_native()

    assert REBUILD_COMMAND in str(raised.value)
    assert loaded_paths == []


def test_malformed_manifest_names_conditional_rebuild_command(
    published_release: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, manifest_path = published_release
    manifest_path.write_text("not-json\n")
    loaded_paths: list[str] = []
    monkeypatch.setattr(
        library_module.ctypes,
        "CDLL",
        lambda path: loaded_paths.append(path),
    )
    load_native.cache_clear()

    with pytest.raises(RuntimeError, match="manifest") as raised:
        load_native()

    assert REBUILD_COMMAND in str(raised.value)
    assert loaded_paths == []


def test_wrong_native_abi_is_rejected_before_later_symbols_are_accessed(
    published_release: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, library_path, _ = published_release
    fake_library = _FakeLibrary(abi_version=1)
    loaded_paths: list[str] = []

    def fake_cdll(path: str) -> Any:
        loaded_paths.append(path)
        return fake_library

    monkeypatch.setattr(library_module.ctypes, "CDLL", fake_cdll)
    load_native.cache_clear()

    with pytest.raises(RuntimeError, match="expected 2.*found 1") as raised:
        load_native()

    assert Path(loaded_paths[0]).resolve() == library_path.resolve()
    assert fake_library.requested_symbols == ["acados_pifire_abi_version"]
    assert REBUILD_COMMAND in str(raised.value)
    assert load_native.cache_info().currsize == 0


def _loaded_native_path() -> Path:
    library = load_native()
    path = getattr(library, "_name", None)
    assert path is not None
    return Path(path)


def test_published_library_exposes_abi_v2(built_native_release: Path) -> None:
    library = load_native()
    library.acados_pifire_abi_version.restype = int

    assert library.acados_pifire_abi_version() == 2


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ELF export inspection")
def test_elf_library_exports_only_the_five_grey_abi_symbols(
    built_native_release: Path,
) -> None:
    result = subprocess.run(
        ["readelf", "--wide", "--dyn-syms", str(_loaded_native_path())],
        capture_output=True,
        text=True,
        check=True,
    )
    exports = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 8 and fields[4] in {"GLOBAL", "WEAK"} and fields[6] != "UND":
            exports.add(fields[7].partition("@")[0])

    assert exports == PUBLIC_ABI_SYMBOLS


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin export inspection")
def test_darwin_library_exports_only_the_five_grey_abi_symbols(
    built_native_release: Path,
) -> None:
    result = subprocess.run(
        ["nm", "-gU", str(_loaded_native_path())],
        capture_output=True,
        text=True,
        check=True,
    )
    exports = {fields[-1].removeprefix("_") for line in result.stdout.splitlines() if (fields := line.split())}

    assert exports == PUBLIC_ABI_SYMBOLS
