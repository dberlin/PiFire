"""Smoke tests for the common-package split.

After the hard split, every public name lives in a dedicated submodule and
common.common no longer re-exports the moved names (the temporary facade, and
common/__init__.py's ``from common.common import *``, are both gone).
"""

import importlib

NEW_MODULES = (
    "common.common",
    "common.defaults",
    "common.system",
    "common.persistence.protocols",
    "common.persistence.transforms",
    "common.persistence.control",
    "common.persistence.runtime",
    "common.persistence.history",
    "common.persistence.control_trace",
    "common.persistence.model_evidence",
    "common.persistence.install_state",
    "common.api_commands",
    "common.settings_migration",
    "common.backups",
)


def test_new_modules_import_standalone():
    for mod in NEW_MODULES:
        importlib.import_module(mod)


def test_public_names_resolve_from_new_homes():
    from common.api_commands import process_command  # noqa: F401
    from common.backups import backup_settings, read_pellet_db_file  # noqa: F401
    from common.defaults import default_control, default_settings  # noqa: F401
    from common.persistence.control import (
        enqueue_control_delta,
        read_control,
        write_control_snapshot,
    )
    from common.persistence.history import read_all_metrics, read_history  # noqa: F401
    from common.persistence.install_state import get_wizard_install_status, load_os_info  # noqa: F401
    from common.persistence.runtime import (
        read_probe_status,
        read_settings,
    )
    from common.settings_migration import read_settings_file, upgrade_settings  # noqa: F401
    from common.system import get_wifi_quality, is_real_hardware  # noqa: F401


def test_common_common_no_longer_re_exports_moved_names():
    """The temporary facade is gone: moved names must NOT resolve as attributes
    of common.common anymore."""
    import common.common as c

    for name in (
        "process_command",
        "read_control",
        "default_settings",
        "is_real_hardware",
        "read_settings_file",
        "backup_settings",
        "read_probe_status",
    ):
        assert not hasattr(c, name), f"common.common still re-exports moved name {name!r}"


def test_common_package_has_no_star_facade():
    """common/__init__.py must not re-export common.common's names."""
    import common

    for name in ("process_command", "read_control", "default_settings", "write_log"):
        assert not hasattr(common, name), f"common package still re-exports {name!r}"


def test_residual_utilities_still_live_in_common_common():
    """The bottom utility layer stayed in common.common."""
    from common.common import (  # noqa: F401
        create_logger,
        deep_update,
        generate_uuid,
        read_generic_json,
        write_generic_json,
        write_log,
    )
