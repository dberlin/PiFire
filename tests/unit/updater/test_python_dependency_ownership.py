from __future__ import annotations

import json
import pathlib
import re
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
FRESH_INSTALLERS = (
    ROOT / "auto-install/install.sh",
    ROOT / "auto-install/install-debian.sh",
    ROOT / "auto-install/install-fedora.sh",
    ROOT / "auto-install/pifire-dietpi.sh",
)
VENV_CREATORS = FRESH_INSTALLERS
ALLOWED_SYSTEM_PYTHON_PACKAGES = {"python3", "python3-dev", "python3-devel"}
RPI_LGPIO_DEPENDENCY = "rpi-lgpio>=0.6; platform_system == 'Linux' and platform_machine == 'aarch64'"
LINUX_LGPIO_DEPENDENCY = "lgpio>=0.2.2.0; platform_system == 'Linux'"


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


def test_linux_blinka_profiles_install_platform_lgpio_in_the_venv() -> None:
    profiles = _manifest()["modules"]["grillplatform"]
    raspberry = [entry for entry in profiles.values() if entry["filename"] == "raspberry_pi_all"]
    generic_linux = [entry for entry in profiles.values() if entry["filename"] != "raspberry_pi_all"]

    assert raspberry
    assert generic_linux
    assert all(RPI_LGPIO_DEPENDENCY in entry["py_dependencies"] for entry in raspberry)
    assert all(LINUX_LGPIO_DEPENDENCY not in entry["py_dependencies"] for entry in raspberry)
    assert all(LINUX_LGPIO_DEPENDENCY in entry["py_dependencies"] for entry in generic_linux)
    assert all(RPI_LGPIO_DEPENDENCY not in entry["py_dependencies"] for entry in generic_linux)


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


def test_current_update_bootstraps_the_new_platform_neutral_updater() -> None:
    manifest = _updater_manifest()
    current = manifest["metadata"]["versions"]
    migration = next(
        entry
        for entry in manifest["versions"]
        if entry["version"] == current["server"] and entry["build"] == current["build"]
    )
    commands = [command for section in migration["dependencies"].values() for command in section["command_list"]]

    assert current["build"] == 119
    assert commands == [
        [
            "python",
            "/usr/local/bin/pifire/updater.py",
            "--bootstrap-uv",
            "--legacy-python-token",
            "sudo",
        ],
        [
            "python",
            "/usr/local/bin/pifire/updater.py",
            "--refresh-python-environment",
            "--previous-wizard-ref",
            "v1.22.2",
            "--legacy-python-token",
            "sudo",
        ],
    ]
    for command in commands:
        assert "sudo" in command
        rewritten = ["bin/python" if item == "python" else item for item in command]
        assert rewritten[0] == "bin/python"


def test_platform_specific_upgrade_script_is_removed() -> None:
    assert not (ROOT / "updater/upgrade.sh").exists()


def _module(dependency: str) -> dict:
    return {
        "settings_dependencies": {},
        "py_dependencies": [dependency],
        "apt_dependencies": ["existing-os-package"],
        "command_list": [["existing-command"]],
    }


def _existing_hardware() -> tuple[dict, dict]:
    settings = {
        "globals": {"units": "F", "uv": False, "venv": True, "python_exec": "bin/python"},
        "platform": {"current": "generic"},
        "modules": {"display": "display", "dist": "distance"},
        "display": {"config": {"display": {}}},
        "probe_settings": {"probe_map": {"probe_devices": [{"module": "probe"}]}},
    }
    wizard_data = {
        "modules": {
            "grillplatform": {"generic": _module(LINUX_LGPIO_DEPENDENCY)},
            "display": {"display": _module("display-extra>=1")},
            "distance": {"distance": _module("distance-extra>=2")},
            "probes": {"probe": _module("probe-extra>=3")},
        }
    }
    return settings, wizard_data


def test_updater_refreshes_exact_venv_and_selected_wizard_python_dependencies(monkeypatch) -> None:
    import updater

    settings, wizard_data = _existing_hardware()
    commands = []
    writes = []

    def run(command):
        commands.append(command)
        return 0

    monkeypatch.setattr(updater, "write_settings", lambda value: writes.append(value))

    result, manual_actions = updater.refresh_python_environment(
        settings=settings,
        wizard_data=wizard_data,
        runner=run,
    )

    assert result == 0
    assert manual_actions == ()
    assert commands == [
        ["uv", "venv", "--allow-existing", ".venv"],
        ["uv", "sync", "--no-dev"],
        [
            "uv",
            "pip",
            "install",
            "--python",
            ".venv/bin/python",
            LINUX_LGPIO_DEPENDENCY,
        ],
        ["uv", "pip", "install", "--python", ".venv/bin/python", "display-extra>=1"],
        ["uv", "pip", "install", "--python", ".venv/bin/python", "distance-extra>=2"],
        ["uv", "pip", "install", "--python", ".venv/bin/python", "probe-extra>=3"],
    ]
    assert all(command[0] == "uv" for command in commands)
    assert settings["globals"] == {
        "units": "F",
        "uv": True,
        "venv": True,
        "python_exec": ".venv/bin/python",
    }
    assert writes == [settings]


