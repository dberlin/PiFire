"""Smoke tests for the common-package split (Phase A, Task 9).

After the hard split, every public name lives in a dedicated submodule and
common.common no longer re-exports the moved names (the temporary facade, and
common/__init__.py's ``from common.common import *``, are both gone).
"""

import importlib


NEW_MODULES = (
    "common.common",
    "common.defaults",
    "common.system",
    "common.datastore_accessors",
    "common.api_commands",
    "common.settings_migration",
    "common.backups",
)


def test_new_modules_import_standalone():
    for mod in NEW_MODULES:
        importlib.import_module(mod)


def test_public_names_resolve_from_new_homes():
    from common.api_commands import process_command  # noqa: F401
    from common.datastore_accessors import (  # noqa: F401
        read_control,
        write_control,
        read_settings,
        read_probe_status,
    )
    from common.defaults import default_settings, default_control  # noqa: F401
    from common.system import is_real_hardware, get_wifi_quality  # noqa: F401
    from common.settings_migration import read_settings_file, upgrade_settings  # noqa: F401
    from common.backups import backup_settings, read_pellet_db_file  # noqa: F401


def test_common_common_no_longer_re_exports_moved_names():
    """The temporary facade is gone: moved names must NOT resolve as attributes
    of common.common anymore."""
    import common.common as c

    for name in (
        "process_command",
        "read_control",
        "write_control",
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

    for name in ("process_command", "read_control", "default_settings", "write_log", "WriteKind"):
        assert not hasattr(common, name), f"common package still re-exports {name!r}"


def test_residual_utilities_still_live_in_common_common():
    """The bottom utility layer stayed in common.common."""
    from common.common import (  # noqa: F401
        WriteKind,
        write_log,
        read_generic_json,
        write_generic_json,
        generate_uuid,
        deep_update,
        create_logger,
    )
