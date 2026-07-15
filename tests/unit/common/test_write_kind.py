import pytest
from enum import Enum


def test_write_kind_is_enum_with_two_members():
    from common.common import WriteKind

    assert issubclass(WriteKind, Enum)
    assert {m.name for m in WriteKind} == {"OVERWRITE", "MERGE"}


def test_write_control_requires_kind():
    # kind is positional & required: calling without it raises TypeError
    from common.common import write_control

    with pytest.raises(TypeError):
        write_control({"mode": "Stop"})


def test_write_control_rejects_non_writekind():
    from common.common import write_control

    with pytest.raises(TypeError):
        write_control({"mode": "Stop"}, True)  # legacy boolean no longer accepted
