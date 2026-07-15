"""Tests for wizard/ds18b20.sh's reboot-required sentinel and idempotency.

Runs the real script via bash, but with `sudo` stubbed out on PATH -- so the real
`raspi-config` (a real system config change) is never invoked regardless of what's
installed on the test host -- and with the config.txt path pointed at a scratch file
via PIFIRE_CONFIG_TXT, so /boot is never touched.
"""

import os
import pathlib
import stat
import subprocess

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "wizard" / "ds18b20.sh"


@pytest.fixture
def fake_sudo_bin(tmp_path):
    """A directory containing a fake `sudo` that logs its args and exits 0 -- never
    execs anything for real. Prepending this to PATH means bash resolves `sudo` to
    this stub before it ever finds the real /usr/bin/sudo."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    sudo_log = tmp_path / "sudo-calls.log"
    fake_sudo = bin_dir / "sudo"
    fake_sudo.write_text(f'#!/bin/bash\necho "$*" >> "{sudo_log}"\nexit 0\n')
    fake_sudo.chmod(fake_sudo.stat().st_mode | stat.S_IEXEC)
    return bin_dir, sudo_log


def _run_script(config_txt_path, fake_sudo_bin):
    bin_dir, _ = fake_sudo_bin
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["PIFIRE_CONFIG_TXT"] = str(config_txt_path)
    # Intentional real-process integration test: runs the actual bash script (with
    # sudo stubbed and /boot redirected above) to verify real script behavior.
    return subprocess.run(["bash", str(_SCRIPT)], env=env, capture_output=True, text=True, timeout=10)


def test_enables_onewire_when_not_yet_configured(tmp_path, fake_sudo_bin):
    config_txt = tmp_path / "config.txt"
    config_txt.write_text("# empty config\n")
    _, sudo_log = fake_sudo_bin

    result = _run_script(config_txt, fake_sudo_bin)

    assert "REBOOT_REQUIRED=true" in result.stdout
    assert sudo_log.read_text().strip() == "raspi-config nonint do_onewire 0"


def test_is_a_noop_when_already_configured(tmp_path, fake_sudo_bin):
    config_txt = tmp_path / "config.txt"
    config_txt.write_text("dtoverlay=w1-gpio\n")
    _, sudo_log = fake_sudo_bin

    result = _run_script(config_txt, fake_sudo_bin)

    assert "REBOOT_REQUIRED=false" in result.stdout
    assert not sudo_log.exists()


def test_commented_out_overlay_is_treated_as_not_configured(tmp_path, fake_sudo_bin):
    config_txt = tmp_path / "config.txt"
    config_txt.write_text("#dtoverlay=w1-gpio\n")
    _, sudo_log = fake_sudo_bin

    result = _run_script(config_txt, fake_sudo_bin)

    assert "REBOOT_REQUIRED=true" in result.stdout
    assert sudo_log.read_text().strip() == "raspi-config nonint do_onewire 0"
