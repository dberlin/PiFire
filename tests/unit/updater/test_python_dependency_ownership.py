from __future__ import annotations

import json
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[3]
FRESH_INSTALLERS = (
    ROOT / "auto-install/install.sh",
    ROOT / "auto-install/install-debian.sh",
    ROOT / "auto-install/install-fedora.sh",
    ROOT / "auto-install/pifire-dietpi.sh",
)
VENV_CREATORS = (*FRESH_INSTALLERS, ROOT / "updater/upgrade.sh")
ALLOWED_SYSTEM_PYTHON_PACKAGES = {"python3", "python3-dev", "python3-devel"}
RPI_LGPIO_DEPENDENCY = "rpi-lgpio>=0.6; platform_system == 'Linux' and platform_machine == 'aarch64'"


def _manifest() -> dict:
    return json.loads((ROOT / "wizard/wizard_manifest.json").read_text())


def _updater_manifest() -> dict:
    return json.loads((ROOT / "updater/updater_manifest.json").read_text())


def _apt_dependencies(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "apt_dependencies":
                yield from child
            else:
                yield from _apt_dependencies(child)
    elif isinstance(value, list):
        for child in value:
            yield from _apt_dependencies(child)


def test_shared_scipy_is_a_production_project_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert any(dependency.startswith("scipy>=1.18.0") for dependency in project["dependencies"])


def test_raspberry_pi_profiles_install_lgpio_in_the_venv() -> None:
    profiles = _manifest()["modules"]["grillplatform"]
    raspberry = [entry for entry in profiles.values() if entry["filename"] == "raspberry_pi_all"]
    other = [entry for entry in profiles.values() if entry["filename"] != "raspberry_pi_all"]

    assert raspberry
    assert all(RPI_LGPIO_DEPENDENCY in entry["py_dependencies"] for entry in raspberry)
    assert all(not any("lgpio" in dependency for dependency in entry["py_dependencies"]) for entry in other)


def test_installers_do_not_install_system_python_runtime_packages() -> None:
    package_pattern = re.compile(r"\bpython3(?:-[a-z0-9][a-z0-9.+-]*)?\b")

    for installer in FRESH_INSTALLERS:
        packages = set(package_pattern.findall(installer.read_text().lower()))
        assert packages <= ALLOWED_SYSTEM_PYTHON_PACKAGES, f"{installer}: {sorted(packages)}"


def test_manifests_do_not_install_system_python_runtime_packages() -> None:
    for manifest in (_manifest(), _updater_manifest()):
        assert all(not dependency.startswith("python3-") for dependency in _apt_dependencies(manifest))


def test_venvs_do_not_expose_system_site_packages() -> None:
    for script in VENV_CREATORS:
        assert "--system-site-packages" not in script.read_text(), script


def test_legacy_rpi_gpio_install_paths_are_removed() -> None:
    installer = (ROOT / "auto-install/install.sh").read_text().lower()
    commands = json.dumps(_manifest()["modules"])

    assert "uv pip install rpi.gpio" not in installer
    assert "raspi5.sh" not in commands
    assert not (ROOT / "wizard/raspi5.sh").exists()


def test_current_update_runs_the_dependency_ownership_migration() -> None:
    manifest = _updater_manifest()
    current = manifest["metadata"]["versions"]
    migration = next(
        entry
        for entry in manifest["versions"]
        if entry["version"] == current["server"] and entry["build"] == current["build"]
    )
    commands = [command for section in migration["dependencies"].values() for command in section["command_list"]]

    assert current["build"] == 116
    assert ["bash", "/usr/local/bin/pifire/updater/upgrade.sh"] in commands
    all_commands = [
        command
        for entry in manifest["versions"]
        for section in entry["dependencies"].values()
        for command in section["command_list"]
    ]
    assert all_commands.count(["bash", "/usr/local/bin/pifire/updater/upgrade.sh"]) == 1


def test_upgrade_rebuilds_legacy_system_site_venv_before_wizard_replay() -> None:
    upgrade = (ROOT / "updater/upgrade.sh").read_text()

    assert "include-system-site-packages = true" in upgrade
    assert "VENV_ARGS=(--clear)" in upgrade
    assert 'uv venv "${VENV_ARGS[@]}"' in upgrade
    assert re.search(
        r"if ! \(\s*set -o pipefail\s*python wizard\.py --existing .*\| tee -a .*\s*\); then",
        upgrade,
    )
