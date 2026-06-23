import json
import os


def _manifest():
    path = os.path.join(os.path.dirname(__file__), '..', 'wizard', 'wizard_manifest.json')
    with open(path) as handle:
        return json.load(handle)


def test_x86_platform_entry_present():
    manifest = _manifest()
    entry = manifest['modules']['grillplatform']['x86_numato_emc2101']
    assert entry['filename'] == 'x86_numato_emc2101'
    assert 'adafruit-circuitpython-emc2101' in entry['py_dependencies']


def test_x86_platform_settings_dependencies():
    manifest = _manifest()
    deps = manifest['modules']['grillplatform']['x86_numato_emc2101']['settings_dependencies']
    # Exposes the EMC2101 address and the i2c bus match string.
    assert 'emc2101_address' in deps
    assert 'i2c_bus_match' in deps
