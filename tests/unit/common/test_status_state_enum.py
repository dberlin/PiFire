import json

from common.modes import StatusState


def test_member_is_its_string():
    assert StatusState.ACTIVE == "active"
    assert StatusState.MONITOR == "monitor"
    assert StatusState.INACTIVE == "inactive"
    assert StatusState.UNSET == ""
    assert isinstance(StatusState.ACTIVE, str)


def test_str_returns_the_value_not_the_member_repr():
    # StrEnum (not `class StatusState(str, Enum)`) required: str() must return
    # the value, not "StatusState.ACTIVE" -- the published web/mobile contract.
    assert str(StatusState.ACTIVE) == "active"
    assert str(StatusState.UNSET) == ""


def test_json_serializes_to_plain_string():
    assert json.dumps({"s": StatusState.MONITOR}) == json.dumps({"s": "monitor"})
    assert json.dumps({"s": StatusState.UNSET}) == json.dumps({"s": ""})


def test_all_four_values_exact():
    assert {m.value for m in StatusState} == {"active", "monitor", "inactive", ""}
