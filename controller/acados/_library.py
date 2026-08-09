"""Discovery and integrity validation for the published acados runtime."""

from __future__ import annotations

import ctypes
from functools import cache
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

_EXPECTED_ABI_VERSION = 2
_REBUILD_COMMAND = "./rebuild-acados.sh --if-needed"
_MANIFEST_FILENAME = "build-manifest.json"


def _library_filename() -> str:
    if sys.platform == "darwin":
        return "libacados_pifire.dylib"
    if sys.platform.startswith("linux"):
        return "libacados_pifire.so"
    raise RuntimeError(
        f"Unsupported platform for acados-pifire: {sys.platform}. "
        f"Run `{_REBUILD_COMMAND}` on a supported target."
    )


def _failure(message: str) -> RuntimeError:
    return RuntimeError(f"{message} Run `{_REBUILD_COMMAND}`.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_paths() -> tuple[Path, Path]:
    runtime_root = Path(__file__).resolve().parent.parent / "_native"
    selector = runtime_root / "current"
    try:
        release = selector.resolve(strict=True)
        releases_root = (runtime_root / "releases").resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise _failure(
            f"Native runtime selector is missing or invalid at {selector}."
        ) from error
    if not release.is_dir():
        raise _failure(f"Native runtime selector {selector} does not select a release.")
    if release.parent != releases_root:
        raise _failure(
            f"Native runtime selector {selector} does not select an immutable release."
        )
    return release / _library_filename(), release / _MANIFEST_FILENAME


def _validated_library_path() -> Path:
    library_path, manifest_path = _release_paths()
    release = library_path.parent
    if manifest_path.is_symlink():
        raise _failure(
            f"Native build manifest at {manifest_path} is not owned by the "
            "selected immutable release."
        )
    if library_path.is_symlink():
        raise _failure(
            f"Native library at {library_path} is not owned by the selected "
            "immutable release."
        )
    if not manifest_path.is_file():
        raise _failure(f"Native build manifest is missing at {manifest_path}.")
    if not library_path.is_file():
        raise _failure(f"Native library is missing at {library_path}.")

    try:
        manifest_path = manifest_path.resolve(strict=True)
        library_path = library_path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise _failure("Native release artifacts are missing or invalid.") from error
    if manifest_path.parent != release or library_path.parent != release:
        raise _failure(
            "Native release artifacts are not owned by the selected immutable "
            "release."
        )

    try:
        manifest: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _failure(
            f"Native build manifest at {manifest_path} is malformed."
        ) from error
    if not isinstance(manifest, dict):
        raise _failure(f"Native build manifest at {manifest_path} is malformed.")

    build_digest = manifest.get("build_digest")
    library_sha256 = manifest.get("library_sha256")
    if not isinstance(build_digest, str) or not isinstance(library_sha256, str):
        raise _failure(
            f"Native build manifest at {manifest_path} is missing digest fields."
        )
    if len(build_digest) != 64 or any(
        character not in "0123456789abcdef" for character in build_digest
    ):
        raise _failure(
            f"Native build manifest at {manifest_path} has an invalid digest."
        )
    if len(library_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in library_sha256
    ):
        raise _failure(
            f"Native build manifest at {manifest_path} has an invalid digest."
        )
    if manifest_path.parent.name != build_digest:
        raise _failure(
            "Native build manifest digest does not match the selected release "
            f"{manifest_path.parent.name}."
        )

    try:
        actual_library_sha256 = _sha256(library_path)
    except OSError as error:
        raise _failure(
            f"Native library at {library_path} could not be read."
        ) from error
    if actual_library_sha256 != library_sha256:
        raise _failure(f"Native library digest does not match {manifest_path}.")
    return library_path


@cache
def load_native() -> ctypes.CDLL:
    """Load one digest-validated ABI-v2 release through the atomic selector."""
    library_path = _validated_library_path()
    try:
        library = ctypes.CDLL(str(library_path))
    except OSError as error:
        raise _failure(
            f"Failed to load acados-pifire native library at {library_path}: {error}."
        ) from error

    try:
        abi_version = library.acados_pifire_abi_version
    except AttributeError as error:
        raise _failure(
            f"Native library at {library_path} does not expose ABI version "
            f"{_EXPECTED_ABI_VERSION}."
        ) from error
    try:
        abi_version.argtypes = []
        abi_version.restype = ctypes.c_int
        found_version = int(abi_version())
    except Exception as error:
        raise _failure(
            f"Native library ABI query failed at {library_path}: {error}."
        ) from error
    if found_version != _EXPECTED_ABI_VERSION:
        raise _failure(
            f"Native library ABI mismatch at {library_path}: expected "
            f"{_EXPECTED_ABI_VERSION}, found {found_version}."
        )
    return library