def test_updater_clears_a_legacy_system_site_environment(tmp_path, monkeypatch) -> None:
    import updater

    settings, wizard_data = _existing_hardware()
    config = tmp_path / ".venv" / "pyvenv.cfg"
    config.parent.mkdir()
    config.write_text("include-system-site-packages = true\n")
    commands = []
    monkeypatch.setattr(updater, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(updater, "write_settings", lambda value: None)

    result, _manual_actions = updater.refresh_python_environment(
        settings=settings,
        wizard_data=wizard_data,
        runner=lambda command: commands.append(command) or 0,
    )

    assert result == 0
    assert commands[0] == ["uv", "venv", "--clear", ".venv"]


def test_updater_stops_before_wizard_dependencies_when_uv_sync_fails(monkeypatch) -> None:
    import updater

    settings, wizard_data = _existing_hardware()
    commands = []
    codes = iter((0, 23))

    def run(command):
        commands.append(command)
        return next(codes)

    monkeypatch.setattr(
        updater,
        "write_settings",
        lambda value: raise_unexpected_write(),
    )

    result, manual_actions = updater.refresh_python_environment(
        settings=settings,
        wizard_data=wizard_data,
        runner=run,
    )

    assert result == 23
    assert manual_actions == ()
    assert commands == [
        ["uv", "venv", "--allow-existing", ".venv"],
        ["uv", "sync", "--no-dev"],
    ]


def raise_unexpected_write() -> None:
    raise AssertionError("failed environment refresh must not update settings")


def test_failed_environment_refresh_does_not_advance_migration_cursor(monkeypatch) -> None:
    import updater

    monkeypatch.setattr(updater, "read_updater_manifest", lambda: {"versions": []})
    monkeypatch.setattr(updater, "_run_acados_bootstrap_migrations", lambda *args: 0)
    monkeypatch.setattr(updater, "DEBUG", False, raising=False)
    monkeypatch.setattr(updater, "refresh_python_environment", lambda **kwargs: (29, ()))
    monkeypatch.setattr(
        updater,
        "record_installed_version",
        lambda manifest: raise_unexpected_cursor_advance(),
    )

    result, reboot, manual_actions = updater.install_dependencies("1.22.1", 117)

    assert result == 29
    assert reboot is False
    assert manual_actions == ()


def raise_unexpected_cursor_advance() -> None:
    raise AssertionError("failed environment refresh must leave the migration cursor pending")


def test_new_updater_does_not_recursively_run_its_old_process_bootstrap(monkeypatch) -> None:
    import updater

    manifest = _updater_manifest()
    monkeypatch.setattr(updater, "DEBUG", False, raising=False)
    monkeypatch.setattr(updater, "read_updater_manifest", lambda: manifest)
    monkeypatch.setattr(updater, "_run_acados_bootstrap_migrations", lambda *args: 0)
    monkeypatch.setattr(updater, "refresh_python_environment", lambda **kwargs: (0, ()))
    monkeypatch.setattr(updater, "read_settings", lambda: {"globals": {"uv": True}})
    monkeypatch.setattr(updater, "record_installed_version", lambda value: None)
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        updater.subprocess,
        "Popen",
        lambda *args, **kwargs: raise_unexpected_bootstrap(),
    )

    result, reboot, manual_actions = updater.install_dependencies("1.22.1", 117)

    assert result == 0
    assert reboot is False
    assert manual_actions == ()


def raise_unexpected_bootstrap() -> None:
    raise AssertionError("new updater must not invoke its old-process bootstrap command")


def test_updater_flags_only_new_os_packages_and_commands(monkeypatch) -> None:
    import updater

    settings, wizard_data = _existing_hardware()
    wizard_data["modules"]["distance"]["distance"]["apt_dependencies"].append("new-distance-package")
    wizard_data["modules"]["probes"]["probe"]["command_list"].append(["new-probe-setup", "--enable"])
    previous = updater.WizardDependencies(
        python=(),
        apt=("existing-os-package",),
        commands=(("existing-command",),),
    )
    monkeypatch.setattr(updater, "write_settings", lambda value: None)

    result, manual_actions = updater.refresh_python_environment(
        settings=settings,
        wizard_data=wizard_data,
        previous_dependencies=previous,
        runner=lambda command: 0,
    )

    assert result == 0
    assert manual_actions == (
        "Install OS package: new-distance-package",
        "Run command: new-probe-setup --enable",
    )


def test_install_dependencies_returns_manual_actions_to_the_restart_owner(monkeypatch) -> None:
    import updater

    actions = ("Install OS package: libusb",)
    monkeypatch.setattr(updater, "DEBUG", False, raising=False)
    monkeypatch.setattr(updater, "read_updater_manifest", lambda: {"versions": []})
    monkeypatch.setattr(updater, "_run_acados_bootstrap_migrations", lambda *args: 0)
    monkeypatch.setattr(updater, "refresh_python_environment", lambda **kwargs: (0, actions))
    monkeypatch.setattr(updater, "read_settings", lambda: {"globals": {"uv": True}})
    monkeypatch.setattr(updater, "record_installed_version", lambda value: None)
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: None)

    result, reboot, manual_actions = updater.install_dependencies("1.22.2", 118)

    assert result == 0
    assert reboot is False
    assert manual_actions == actions


