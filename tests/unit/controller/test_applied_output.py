import inspect
import itertools

import pytest

from controller.applied_output import (
    AppliedOutput,
    OutputSource,
    classify_output_source,
    seed_output,
)


PRECEDENCE_CASES = [
    (False, False, False, OutputSource.CONTROLLER),
    (False, False, True, OutputSource.FAN_ASSIST),
    (True, False, False, OutputSource.LID_OPEN),
    (True, False, True, OutputSource.LID_OPEN),
    (False, True, False, OutputSource.MANUAL_OVERRIDE),
    (False, True, True, OutputSource.MANUAL_OVERRIDE),
    (True, True, False, OutputSource.MANUAL_OVERRIDE),
    (True, True, True, OutputSource.MANUAL_OVERRIDE),
]


def test_precedence_cases_cover_every_boolean_combination():
    arity = len(inspect.signature(classify_output_source).parameters)
    covered = {case[:arity] for case in PRECEDENCE_CASES}
    expected = set(itertools.product([False, True], repeat=arity))
    assert covered == expected


@pytest.mark.parametrize("lid,manual,fan,expected", PRECEDENCE_CASES)
def test_precedence(lid, manual, fan, expected):
    assert classify_output_source(lid, manual, fan) is expected


def test_controller_commanded_is_derived_from_source():
    assert AppliedOutput(0.4, OutputSource.CONTROLLER, 1.0).controller_commanded is True
    for source in (OutputSource.LID_OPEN, OutputSource.MANUAL_OVERRIDE, OutputSource.FAN_ASSIST, OutputSource.SEED):
        assert AppliedOutput(0.4, source, 1.0).controller_commanded is False


def test_applied_output_is_frozen():
    applied = AppliedOutput(0.4, OutputSource.CONTROLLER, 1.0)
    with pytest.raises(Exception):
        applied.ratio = 0.9


def test_requested_defaults_to_none():
    assert AppliedOutput(0.9, OutputSource.CONTROLLER, 1.0).requested is None
    assert AppliedOutput(0.9, OutputSource.CONTROLLER, 1.0, requested=1.4).requested == 1.4


def test_seed_output_is_seed_when_nothing_else_applies():
    applied = seed_output(
        0.15, 100.0, lid_open=False, manual_override_active=False, fan_assist_active=False, auger_output=True
    )
    assert applied.source is OutputSource.SEED
    assert applied.ratio == 0.15
    assert applied.timestamp == 100.0
    assert applied.requested is None


def test_seed_output_keeps_a_real_reason_when_one_exists():
    applied = seed_output(
        0.15, 100.0, lid_open=True, manual_override_active=False, fan_assist_active=False, auger_output=False
    )
    assert applied.source is OutputSource.LID_OPEN


def test_seed_output_reports_zero_when_the_auger_is_off():
    applied = seed_output(
        0.5, 100.0, lid_open=False, manual_override_active=False, fan_assist_active=False, auger_output=False
    )
    assert applied.ratio == 0.0


def test_seed_output_reports_manual_override_when_active():
    applied = seed_output(
        0.15, 100.0, lid_open=False, manual_override_active=True, fan_assist_active=False, auger_output=True
    )
    assert applied.source is OutputSource.MANUAL_OVERRIDE


def test_seed_output_reports_fan_assist_when_active():
    applied = seed_output(
        0.15, 100.0, lid_open=False, manual_override_active=False, fan_assist_active=True, auger_output=True
    )
    assert applied.source is OutputSource.FAN_ASSIST
