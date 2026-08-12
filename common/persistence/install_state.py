"""Durable OS, setup-wizard, and updater installation state."""

import json
from typing import cast

from common import datastore

_OS_INFO_KEY = "system:os_info"
_WIZARD_INSTALL_KEY = "wizard:install"
_WIZARD_STATUS_PREFIX = "wizard"
_UPDATER_STATUS_PREFIX = "updater"


def _read_json_key_or_none(key):
    raw = datastore.get_blob(key)
    return json.loads(raw) if raw is not None else None


def _get_install_status(prefix):
    return (
        _read_json_key_or_none(f"{prefix}:percent"),
        _read_json_key_or_none(f"{prefix}:status"),
        _read_json_key_or_none(f"{prefix}:output"),
    )


def _set_install_status(prefix, percent, status, output):
    datastore.set_blob(f"{prefix}:percent", json.dumps(percent))
    datastore.set_blob(f"{prefix}:status", json.dumps(status))
    datastore.set_blob(f"{prefix}:output", json.dumps(output))


def store_os_info(os_info):
    """Cache the probed OS and architecture information."""
    datastore.set_blob(_OS_INFO_KEY, json.dumps(os_info))


def load_os_info():
    """Return cached OS information, or a fresh empty mapping when absent."""
    return _read_json_key_or_none(_OS_INFO_KEY) or {}


def load_wizard_install_info():
    """Load the wizard's unvalidated installation draft."""
    return json.loads(cast(str, datastore.get_blob(_WIZARD_INSTALL_KEY)))


def store_wizard_install_info(wizard_install_info):
    """Store the wizard installation draft verbatim as JSON."""
    datastore.set_blob(_WIZARD_INSTALL_KEY, json.dumps(wizard_install_info))


def delete_wizard_install_info():
    """Delete the wizard installation draft when one exists."""
    datastore.delete_blob(_WIZARD_INSTALL_KEY)


def get_wizard_install_status():
    """Return wizard installation percent, status, and output values."""
    return _get_install_status(_WIZARD_STATUS_PREFIX)


def set_wizard_install_status(percent, status, output):
    """Store wizard installation percent, status, and output values."""
    _set_install_status(_WIZARD_STATUS_PREFIX, percent, status, output)


def get_updater_install_status():
    """Return updater installation percent, status, and output values."""
    return _get_install_status(_UPDATER_STATUS_PREFIX)


def set_updater_install_status(percent, status, output):
    """Store updater installation percent, status, and output values."""
    _set_install_status(_UPDATER_STATUS_PREFIX, percent, status, output)
