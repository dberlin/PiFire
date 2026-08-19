"""Coverage for the pure-logic virtual aggregator probes (virtual_average/median/highest/lowest)
and the trivial prototype/disabled probes.

These modules import only probes.base + statistics/random -- no hardware, no mocks needed. Tests
construct a real ReadProbes (each module's subclass of probes.base.ProbeInterface) via a minimal
but complete probe_info/device_info pair, following the pattern already established in
tests/unit/probes/test_base.py.

Ambiguity resolved from the task brief: ProbeInterface.__init__ (probes/base.py:133) requires
probe_info to be a *list* of probe dicts -- set_profiles/_discover_port_types/_build_port_map
all do `for probe in probe_info: ... probe["device"] ...`. The brief's own example passes
probe_info={"probes": []}, a dict rather than a list; iterating a dict yields its string keys, so
the very first `probe["device"]` inside set_profiles raises `TypeError: string indices must be
integers`. Every probe dict here also carries a "profile" key: set_profiles unconditionally does
`self.probe_profiles[port] = probe["profile"]` for any probe entry matching device+port, so a
dict without one raises KeyError -- this applies even to the aggregator modules, which never
themselves read probe_profiles.
"""

import pytest

from probes import disabled, prototype, virtual_average, virtual_highest, virtual_lowest, virtual_median

# Real Steinhart-Hart coefficients (Thermoworks-Pro-Series-HeaterMeter, TWPS00), same reference
# values used in tests/unit/probes/test_base.py. Only prototype.py's ReadProbes exercises these
# via base's real read_all_ports/_voltage_to_temp; the four aggregator modules never touch
# probe_profiles, but set_profiles always builds the entry regardless.
PROFILE_A = 0.00073431401
PROFILE_B = 0.0002157437
PROFILE_C = 9.515686e-8


def _profile():
    return {"A": PROFILE_A, "B": PROFILE_B, "C": PROFILE_C}


def _probe(port, label, ptype, device="virt"):
    return {"device": device, "port": port, "label": label, "type": ptype, "profile": _profile()}


def _device(module, probes_list, port="VIRT0", device_name="virt", config=None):
    cfg = {"probes_list": probes_list}
    if config:
        cfg.update(config)
    return {"device": device_name, "module": module, "ports": [port], "config": cfg}


def _out(**groups):
    data = {"primary": {}, "food": {}, "aux": {}, "tr": {}}
    data.update(groups)
    return data


# ---------------------------------------------------------------------------
# virtual_average / virtual_median / virtual_highest / virtual_lowest
# ---------------------------------------------------------------------------

AGGREGATORS = [
    (virtual_average, "virtual_average", [100, 200], 150.0),
    (virtual_median, "virtual_median", [100, 200], 150.0),
    (virtual_highest, "virtual_highest", [100, 200], 200),
    (virtual_lowest, "virtual_lowest", [100, 200], 100),
]


@pytest.mark.parametrize("module,module_name,values,expected", AGGREGATORS)
@pytest.mark.parametrize("ptype,group", [("Primary", "primary"), ("Food", "food"), ("Aux", "aux")])
def test_aggregator_writes_expected_group_and_zeroes_tr(module, module_name, values, expected, ptype, group):
    device_info = _device(module_name, ["Grill1", "Grill2"])
    probe_info = [_probe("VIRT0", "AggLabel", ptype)]
    obj = module.ReadProbes(probe_info, device_info, units="F")

    output_data = _out(primary={"Grill1": values[0], "Grill2": values[1]})
    result = obj.read_all_ports(output_data)

    assert result[group]["AggLabel"] == pytest.approx(expected)
    assert result["tr"]["AggLabel"] == 0


def test_virtual_median_distinct_value_from_three_inputs():
    device_info = _device("virtual_median", ["Grill1", "Grill2", "Grill3"])
    probe_info = [_probe("VIRT0", "MedianLabel", "Primary")]
    obj = virtual_median.ReadProbes(probe_info, device_info, units="F")

    output_data = _out(primary={"Grill1": 100, "Grill2": 300, "Grill3": 150})
    result = obj.read_all_ports(output_data)

    assert result["primary"]["MedianLabel"] == pytest.approx(150.0)
    assert result["tr"]["MedianLabel"] == 0


