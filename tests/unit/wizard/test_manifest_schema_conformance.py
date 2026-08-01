"""Every value the wizard manifest can offer must be writable.

The ft232h install failure was not a bad value -- it was a manifest option the
settings schema had no type for. That mismatch is invisible until someone picks
the option on real hardware, and it surfaces inside the DETACHED installer,
where an unhandled SettingsValidationError leaves the browser polling a frozen
status forever. The cost of finding it that way is a bricked setup run; the
cost of finding it here is a red test.

So: walk the manifest, take every option a user can pick, put it where the
manifest says it goes, and validate the resulting tree.
"""

import copy

import pytest

from common.common import read_wizard, set_nested_key_value
from common.defaults import default_settings
from common.settings_schema import (
    SettingsValidationError,
    coerce_setting_value,
    declared_types_for_path,
    validate_settings_tree,
)
from wizard import _convert_value


def selectable_values():
    """(section, module, dependency, path, value) for every option a user can
    pick that lands on a path the schema actually models."""
    wizard_data = read_wizard()
    for section, modules in wizard_data.get("modules", {}).items():
        for module_name, module in modules.items():
            for dep, spec in (module.get("settings_dependencies") or {}).items():
                path = spec.get("settings")
                if not path or not declared_types_for_path(path):
                    continue
                values = list((spec.get("options") or {}).keys())
                if spec.get("default") is not None:
                    values.append(spec["default"])
                for value in values:
                    yield section, module_name, dep, tuple(path), value


CASES = sorted(set(selectable_values()))


def test_the_manifest_offers_something_to_check():
    """A conformance sweep that silently walks nothing is worse than no sweep."""
    assert len(CASES) > 100
    paths = {case[3] for case in CASES}
    assert ("platform", "outputs", "auger") in paths
    assert ("platform", "ft232h", "url") in paths


@pytest.mark.parametrize("section,module,dep,path,value", CASES)
def test_every_manifest_option_can_be_written(ds, section, module, dep, path, value):
    settings = copy.deepcopy(default_settings())
    coerced = coerce_setting_value(list(path), value, _convert_value)
    set_nested_key_value(settings, list(path), coerced)

    try:
        validate_settings_tree(settings)
    except SettingsValidationError as exc:
        pytest.fail(
            f"{section}/{module}: picking {value!r} for '{dep}' writes {coerced!r} "
            f"to {'.'.join(path)}, which the schema rejects: {exc}"
        )
