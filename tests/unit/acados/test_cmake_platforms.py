from __future__ import annotations

from pathlib import Path
import subprocess

import pytest


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_PLATFORM_MODULE = _REPOSITORY_ROOT / "cmake" / "AcadosPifirePlatform.cmake"


def _run_mapping(
    tmp_path: Path, system_name: str, processor: str, *, avx_available: bool = False
) -> subprocess.CompletedProcess[str]:
    output = tmp_path / "target.txt"
    driver = tmp_path / "platform-test.cmake"
    driver.write_text(
        'include("${PLATFORM_MODULE}")\n'
        'file(WRITE "${TARGET_OUTPUT}" '
        '"${ACADOS_PIFIRE_BLASFEO_TARGET}|${ACADOS_PIFIRE_HPIPM_TARGET}")\n'
    )
    return subprocess.run(
        [
            "cmake",
            f"-DPLATFORM_MODULE={_PLATFORM_MODULE}",
            f"-DTARGET_OUTPUT={output}",
            f"-DCMAKE_SYSTEM_NAME={system_name}",
            f"-DCMAKE_SYSTEM_PROCESSOR={processor}",
            f"-DACADOS_PIFIRE_AVX_AVAILABLE={'ON' if avx_available else 'OFF'}",
            "-P",
            str(driver),
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    (
        "system_name",
        "processor",
        "avx_available",
        "expected_blasfeo",
        "expected_hpipm",
    ),
    [
        ("Linux", "x86_64", True, "X64_AUTOMATIC", "AVX"),
        ("Linux", "x86_64", False, "X64_AUTOMATIC", "GENERIC"),
        ("Linux", "aarch64", True, "ARMV8A_ARM_CORTEX_A57", "GENERIC"),
        ("Darwin", "x86_64", True, "X64_AUTOMATIC", "AVX"),
        ("Darwin", "arm64", True, "ARMV8A_APPLE_M1", "GENERIC"),
    ],
)
def test_cmake_maps_supported_native_targets_exactly(
    tmp_path: Path,
    system_name: str,
    processor: str,
    avx_available: bool,
    expected_blasfeo: str,
    expected_hpipm: str,
) -> None:
    completed = _run_mapping(tmp_path, system_name, processor, avx_available=avx_available)
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "target.txt").read_text() == (f"{expected_blasfeo}|{expected_hpipm}")


@pytest.mark.parametrize(
    ("system_name", "processor", "expected_error"),
    [
        (
            "Linux",
            "riscv64",
            "Unsupported Linux processor for acados-pifire: riscv64",
        ),
        (
            "Darwin",
            "powerpc",
            "Unsupported Apple processor for acados-pifire: powerpc",
        ),
        (
            "Windows",
            "x86_64",
            "Unsupported host for acados-pifire: Windows/x86_64",
        ),
    ],
)
def test_cmake_rejects_unsupported_native_targets(
    tmp_path: Path,
    system_name: str,
    processor: str,
    expected_error: str,
) -> None:
    completed = _run_mapping(tmp_path, system_name, processor)
    assert completed.returncode != 0
    assert expected_error in completed.stderr
