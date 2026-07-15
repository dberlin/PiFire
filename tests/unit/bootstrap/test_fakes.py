from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.probes import FakeProbes
from tests.fakes.notifier import FakeNotifier


def test_grill_records_calls_and_toggles_output():
    g = FakeGrillPlatform(outputs=("power", "auger", "fan", "igniter"))
    g.auger_on()
    assert g.get_output_status()["auger"] is True
    g.auger_off()
    assert g.get_output_status()["auger"] is False
    assert ("auger_on", ()) in g.calls
    assert g.calls[-1][0] == "auger_off"


def test_probes_yield_scripted_sequence():
    p = FakeProbes()
    p.script(
        [
            {"primary": {"Grill": 100}, "food": {}, "aux": {}, "tr": {}},
            {"primary": {"Grill": 110}, "food": {}, "aux": {}, "tr": {}},
        ]
    )
    assert list(p.read_probes()["primary"].values())[0] == 100
    assert list(p.read_probes()["primary"].values())[0] == 110


def test_notifier_records_sent():
    n = FakeNotifier()
    n.send("Grill_Error_01")
    assert n.sent == ["Grill_Error_01"]
