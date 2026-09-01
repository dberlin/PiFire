from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

import controller.acados._library as library_module
from controller.acados import _ffi


@pytest.fixture
def built_native_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    try:
        built_library = library_module._validated_library_path()  # pyright: ignore[reportPrivateUsage]
    except RuntimeError as error:
        pytest.fail(str(error), pytrace=False)
    source_release = built_library.parent
    release = tmp_path / "native-release"
    _ = shutil.copytree(source_release, release)
    isolated_library = release / built_library.name

    def isolated_library_path() -> Path:
        return isolated_library

    monkeypatch.setattr(library_module, "_validated_library_path", isolated_library_path)
    _ffi.load_grey_api.cache_clear()
    library_module.load_native.cache_clear()
    try:
        yield isolated_library
    finally:
        _ffi.load_grey_api.cache_clear()
        library_module.load_native.cache_clear()
