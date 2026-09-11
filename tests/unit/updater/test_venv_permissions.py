from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PREPARE = ROOT / "updater" / "prepare_python_environment.py"


@pytest.mark.parametrize("entrypoint", ("display_launch.py", "display_process.py"))
def test_display_imports_and_children_leave_dependencies_read_only(tmp_path: Path, entrypoint: str) -> None:
    shutil.copyfile(ROOT / entrypoint, tmp_path / entrypoint)
    common = tmp_path / "common"
    common.mkdir()
    (tmp_path / "cache_probe.py").write_text("value = 42\n")
    # Stop after the entrypoint's first application import, exercising both
    # interpreter-local policy and the environment inherited by Python children.
    (common / "__init__.py").write_text(
        "import subprocess, sys\n"
        "import cache_probe\n"
        "subprocess.run([sys.executable, '-c', 'import cache_probe'], check=True)\n"
        "raise SystemExit(0)\n"
    )
    environment = dict(os.environ)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment.pop("PYTHONPYCACHEPREFIX", None)
    completed = subprocess.run(
        [sys.executable, str(tmp_path / entrypoint)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not (tmp_path / "__pycache__").exists()
    assert not (common / "__pycache__").exists()


def test_environment_repair_allows_cache_removal_without_touching_linked_files(tmp_path: Path) -> None:
    cache = tmp_path / ".venv" / "lib" / "python3.14" / "site-packages" / "rpi_backlight" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "backlight.pyc").write_bytes(b"old bytecode")
    shared = tmp_path / "uv-cache-file"
    shared.write_bytes(b"shared package data")
    shared.chmod(0o444)
    os.link(shared, cache.parent / "backlight.py")
    external = tmp_path / "external"
    external.mkdir()
    external.chmod(0o555)
    (cache.parent / "outside").symlink_to(external, target_is_directory=True)
    cache.chmod(0o555)
    try:
        completed = subprocess.run(
            [sys.executable, str(PREPARE), "--repo", str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert stat.S_IMODE(shared.stat().st_mode) == 0o444
        assert stat.S_IMODE(external.stat().st_mode) == 0o555
        shutil.rmtree(cache)
        assert not cache.exists()
    finally:
        external.chmod(0o755)
        if cache.exists():
            cache.chmod(0o755)


def test_environment_repair_refuses_symlinked_environment(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    external.chmod(0o555)
    (tmp_path / ".venv").symlink_to(external, target_is_directory=True)
    try:
        completed = subprocess.run(
            [sys.executable, str(PREPARE), "--repo", str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        assert "symbolic link" in completed.stderr
        assert stat.S_IMODE(external.stat().st_mode) == 0o555
    finally:
        external.chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses the permission failure that requests elevation")
def test_system_python_repair_uses_installed_bash_sudo_permission(tmp_path: Path) -> None:
    cache = tmp_path / ".venv" / "site-packages" / "package" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.pyc").write_bytes(b"bytecode")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    # Emulate the installed command allowlist, not blanket passwordless sudo.
    # Give the child traversal access as root would have; the production helper
    # must still restore write access before the updater can remove the cache.
    sudo.write_text(
        "#!/bin/sh\n"
        '[ "$1" = "-n" ] && [ "$2" = "bash" ] || exit 77\n'
        "shift\n"
        'chmod u+rx "$PIFIRE_TEST_CACHE"\n'
        'exec "$@"\n'
    )
    sudo.chmod(0o755)
    cache.chmod(0o000)
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PIFIRE_TEST_CACHE": str(cache)}
    try:
        completed = subprocess.run(
            [sys.executable, str(PREPARE), "--repo", str(tmp_path)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        shutil.rmtree(cache)
        assert not cache.exists()
    finally:
        if cache.exists():
            cache.chmod(0o755)


@pytest.mark.parametrize("symlinked", (False, True))
def test_installed_updater_prepares_permissions_before_sync_or_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, symlinked: bool
) -> None:
    import updater

    cache = tmp_path / "packages" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.pyc").write_bytes(b"bytecode")
    if symlinked:
        (tmp_path / ".venv").symlink_to(cache.parent, target_is_directory=True)
    else:
        cache.parent.rename(tmp_path / ".venv")
        cache = tmp_path / ".venv" / "__pycache__"
    cache.chmod(0o555)
    popen = subprocess.Popen
    synced = False

    def launch(command, **kwargs):
        return popen(
            [*command[:2], str(PREPARE), "--repo", str(tmp_path)],
            **kwargs,
        )

    def sync(**kwargs):
        nonlocal synced
        shutil.rmtree(cache)
        synced = True
        return 23, ()

    manifest = json.loads((ROOT / "updater/updater_manifest.json").read_text())
    monkeypatch.setattr(updater, "read_updater_manifest", lambda: manifest)
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    monkeypatch.setattr(updater, "_publish", lambda *args: None)
    monkeypatch.setattr(updater, "refresh_python_environment", sync)
    try:
        result, reboot, actions = updater.install_dependencies("1.30.0", 130)
        assert result == (1 if symlinked else 23)
        assert synced is not symlinked
        assert not reboot
        assert actions == ()
    finally:
        if cache.exists():
            cache.chmod(0o755)
