"""reboot_required is now determined dynamically per wizard run (see wizard.py's
_run_install_commands and board-config.py's REBOOT_REQUIRED sentinel) rather than
declared statically per module -- the old static flag in wizard_manifest.json is
unused and should not reappear."""

import json
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _find_reboot_required_paths(obj, path=()):
    found = []
    if isinstance(obj, dict):
        if "reboot_required" in obj:
            found.append(".".join(str(p) for p in path))
        for key, value in obj.items():
            found.extend(_find_reboot_required_paths(value, path + (key,)))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(_find_reboot_required_paths(value, path + (index,)))
    return found


def test_wizard_manifest_has_no_static_reboot_required_flags():
    with open(os.path.join(BASE, "wizard", "wizard_manifest.json")) as f:
        manifest = json.load(f)

    assert _find_reboot_required_paths(manifest) == []
