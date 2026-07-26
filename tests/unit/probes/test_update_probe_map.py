"""ProbesMain.update_probe_map() had ZERO callers before 2026-07-26 (verified
with `grep -rn update_probe_map`), and its `error = self._setup_probe_devices(...)`
was dead: _setup_probe_devices returned None unconditionally. Both are fixed
here, because POST /api/probe_map is now its caller."""

from probes.main import ProbesMain

VIRT = {
    "config": {"probes_list": []},
    "device": "VirtDev",
    "module": "virtual_average",
    "module_filename": "virtual_average",
    "ports": ["VIRT0"],
}
BOGUS = {
    "config": {},
    "device": "Ghost",
    "module": "no_such_module",
    "module_filename": "no_such_module",
    "ports": ["X0"],
}


def _map(devices, probes=()):
    return {"probe_devices": list(devices), "probe_info": list(probes)}


def test_update_probe_map_rebuilds_the_device_list():
    pm = ProbesMain(_map([]), "F")
    assert pm.probe_device_list == []

    errors = pm.update_probe_map(_map([VIRT]))

    assert errors == []
    assert len(pm.probe_device_list) == 1
    assert pm.probe_devices == [VIRT]


def test_update_probe_map_shrinks_as_well_as_grows():
    pm = ProbesMain(_map([VIRT]), "F")
    assert len(pm.probe_device_list) == 1

    pm.update_probe_map(_map([]))

    assert pm.probe_device_list == []


def test_update_probe_map_reports_a_module_that_will_not_import():
    """An unimportable module degrades to probes.disabled rather than raising
    -- _setup_probe_devices:44-53 -- but the caller must be able to SEE it."""
    pm = ProbesMain(_map([]), "F")

    errors = pm.update_probe_map(_map([dict(BOGUS)]))

    assert len(errors) == 1
    assert "no_such_module" in errors[0]
    assert len(pm.probe_device_list) == 1  # the disabled stand-in
