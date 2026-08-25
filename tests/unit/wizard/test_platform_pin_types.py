"""Platform pin values the wizard writes must satisfy the settings schema.

Reported against a fresh ft232h_relay install: picking that platform and
finishing produced

    SettingsValidationError: platform.outputs.auger: Input should be a valid
    integer; ...; platform.ft232h.url: Input should be a valid string

from write_settings(), inside the DETACHED installer -- which has no handler,
so the process died with the browser polling a status frozen at "Installing
Dependencies..." (percent 15). Nothing was wrong with the values: ft232h
addresses its pins by NAME (grillplat/ft232h.py:158) and its url is a string.
The schema modelled only the Raspberry Pi's integer BCM pins, and the wizard's
shape-based conversion turned the url option "1" into int 1.

subprocess is neutralized throughout; no installer ever runs.
"""

import logging

import pytest

import wizard
from common import defaults
from common.common import read_wizard
from common.persistence import install_state as install_persistence
from common.persistence import runtime as runtime_persistence


@pytest.fixture
def no_install(monkeypatch):
    monkeypatch.setattr(wizard, "logger", logging.getLogger("wizard_pin_test"), raising=False)
    monkeypatch.setattr(wizard, "is_real_hardware", lambda *a, **k: False)
    monkeypatch.setattr(wizard.time, "sleep", lambda *a, **k: None)

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(wizard.subprocess, "run", lambda *a, **k: _Result())
    monkeypatch.setattr(
        wizard.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("a real subprocess escaped the test harness"),
    )


def install_info(module, settings):
    """A wizardInstallInfo selecting one grillplatform module, shaped as
    /api/wizard/finish stores it. `current` is always present because the
    dependency-calculation loop indexes it directly (wizard.py:391)."""
    settings = {"current": module, **settings}
    return {
        "modules": {
            "grillplatform": {"profile_selected": [module], "settings": settings, "config": {}},
            "display": {"profile_selected": ["none"], "settings": {}, "config": {}},
            "distance": {"profile_selected": ["none"], "settings": {}, "config": {}},
            "probes": {"profile_selected": [], "settings": {}, "config": {}},
        },
        "probe_map": defaults.default_settings()["probe_settings"]["probe_map"],
    }


def run(info):
    runtime_persistence.write_settings_store(defaults.default_settings())
    wizard.run_wizard(runtime_persistence.read_settings(), read_wizard(), info)
    return runtime_persistence.read_settings()


def test_ft232h_install_writes_named_pins_and_a_string_url(ds, no_install):
    """The reported case, with the manifest's own option values."""
    settings = run(
        install_info(
            "ft232h_relay",
            {
                "current": "ft232h_relay",
                "system_type": "ft232h_relay",
                "ft232h_url": "1",
                "output_power": "C0",
                "output_igniter": "D5",
                "output_auger": "D4",
                "output_fan": "C3",
            },
        )
    )

    outputs = settings["platform"]["outputs"]
    assert (outputs["power"], outputs["igniter"], outputs["auger"], outputs["fan"]) == (
        "C0",
        "D5",
        "D4",
        "C3",
    )
    # A string, not int 1: canonical_url() takes '1' to mean the first FT232H,
    # and the field is declared str.
    assert settings["platform"]["ft232h"]["url"] == "1"

    percent, _, _ = install_persistence.get_wizard_install_status()
    assert percent == 101, "the install must run to completion, not die at 15%"


def test_a_pin_the_board_does_not_wire_is_written_as_none(ds, no_install):
    """Every board offers "None" for pins it does not use, and on several it is
    the ONLY option -- pcb_4.x.x's selector input among them. That wrote None
    into a field declared int and killed the same installer the same way."""
    settings = run(install_info("pcb_4.x.x", {"input_selector": "None"}))

    assert settings["platform"]["inputs"]["selector"] is None
    assert install_persistence.get_wizard_install_status()[0] == 101


def test_a_raspberry_pi_pin_is_still_written_as_an_integer(ds, no_install):
    """Widening the pin fields must not turn the Pi's BCM numbers into strings:
    the platform drivers index GPIO by int, and every existing install holds
    ints. '14' has to keep landing as 14."""
    settings = run(install_info("custom", {"output_auger": "14", "input_shutdown": "17"}))

    assert settings["platform"]["outputs"]["auger"] == 14
    assert settings["platform"]["inputs"]["shutdown"] == 17


def test_a_failing_install_is_published_rather_than_dying_silently(ds, no_install, monkeypatch):
    """The wizard runs detached, so an exception used to end the process with
    the status blob holding whatever line it had reached -- and the browser,
    which reads only "above 100" as finished, polled that forever."""
    from common.install_log import INSTALL_FAILED_PERCENT

    runtime_persistence.write_settings_store(defaults.default_settings())
    monkeypatch.setattr(wizard, "run_wizard", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))

    code = wizard.run_wizard_reporting_failure(
        runtime_persistence.read_settings(), read_wizard(), install_info("custom", {})
    )

    assert code == 1
    percent, status, output = install_persistence.get_wizard_install_status()
    assert percent == INSTALL_FAILED_PERCENT
    assert percent < 0, "the browser distinguishes failure from progress by sign"
    assert status == "Installation failed"
    assert "disk full" in output


def test_a_successful_install_reports_no_failure(ds, no_install):
    runtime_persistence.write_settings_store(defaults.default_settings())

    code = wizard.run_wizard_reporting_failure(
        runtime_persistence.read_settings(), read_wizard(), install_info("custom", {})
    )

    assert code == 0
    assert install_persistence.get_wizard_install_status()[0] == 101
