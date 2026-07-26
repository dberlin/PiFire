"""The four timer ops.

Each op reproduces one branch of common/api_commands.py::_cmd_set_timer, with
one difference that is the entire point: the BRANCH is chosen at drain time from
live state, while the CLOCK travels in the op as `at`. So a stop followed by a
pause inside one control cycle pauses a timer that is already cleared -- which is
`_cmd_set_timer`'s own start == 0 branch, i.e. a no-op -- instead of resurrecting
a countdown from a pre-stop read.
"""

import logging

from common.control_delta import apply_control_delta, control_delta

NOW = 1_700_000_000.0


def _running():
    return {
        "timer": {"start": 1000.0, "paused": 0, "end": 2000.0},
        "notify_data": [{"label": "Timer", "type": "timer", "req": True, "shutdown": True, "keep_warm": False}],
    }


def _paused():
    control = _running()
    control["timer"]["paused"] = 1500.0
    return control


def _stopped():
    return {
        "timer": {"start": 0, "paused": 0, "end": 0},
        "notify_data": [{"label": "Timer", "type": "timer", "req": False, "shutdown": False, "keep_warm": False}],
    }


def _timer_entry(control):
    return next(e for e in control["notify_data"] if e["type"] == "timer")


def test_clear_zeroes_the_countdown_and_disarms_both_expiry_flags():
    control = _running()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.clear"}]))
    assert control["timer"] == {"start": 0, "paused": 0, "end": 0}
    assert _timer_entry(control) == {
        "label": "Timer",
        "type": "timer",
        "req": False,
        "shutdown": False,
        "keep_warm": False,
    }


def test_pause_on_a_running_timer_stamps_paused_from_the_requests_clock():
    control = _running()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.pause", "at": NOW}]))
    assert control["timer"] == {"start": 1000.0, "paused": NOW, "end": 2000.0}
    assert _timer_entry(control)["req"] is False


def test_pause_on_a_stopped_timer_clears():
    """_cmd_set_timer's start == 0 branch (common/api_commands.py:685-693)."""
    control = _stopped()
    control["timer"]["end"] = 5.0
    apply_control_delta(control, control_delta(ops=[{"op": "timer.pause", "at": NOW}]))
    assert control["timer"] == {"start": 0, "paused": 0, "end": 0}


def test_start_or_resume_on_a_stopped_timer_arms_seconds_from_at():
    control = _stopped()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.start_or_resume", "at": NOW, "seconds": 300}]))
    assert control["timer"] == {"start": NOW, "paused": 0, "end": NOW + 300}
    assert _timer_entry(control)["req"] is True


def test_start_or_resume_substitutes_sixty_seconds_for_a_null_duration():
    """The bare form's is_float() fallback (common/api_commands.py:672)."""
    control = _stopped()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.start_or_resume", "at": NOW, "seconds": None}]))
    assert control["timer"]["end"] == NOW + 60


def test_start_or_resume_on_a_paused_timer_shifts_the_end_and_unpauses():
    control = _paused()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.start_or_resume", "at": NOW, "seconds": 500}]))
    assert control["timer"] == {"start": 1000.0, "paused": 0, "end": 2000.0 - 1500.0 + NOW}


def test_start_with_options_arms_the_countdown_and_both_flags():
    control = _stopped()
    apply_control_delta(
        control,
        control_delta(
            ops=[
                {
                    "op": "timer.start_with_options",
                    "at": NOW,
                    "seconds": 600,
                    "shutdown": True,
                    "keep_warm": False,
                }
            ]
        ),
    )
    assert control["timer"] == {"start": NOW, "paused": 0, "end": NOW + 600}
    entry = _timer_entry(control)
    assert (entry["req"], entry["shutdown"], entry["keep_warm"]) == (True, True, False)


def test_start_with_options_drops_and_logs_when_the_timer_became_paused(caplog):
    """Request time already rejected a paused timer (common/api_commands.py:620-623).
    Reaching the drain paused means another writer paused it in the same cycle."""
    control = _paused()
    with caplog.at_level(logging.ERROR, logger="control"):
        apply_control_delta(
            control,
            control_delta(
                ops=[
                    {
                        "op": "timer.start_with_options",
                        "at": NOW,
                        "seconds": 600,
                        "shutdown": True,
                        "keep_warm": False,
                    }
                ]
            ),
        )
    assert control["timer"] == {"start": 1000.0, "paused": 1500.0, "end": 2000.0}
    assert "timer.start_with_options" in caplog.text


# --- the two resurrections, at the op level --------------------------------


def test_clear_then_pause_leaves_the_timer_stopped():
    """web-react TimerBar's Stop-then-Pause pair. Pinned as resurrecting at
    tests/characterization/test_process_command_golden.py::
    test_a_pause_after_a_stop_in_one_cycle_resurrects_the_timer."""
    control = _running()
    apply_control_delta(control, control_delta(ops=[{"op": "timer.clear"}, {"op": "timer.pause", "at": NOW}]))
    assert control["timer"] == {"start": 0, "paused": 0, "end": 0}
    assert _timer_entry(control)["shutdown"] is False


def test_clear_then_start_or_resume_arms_a_fresh_timer_rather_than_the_old_one():
    """Stop-then-Resume. The old end time (2000.0) must NOT come back; what the
    user gets is what they would get one control cycle apart -- the resume sees
    paused == 0 and arms a fresh countdown."""
    control = _paused()
    apply_control_delta(
        control,
        control_delta(ops=[{"op": "timer.clear"}, {"op": "timer.start_or_resume", "at": NOW, "seconds": 500}]),
    )
    assert control["timer"] == {"start": NOW, "paused": 0, "end": NOW + 500}


def test_start_or_resume_then_clear_leaves_the_timer_stopped():
    """Residual 2: a `stop` against an already-zero ancestor carried no evidence
    of intent, so start + stop in one cycle left the timer RUNNING."""
    control = _stopped()
    apply_control_delta(
        control,
        control_delta(ops=[{"op": "timer.start_or_resume", "at": NOW, "seconds": 600}, {"op": "timer.clear"}]),
    )
    assert control["timer"] == {"start": 0, "paused": 0, "end": 0}


def test_every_validated_op_name_has_an_applier():
    """A name in _OP_FIELDS but not in _OP_APPLIERS passes validation at PUSH
    time in the web process and then raises KeyError in the control loop's
    drain, a process away. Pin both ends of the table against each other."""
    from common.control_delta import _OP_APPLIERS, CONTROL_DELTA_OPS

    assert set(_OP_APPLIERS) == set(CONTROL_DELTA_OPS)
