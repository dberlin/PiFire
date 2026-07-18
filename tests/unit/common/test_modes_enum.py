import json
from common.modes import Mode


def test_member_is_its_string():
    assert Mode.SMOKE == "Smoke"
    assert isinstance(Mode.SMOKE, str)
    assert Mode.STOP == "Stop" and Mode.ERROR == "Error" and Mode.RECIPE == "Recipe"


def test_str_and_format_return_the_value_not_the_member_repr():
    # This is exactly why StrEnum (not `class Mode(str, Enum)`) is required:
    # `(str, Enum)` would give "Mode.SMOKE" here, a behavior change in any log/display.
    assert str(Mode.SMOKE) == "Smoke"
    assert f"{Mode.SMOKE}" == "Smoke"
    assert "%s" % Mode.HOLD == "Hold"
    assert "mode is " + Mode.ERROR == "mode is Error"


def test_json_serializes_to_plain_string():
    assert json.dumps({"mode": Mode.SMOKE}) == json.dumps({"mode": "Smoke"})
    # round-trip: JSON read gives a plain str that still == the member
    assert json.loads(json.dumps({"mode": Mode.HOLD}))["mode"] == Mode.HOLD


def test_dict_key_and_set_interop_with_plain_strings():
    # dispatch map keyed by Mode, looked up with a plain string from control["mode"]
    d = {Mode.SMOKE: 1, Mode.HOLD: 2}
    assert d["Smoke"] == 1 and d[Mode.HOLD] == 2
    # set membership (ALLOWED_EXITS) works both ways
    s = {Mode.ERROR, Mode.REIGNITE, Mode.STOP}
    assert "Error" in s and Mode.STOP in s


def test_all_eleven_values_exact():
    assert {m.value for m in Mode} == {
        "Startup",
        "Smoke",
        "Hold",
        "Monitor",
        "Manual",
        "Prime",
        "Reignite",
        "Shutdown",
        "Stop",
        "Error",
        "Recipe",
    }
