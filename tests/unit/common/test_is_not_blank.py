# tests/unit/common/test_is_not_blank.py
from common.app import is_not_blank


def test_key_absent_returns_false():
    assert is_not_blank({}, "pmode") is False


def test_key_present_with_value_returns_true():
    assert is_not_blank({"pmode": "2"}, "pmode") is True


def test_value_with_surrounding_whitespace_is_not_blank():
    # Stripping decides blankness only; a real value keeps its branch.
    assert is_not_blank({"pmode": " 2 "}, "pmode") is True


def test_key_present_but_empty_returns_false():
    # Regression: today this is True (the helper compares the key NAME, not the
    # value, so it is always true for a present key), which lets int("") reach
    # the settings route and raise a 500. After the fix, an empty submission
    # must read as blank so the branch is skipped and the prior value stands.
    assert is_not_blank({"pmode": ""}, "pmode") is False


def test_key_present_but_whitespace_only_returns_false():
    # Same 500, same cause: int("   ") raises ValueError exactly as int("") does.
    # None of the 37 callers save a raw string except selectController, and a
    # whitespace controller name is garbage there too, so whitespace is blank.
    assert is_not_blank({"pmode": "   "}, "pmode") is False