@pytest.mark.parametrize("module,module_name,values,expected", AGGREGATORS)
def test_aggregator_sources_probe_from_food_group(module, module_name, values, expected):
    # The mixed food/aux probe-location case: the *source* probes being aggregated live in
    # output_data["food"], not output_data["primary"].
    device_info = _device(module_name, ["Food1", "Food2"])
    probe_info = [_probe("VIRT0", "AggLabel", "Primary")]
    obj = module.ReadProbes(probe_info, device_info, units="F")

    output_data = _out(food={"Food1": values[0], "Food2": values[1]})
    result = obj.read_all_ports(output_data)

    assert result["primary"]["AggLabel"] == pytest.approx(expected)


@pytest.mark.parametrize("module,module_name,values,expected", AGGREGATORS)
def test_aggregator_sources_probe_from_aux_group(module, module_name, values, expected):
    # Same as above, but the source probes live in output_data["aux"] -- covers the
    # final elif in the probes_list source-lookup chain (primary -> food -> aux).
    device_info = _device(module_name, ["Aux1", "Aux2"])
    probe_info = [_probe("VIRT0", "AggLabel", "Primary")]
    obj = module.ReadProbes(probe_info, device_info, units="F")

    output_data = _out(aux={"Aux1": values[0], "Aux2": values[1]})
    result = obj.read_all_ports(output_data)

    assert result["primary"]["AggLabel"] == pytest.approx(expected)


@pytest.mark.parametrize("module", [virtual_average, virtual_median, virtual_highest, virtual_lowest])
def test_aggregator_get_device_info_reports_empty_status(module):
    device_info = _device("agg", ["Grill1"])
    probe_info = [_probe("VIRT0", "Label", "Primary")]
    obj = module.ReadProbes(probe_info, device_info, units="F")

    info = obj.get_device_info()

    assert info["status"] == {}
    assert info is obj.device_info


# ---------------------------------------------------------------------------
# prototype.py: simulated ADC device (ProtoDevice) + ReadProbes
# ---------------------------------------------------------------------------


def _prototype_device_info(ports, transient=False):
    return {
        "device": "proto",
        "module": "prototype",
        "ports": list(ports),
        "config": {"transient": "True" if transient else "False", "voltage_ref": "3.28"},
    }


def _prototype_probe_info(ports_types):
    return [_probe(port, f"{port}_label", ptype, device="proto") for port, ptype in ports_types]


def test_prototype_init_device_builds_protodevice_with_seeded_voltages():
    device_info = _prototype_device_info(("ADC0", "ADC1"))
    probe_info = _prototype_probe_info([("ADC0", "Primary"), ("ADC1", "Food")])

    obj = prototype.ReadProbes(probe_info, device_info, units="F")

    assert isinstance(obj.device, prototype.ProtoDevice)
    assert obj.device.port_value["ADC0"] == 316  # primary seed voltage
    assert obj.device.port_value["ADC1"] == 3000  # non-primary seed voltage
    assert obj.device.get_status() == {"error": None}


def test_prototype_read_voltage_primary_increments_on_high_seed(monkeypatch):
    device_info = _prototype_device_info(("ADC0",))
    probe_info = _prototype_probe_info([("ADC0", "Primary")])
    obj = prototype.ReadProbes(probe_info, device_info, units="F")
    monkeypatch.setattr(prototype.random, "randint", lambda a, b: 8)

    # seed(8) > 7 and 316 < maxPrimaryVoltage(550) -> += primaryChangeFactor(2)
    assert obj.device.read_voltage("ADC0") == 318


def test_prototype_read_voltage_primary_decrements_on_low_seed(monkeypatch):
    device_info = _prototype_device_info(("ADC0",))
    probe_info = _prototype_probe_info([("ADC0", "Primary")])
    obj = prototype.ReadProbes(probe_info, device_info, units="F")
    monkeypatch.setattr(prototype.random, "randint", lambda a, b: 0)

    # seed(0) < 1 and 316 > minPrimaryVoltage(300) -> -= primaryChangeFactor(2)
    assert obj.device.read_voltage("ADC0") == 314


def test_prototype_read_voltage_food_port_high_seed_decrements(monkeypatch):
    device_info = _prototype_device_info(("ADC0", "ADC1"))
    probe_info = _prototype_probe_info([("ADC0", "Primary"), ("ADC1", "Food")])
    obj = prototype.ReadProbes(probe_info, device_info, units="F")
    monkeypatch.setattr(prototype.random, "randint", lambda a, b: 8)

    # seed(8) > 7 and 3000 > maxFoodVoltage(1300) -> -= otherChangeFactor(10)
    assert obj.device.read_voltage("ADC1") == 2990


