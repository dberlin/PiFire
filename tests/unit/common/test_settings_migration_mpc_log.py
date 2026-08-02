"""Step 2 of the shape migrations: the MPC calibration log moves under ./logs.

It used to be written to ./controller/mpc_calibration_log.csv -- inside the
source tree, beside the module that writes it, where nothing that collects or
rotates logs would find it. The default moved; this step carries the installs
that already stored the old one in their settings.
"""

import json
from pathlib import Path

from common.settings_migration import (
    _LEGACY_MPC_LOG_PATH,
    _MPC_LOG_PATH,
    _migrate_mpc_log_path,
)

CONTROLLERS_JSON = Path(__file__).resolve().parents[3] / "controller" / "controllers.json"


def _settings(log_path):
    return {"controller": {"selected": "mpc", "config": {"mpc": {"log_path": log_path}}}}


def test_the_superseded_default_is_rewritten():
    settings = _settings(_LEGACY_MPC_LOG_PATH)

    assert _migrate_mpc_log_path(settings) is True
    assert settings["controller"]["config"]["mpc"]["log_path"] == _MPC_LOG_PATH


def test_a_path_the_operator_chose_is_left_alone():
    # The whole point of the option is that it can be pointed elsewhere; a
    # migration that "fixed" a chosen path would lose the operator's file.
    settings = _settings("/mnt/usb/calibration.csv")

    assert _migrate_mpc_log_path(settings) is False
    assert settings["controller"]["config"]["mpc"]["log_path"] == "/mnt/usb/calibration.csv"


def test_it_is_idempotent():
    settings = _settings(_MPC_LOG_PATH)

    assert _migrate_mpc_log_path(settings) is False
    assert settings["controller"]["config"]["mpc"]["log_path"] == _MPC_LOG_PATH


def test_a_tree_with_no_mpc_config_is_untouched():
    # Every install that has never selected MPC, and every partial tree the
    # migration runner hands over mid-upgrade.
    for settings in (
        {},
        {"controller": {}},
        {"controller": {"config": {}}},
        {"controller": {"config": {"pid": {"PB": 60.0}}}},
        {"controller": {"config": {"mpc": "not a dict"}}},
        {"controller": {"config": {"mpc": {}}}},
    ):
        assert _migrate_mpc_log_path(settings) is False


def test_the_manifest_declares_the_new_default():
    # The migration only reaches trees that already exist. A fresh install
    # takes its value from the manifest, so the two have to agree or a new
    # grill would write where the old one did.
    manifest = json.loads(CONTROLLERS_JSON.read_text())
    options = manifest["metadata"]["mpc"]["config"]
    declared = next(o for o in options if o["option_name"] == "log_path")

    assert declared["option_default"] == _MPC_LOG_PATH


def test_the_module_default_agrees_with_the_manifest():
    from controller.mpc import _DEFAULTS

    assert _DEFAULTS["log_path"] == _MPC_LOG_PATH
