from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import controller.acados._library as library_module
from controller.acados import _ffi
from controller.acados._library import load_native

REBUILD_COMMAND = "./rebuild-acados.sh --if-needed"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
PUBLIC_ABI_SYMBOLS = {
    "acados_pifire_abi_version",
    "acados_pifire_grey_create",
    "acados_pifire_grey_destroy",
    "acados_pifire_grey_reset",
    "acados_pifire_grey_solve",
}


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
    build_digest = "a" * 64
    release = runtime_root / "releases" / build_digest
    release.mkdir(parents=True)
    package.mkdir(parents=True)

    library_path = release / library_module._library_filename()
    library_path.write_bytes(b"unit-test native library payload")
    manifest_path = release / "build-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "build_digest": build_digest,
                "library_sha256": hashlib.sha256(library_path.read_bytes()).hexdigest(),
            }
        )
        + "\n"
    )
    (runtime_root / "current").symlink_to(release, target_is_directory=True)

    monkeypatch.setattr(library_module, "__file__", str(package / "_library.py"))
    _ffi.load_grey_api.cache_clear()
    load_native.cache_clear()
    yield runtime_root, library_path, manifest_path
    _ffi.load_grey_api.cache_clear()
    load_native.cache_clear()


@pytest.fixture
def built_native_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    built_library = PROJECT_ROOT / "build" / "acados-configure" / "native-output" / library_module._library_filename()
    if not built_library.is_file():
        pytest.fail(
            f"Built native library is missing at {built_library}. "
            "Run `cmake --build build/acados-configure -j2 "
            "--target acados_pifire` before this focused gate.",
            pytrace=False,
        )

    package = tmp_path / "checkout" / "controller" / "acados"
    package.mkdir(parents=True)
    with built_library.open("rb") as stream:
        library_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    build_digest = hashlib.sha256(library_digest.encode("ascii")).hexdigest()
    release = package.parent / "_native" / "releases" / build_digest
    release.mkdir(parents=True)
    library_path = release / library_module._library_filename()
    shutil.copy2(built_library, library_path)
    (release / "build-manifest.json").write_text(
        json.dumps(
            {
                "build_digest": build_digest,
                "library_sha256": library_digest,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (release.parent.parent / "current").symlink_to(release, target_is_directory=True)

    monkeypatch.setattr(library_module, "__file__", str(package / "_library.py"))
    _ffi.load_grey_api.cache_clear()
    load_native.cache_clear()
    yield library_path
    _ffi.load_grey_api.cache_clear()
    load_native.cache_clear()


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