def test_prototype_read_voltage_food_port_low_seed_increments(monkeypatch):
    device_info = _prototype_device_info(("ADC0", "ADC1"))
    probe_info = _prototype_probe_info([("ADC0", "Primary"), ("ADC1", "Food")])
    obj = prototype.ReadProbes(probe_info, device_info, units="F")
    monkeypatch.setattr(prototype.random, "randint", lambda a, b: 0)

    # seed(0) < 1 and 3000 < minFoodVoltage(3200) -> += otherChangeFactor(10)
    assert obj.device.read_voltage("ADC1") == 3010


def test_prototype_read_voltage_transient_food_port_returns_none(monkeypatch):
    device_info = _prototype_device_info(("ADC0", "ADC1"), transient=True)
    probe_info = _prototype_probe_info([("ADC0", "Primary"), ("ADC1", "Food")])
    obj = prototype.ReadProbes(probe_info, device_info, units="F")
    # randint is used for BOTH the outer seed roll and the transient(0,100) roll; fixing it to a
    # value < 80 makes the "return None" short-circuit fire regardless of which call it answers.
    monkeypatch.setattr(prototype.random, "randint", lambda a, b: 8)

    assert obj.device.read_voltage("ADC1") is None


def test_prototype_read_all_ports_via_base_populates_temps_and_tr(monkeypatch):
    device_info = _prototype_device_info(("ADC0", "ADC1"))
    probe_info = _prototype_probe_info([("ADC0", "Primary"), ("ADC1", "Food")])
    obj = prototype.ReadProbes(probe_info, device_info, units="F")
    monkeypatch.setattr(prototype.random, "randint", lambda a, b: 8)

    # read_all_ports is inherited from ProbeInterface (probes/base.py); already covered in
    # test_base.py. Exercised here to cover ProtoDevice.read_voltage's full call path from a
    # real ReadProbes instance. Values aren't pinned (Steinhart-Hart output can legitimately
    # clamp to int 0 out-of-bounds), only structure/type.
    result = obj.read_all_ports(obj.output_data)

    assert isinstance(result["primary"]["ADC0_label"], (int, float))
    assert isinstance(result["tr"]["ADC0_label"], int)
    assert isinstance(result["food"]["ADC1_label"], (int, float))
    assert isinstance(result["tr"]["ADC1_label"], int)


def test_prototype_get_device_info_returns_protodevice_status():
    device_info = _prototype_device_info(("ADC0",))
    probe_info = _prototype_probe_info([("ADC0", "Primary")])
    obj = prototype.ReadProbes(probe_info, device_info, units="F")

    info = obj.get_device_info()

    assert info["status"] == {"error": None}
    assert info is obj.device_info


# ---------------------------------------------------------------------------
# disabled.py: fixed "disabled" placeholder probe (no real device)
# ---------------------------------------------------------------------------


def _disabled_device_info(ports):
    return {"device": "dis", "module": "disabled", "ports": ports, "config": {}}


def test_disabled_init_skips_device_creation():
    # _init_device is overridden to a no-op `pass`, so unlike every other probe module,
    # self.device is never assigned. Pinning this since it's a real behavioral difference,
    # not a bug: disabled.py's read_all_ports/get_device_info never touch self.device.
    device_info = _disabled_device_info(["ADC0"])
    probe_info = [_probe("ADC0", "Label", "Primary", device="dis")]

    obj = disabled.ReadProbes(probe_info, device_info, units="F")

    assert not hasattr(obj, "device")


def test_disabled_read_all_ports_writes_zero_to_every_group():
    device_info = _disabled_device_info(["P1", "P2", "P3"])
    probe_info = [
        _probe("P1", "Primary1", "Primary", device="dis"),
        _probe("P2", "Food1", "Food", device="dis"),
        _probe("P3", "Aux1", "Aux", device="dis"),
    ]
    obj = disabled.ReadProbes(probe_info, device_info, units="F")

    result = obj.read_all_ports(obj.output_data)

    assert result["primary"]["Primary1"] == 0
    assert result["food"]["Food1"] == 0
    assert result["aux"]["Aux1"] == 0
    assert result["tr"] == {"Primary1": 0, "Food1": 0, "Aux1": 0}


def test_disabled_get_device_info_reports_disconnected():
    device_info = _disabled_device_info(["P1"])
    probe_info = [_probe("P1", "Primary1", "Primary", device="dis")]
    obj = disabled.ReadProbes(probe_info, device_info, units="F")

    info = obj.get_device_info()

    assert info["status"] == {"connected": False, "disabled": True}
    assert info is obj.device_info