def test_bootstrap_compares_selected_dependencies_with_the_previous_release(monkeypatch) -> None:
    import updater

    settings, wizard_data = _existing_hardware()
    previous_wizard_data = json.loads(json.dumps(wizard_data))
    wizard_data["modules"]["distance"]["distance"]["apt_dependencies"].append("new-distance-package")
    wizard_data["modules"]["probes"]["probe"]["command_list"].append(["new-probe-setup", "--enable"])
    git_commands = []

    class GitResult:
        returncode = 0
        stdout = json.dumps(previous_wizard_data)
        stderr = ""

    def git_runner(command, **kwargs):
        git_commands.append(command)
        return GitResult()

    monkeypatch.setattr(updater, "write_settings", lambda value: None)

    result, manual_actions = updater.refresh_python_environment_from_revision(
        "v1.22.2",
        settings=settings,
        wizard_data=wizard_data,
        runner=lambda command: 0,
        git_runner=git_runner,
    )

    assert result == 0
    assert git_commands == [["git", "show", "v1.22.2:wizard/wizard_manifest.json"]]
    assert manual_actions == (
        "Install OS package: new-distance-package",
        "Run command: new-probe-setup --enable",
    )


def test_bootstrap_child_advances_cursor_after_successful_refresh(monkeypatch) -> None:
    import updater

    recorded = []
    monkeypatch.setattr(
        updater,
        "refresh_python_environment_from_revision",
        lambda revision: (0, ()),
    )
    monkeypatch.setattr(updater, "record_installed_version", lambda: recorded.append(True))

    result, manual_actions = updater.run_python_environment_bootstrap("v1.22.2")

    assert result == 0
    assert manual_actions == ()
    assert recorded == [True]


def test_bootstrap_child_blocks_old_parent_restart_when_manual_actions_appear(monkeypatch, capsys) -> None:
    import updater

    actions = ("Install OS package: libusb",)
    persisted = []
    restart_pending = []
    monkeypatch.setattr(
        updater,
        "refresh_python_environment_from_revision",
        lambda revision: (0, actions),
    )
    monkeypatch.setattr(updater, "record_installed_version", lambda: None)
    monkeypatch.setattr(updater, "set_update_manual_dependency_actions", persisted.append)
    monkeypatch.setattr(updater, "set_update_restart_pending", restart_pending.append)
    monkeypatch.setattr(updater, "set_updater_install_status", lambda *args: None)

    result, manual_actions = updater.run_python_environment_bootstrap("v1.22.2")

    assert result == updater.MANUAL_DEPENDENCY_ACTIONS_EXIT
    assert manual_actions == actions
    assert persisted == [actions]
    assert restart_pending == [True]
    assert "Install OS package: libusb" in capsys.readouterr().out


def test_missing_uv_is_bootstrapped_outside_the_virtual_environment(tmp_path) -> None:
    import updater

    calls = []

    class Download:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"install uv"

    class Result:
        returncode = 0
        stderr = b""

    def run(command, **kwargs):
        calls.append((command, kwargs))
        install_dir = pathlib.Path(kwargs["env"]["UV_INSTALL_DIR"])
        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / "uv").write_text("binary")
        return Result()

    executable = updater.bootstrap_uv_executable(
        repo_root=tmp_path,
        which=lambda name: None,
        opener=lambda url: Download(),
        runner=run,
    )

    assert executable == str(tmp_path / ".toolchain" / "uv" / "uv")
    assert calls[0][0] == ["/bin/sh"]
    assert calls[0][1]["input"] == b"install uv"


def test_automatic_refresh_refuses_missing_uv_before_running_any_command(tmp_path, monkeypatch) -> None:
    import updater

    settings, wizard_data = _existing_hardware()
    commands = []
    monkeypatch.setattr(updater, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(updater.shutil, "which", lambda name: None)
    monkeypatch.setattr(updater, "write_settings", lambda value: None)

    with pytest.raises(RuntimeError, match="uv is not installed"):
        updater.refresh_python_environment(
            settings=settings,
            wizard_data=wizard_data,
            runner=lambda command: commands.append(command) or 0,
        )

    assert commands == []


def test_manual_action_exit_preserves_the_specific_bootstrap_status(monkeypatch) -> None:
    import updater

    monkeypatch.setattr(
        updater,
        "report_failure",
        lambda *args: raise_unexpected_generic_failure(),
    )

    with pytest.raises(SystemExit) as raised:
        updater.exit_after_python_environment_bootstrap(updater.MANUAL_DEPENDENCY_ACTIONS_EXIT)

    assert raised.value.code == updater.MANUAL_DEPENDENCY_ACTIONS_EXIT


def raise_unexpected_generic_failure() -> None:
    raise AssertionError("manual dependency exit must preserve its specific status")
