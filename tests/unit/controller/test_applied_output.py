import inspect
import itertools

import pytest

from controller.applied_output import (
    AppliedOutput,
    FrameFeedbackDisposition,
    OutputSource,
    classify_output_source,
    seed_output,
)

PRECEDENCE_CASES = [
    (False, False, OutputSource.CONTROLLER),
    (True, False, OutputSource.LID_OPEN),
    (False, True, OutputSource.MANUAL_OVERRIDE),
    (True, True, OutputSource.MANUAL_OVERRIDE),
]


def test_precedence_cases_cover_every_boolean_combination():
    arity = len(inspect.signature(classify_output_source).parameters)
    covered = {case[:arity] for case in PRECEDENCE_CASES}
    expected = set(itertools.product([False, True], repeat=arity))
    assert covered == expected


@pytest.mark.parametrize("lid,manual,expected", PRECEDENCE_CASES)
def test_precedence(lid, manual, expected):
    assert classify_output_source(lid, manual) is expected


def test_controller_commanded_is_derived_from_source():
    assert AppliedOutput(0.4, OutputSource.CONTROLLER, 1.0).controller_commanded is True
    for source in (OutputSource.LID_OPEN, OutputSource.MANUAL_OVERRIDE, OutputSource.SEED):
        assert AppliedOutput(0.4, source, 1.0).controller_commanded is False


def test_applied_output_is_frozen():
    applied = AppliedOutput(0.4, OutputSource.CONTROLLER, 1.0)
    with pytest.raises(Exception):
        applied.ratio = 0.9


def test_requested_defaults_to_none():
    assert AppliedOutput(0.9, OutputSource.CONTROLLER, 1.0).requested is None
    assert AppliedOutput(0.9, OutputSource.CONTROLLER, 1.0, requested=1.4).requested == 1.4


def test_terminal_frame_feedback_carries_immutable_producing_calibration_identity():
    applied = AppliedOutput(
        0.4,
        OutputSource.CONTROLLER,
        20.0,
        producing_result_revision=7,
        producing_calibration_revision=3,
        producing_calibration_action="start",
        producing_calibration_generation=1,
        feedback_disposition=FrameFeedbackDisposition.COMPLETE,
        sample_complete=True,
    )

    assert applied.producing_result_revision == 7
    assert applied.producing_calibration_revision == 3
    assert applied.producing_calibration_action == "start"
    assert applied.producing_calibration_generation == 1
    assert applied.feedback_disposition is FrameFeedbackDisposition.COMPLETE
    with pytest.raises(Exception):
        applied.feedback_disposition = FrameFeedbackDisposition.PROGRESS


def test_seed_output_is_seed_when_nothing_else_applies():
    applied = seed_output(0.15, 100.0, lid_open=False, manual_override_active=False, auger_output=True)
    assert applied.source is OutputSource.SEED
    assert applied.ratio == 0.15
    assert applied.timestamp == 100.0
    assert applied.requested is None


def test_seed_output_keeps_a_real_reason_when_one_exists():
    applied = seed_output(0.15, 100.0, lid_open=True, manual_override_active=False, auger_output=False)
    assert applied.source is OutputSource.LID_OPEN


def test_seed_output_reports_zero_when_the_auger_is_off():
    applied = seed_output(0.5, 100.0, lid_open=False, manual_override_active=False, auger_output=False)
    assert applied.ratio == 0.0


def test_seed_output_reports_manual_override_when_active():
    applied = seed_output(0.15, 100.0, lid_open=False, manual_override_active=True, auger_output=True)
    assert applied.source is OutputSource.MANUAL_OVERRIDE
