"""Timer commands use the admitted drain clock, never queued wall provenance."""

from copy import deepcopy
from typing import NotRequired, TypedDict

import pytest

from common.control_delta import ControlDeltaError, apply_control_delta, control_delta
from common.timer import TimerRecord, default_timer, pause_timer, remaining_seconds, start_timer
from tests.fakes.clock import clock_stamp


NOW = clock_stamp()


class _TimerControl(TypedDict):
    mode: str
    timer: TimerRecord
    notify_data: list[dict[str, str | bool]]
    recipe: NotRequired[dict[str, int]]


def _control(timer: TimerRecord | None = None) -> _TimerControl:
    return {
        "mode": "Hold",
        "timer": default_timer() if timer is None else timer,
        "notify_data": [{"label": "Timer", "type": "timer", "req": True, "shutdown": True, "keep_warm": False}],
    }


def _op(name: str, **fields: object) -> dict[str, object]:
    return {
        "op": name,
        "requested_wall_s": NOW.observed_wall_s,
        "target_runtime_id": NOW.runtime_id,
        **fields,
    }


def test_delayed_drain_starts_the_full_requested_duration():
    control = _control()
    envelope = control_delta(ops=[_op("timer.start_or_resume", seconds=300)])
    drained = clock_stamp(monotonic_s=800, wall_s=NOW.observed_wall_s + 700)

    apply_control_delta(control, envelope, timer_now=drained)

    assert remaining_seconds(control["timer"], drained) == 300
    assert remaining_seconds(control["timer"], clock_stamp(monotonic_s=825)) == 275
    assert control["timer"]["action_armed"] is True


@pytest.mark.parametrize("wall_jump", [-86_400, 86_400])
def test_pause_uses_elapsed_duration_despite_wall_jumps(wall_jump):
    control = _control(start_timer(300, NOW))
    paused = clock_stamp(monotonic_s=130, wall_s=NOW.observed_wall_s + wall_jump)

    apply_control_delta(control, control_delta(ops=[_op("timer.pause")]), timer_now=paused)

    assert control["timer"]["state"] == "paused"
    assert remaining_seconds(control["timer"], clock_stamp(monotonic_s=900)) == 270
    assert control["timer"]["action_armed"] is False
    assert control["notify_data"][0]["req"] is False


@pytest.mark.parametrize("interrupted", [False, True])
def test_resume_preserves_known_remaining_instead_of_replacing_with_requested_seconds(interrupted):
    timer = pause_timer(start_timer(300, NOW), clock_stamp(monotonic_s=130))
    if interrupted:
        timer["state"] = "interrupted"
    control = _control(timer)
    resumed = clock_stamp(monotonic_s=800, wall_s=NOW.observed_wall_s - 50_000)

    apply_control_delta(control, control_delta(ops=[_op("timer.start_or_resume", seconds=500)]), timer_now=resumed)

    assert control["timer"]["state"] == "running"
    assert remaining_seconds(control["timer"], resumed) == 270
    assert remaining_seconds(control["timer"], clock_stamp(monotonic_s=820)) == 250
    assert control["timer"]["action_armed"] is True
    assert control["notify_data"][0]["req"] is True


def test_fifo_pause_then_resume_reads_the_newly_paused_duration():
    control = _control(start_timer(300, NOW))
    drained = clock_stamp(monotonic_s=130)

    apply_control_delta(
        control,
        control_delta(ops=[_op("timer.pause"), _op("timer.start_or_resume", seconds=500)]),
        timer_now=drained,
    )

    assert control["timer"]["state"] == "running"
    assert remaining_seconds(control["timer"], drained) == 270
    assert control["timer"]["action_armed"] is True


def test_clear_then_pause_cannot_resurrect_a_countdown_or_expiry_actions():
    control = _control(start_timer(300, NOW))

    apply_control_delta(control, control_delta(ops=[_op("timer.clear"), _op("timer.pause")]), timer_now=NOW)

    assert control["timer"]["state"] == "stopped"
    assert remaining_seconds(control["timer"], NOW) == 0
    assert control["timer"]["action_armed"] is False
    assert control["notify_data"][0]["req"] is False
    assert control["notify_data"][0]["shutdown"] is False
    assert control["notify_data"][0]["keep_warm"] is False


def test_clear_then_start_replaces_the_old_paused_duration():
    control = _control(pause_timer(start_timer(300, NOW), clock_stamp(monotonic_s=130)))

    apply_control_delta(
        control,
        control_delta(ops=[_op("timer.clear"), _op("timer.start_or_resume", seconds=500)]),
        timer_now=clock_stamp(monotonic_s=800),
    )

    assert remaining_seconds(control["timer"], clock_stamp(monotonic_s=800)) == 500
    assert control["timer"]["action_armed"] is True
    assert control["notify_data"][0]["shutdown"] is False


def test_start_then_clear_leaves_no_armed_countdown():
    control = _control()

    apply_control_delta(
        control,
        control_delta(ops=[_op("timer.start_or_resume", seconds=600), _op("timer.clear")]),
        timer_now=NOW,
    )

    assert control["timer"]["state"] == "stopped"
    assert remaining_seconds(control["timer"], NOW) == 0
    assert control["timer"]["action_armed"] is False
    assert control["notify_data"][0]["req"] is False


def test_start_with_options_arms_the_selected_expiry_action():
    control = _control()

    apply_control_delta(
        control,
        control_delta(ops=[_op("timer.start_with_options", seconds=600, shutdown=False, keep_warm=True)]),
        timer_now=NOW,
    )

    assert remaining_seconds(control["timer"], NOW) == 600
    assert control["timer"]["action_armed"] is True
    assert control["notify_data"][0]["req"] is True
    assert control["notify_data"][0]["shutdown"] is False
    assert control["notify_data"][0]["keep_warm"] is True


def test_start_with_options_cannot_replace_a_paused_timer():
    control = _control(pause_timer(start_timer(300, NOW), clock_stamp(monotonic_s=130)))
    before = deepcopy(control)

    with pytest.raises(ControlDeltaError):
        apply_control_delta(
            control,
            control_delta(ops=[_op("timer.start_with_options", seconds=600, shutdown=False, keep_warm=True)]),
            timer_now=clock_stamp(monotonic_s=800),
        )

    assert control == before


def test_resume_with_unknown_remaining_is_rejected_without_rearming():
    timer = default_timer()
    timer.update(state="interrupted", remaining_s=None)
    control = _control(timer)
    control["notify_data"][0]["req"] = False
    before = deepcopy(control)

    with pytest.raises(ValueError):
        apply_control_delta(control, control_delta(ops=[_op("timer.start_or_resume", seconds=300)]), timer_now=NOW)

    assert control == before


def test_stale_generation_rejects_the_whole_envelope_before_any_mutation():
    control = _control(start_timer(300, NOW))
    control["recipe"] = {"step": 2}
    before = deepcopy(control)
    stale_op = _op("timer.clear", target_runtime_id="962c8912-46d2-4784-90af-7d8ea1c0d878")
    envelope = control_delta(
        set_values={"mode": "Shutdown"},
        ops=[_op("timer.pause"), stale_op],
        delete_paths=[["recipe", "step"]],
    )

    with pytest.raises(ControlDeltaError):
        apply_control_delta(control, envelope, timer_now=clock_stamp(monotonic_s=130))

    assert control == before
