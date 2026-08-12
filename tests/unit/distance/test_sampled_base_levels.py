"""Direct coverage of SampledHopperLevel._level_from_distance_cm.

Every transport (ToF, serial ToF, HC-SR04) inherits this method unchanged, so
the percentage mapping was being asserted three times through three transport
fixtures. It is arithmetic on the base class -- test it there once, and let
each transport module keep only the tests about ITS transport.
"""

import pytest

from distance._sampled_base import SampledHopperLevel


def _levels(full_cm, empty_cm):
    obj = object.__new__(SampledHopperLevel)
    obj.full = full_cm
    obj.empty = empty_cm
    return obj


@pytest.mark.parametrize(
    ("distance_cm", "expected"),
    [
        (4.0, 100),  # exactly at full
        (2.0, 100),  # closer than full clamps to 100
        (22.0, 0),  # exactly at empty
        (30.0, 0),  # beyond empty clamps to 0
        (13.0, 50),  # midpoint interpolates
    ],
    ids=["at-full", "above-full", "at-empty", "below-empty", "midpoint"],
)
def test_level_from_distance_cm(distance_cm, expected):
    assert _levels(full_cm=4, empty_cm=22)._level_from_distance_cm(distance_cm) == expected
