"""Characterization test for `_display_loop`'s per-iteration behavior.

`_display_loop` is an infinite `while True` loop that the pixel-snapshot
harness (test_fixed_base_golden.py) cannot reach -- it never returns. This
test drives exactly ONE iteration deterministically by making the loop's
own `time.sleep` call raise a sentinel exception on its first invocation,
then asserts what that one iteration actually did.

This is the "before" side of Phase B Task 5, which will reconcile the
240x240 base's richer loop (a `self.monitor_display` flag, distinct
`self.loop_delay` / `self.clear_delay` sleeps, a `continue` after each
state transition, and nulling `in_data`/`status_data` after render)
against the two large bases' simpler loop (flat `time.sleep(0.1)`, no
`monitor_display` attribute at all, no `continue`, no nulling). The
assertions below encode today's divergence explicitly so Task 5's diff
shows exactly what changed.

`_display_current` is replaced with a no-op recorder rather than exercised
for real: this characterizes only the LOOP's control flow (which branch
fires, which sleep duration, which attributes mutate), not the render
path -- that's covered separately by test_fixed_base_golden.py.
"""

from unittest import mock

import pytest

from tests.ui.fixed_base_harness import make_base

SAMPLE_IN_DATA = {
    "probe_history": {"primary": {"Grill": 225}, "food": {"Probe1": 145}},
    "primary_setpoint": 225,
    "notify_targets": {"Grill": 0, "Probe1": 165},
}
SAMPLE_STATUS_DATA = {
    "mode": "Smoke",
    "outpins": {"fan": True, "igniter": False, "auger": False},
    "notify_data": [],
    "recipe_paused": False,
    "recipe": False,
    "s_plus": False,
    "hopper_level_enabled": True,
    "hopper_level": 80,
    "p_mode": 2,
    "units": "F",
}


class _StopLoop(Exception):
    """Raised from the mocked `time.sleep` to unwind `_display_loop`'s
    `while True` deterministically after exactly one iteration."""


def _drive_one_iteration(base):
    """Run `base._display_loop()` until its first `time.sleep` call, then stop.

    Returns a dict recording the sleep call's argument and how many times
    the `_display_current` recorder fired. Also patches `os.system` for
    the duration of the call as defense-in-depth: `_display_loop` does not
    call `_menu_display` (the `sudo reboot` path) on this render-only
    branch, but a hard block here costs nothing and this repo has a history
    of real reboot incidents from unmocked `os.system` in display code.
    """
    calls = {"display_current": 0, "sleep_arg": None}

    def _record_display_current(in_data, status_data):
        calls["display_current"] += 1

    def _sleep_then_stop(seconds):
        calls["sleep_arg"] = seconds
        raise _StopLoop

    base._display_current = _record_display_current
    base.input_enabled = False
    base.display_active = True
    base.display_timeout = None
    base.display_command = None
    base.in_data = dict(SAMPLE_IN_DATA)
    base.status_data = dict(SAMPLE_STATUS_DATA)

    with (
        mock.patch("os.system", side_effect=AssertionError("os.system must not be reached by _display_loop here")),
        mock.patch("time.time", return_value=1_000_000.0),
        mock.patch("time.sleep", side_effect=_sleep_then_stop),
    ):
        try:
            base._display_loop()
        except _StopLoop:
            pass

    return calls


# (short_name, module, expected_sleep_seconds, has_monitor_display_attr,
#  expects_monitor_display_true, expects_data_nulled)
LOOP_CASES = [
    # 240x240: renders once via the `elif ... display_active` branch,
    # nulls in_data/status_data, then since monitor_display started False
    # it flips True and sleeps `clear_delay` (1s) -- base_240x240.py:294-320.
    ("240x240", "display.base_240x240", 1, True, True, True),
    # 240x320 / 320x480: renders via the identical `elif` branch but never
    # nulls the data, has no monitor_display concept at all, and always
    # falls through to the flat `time.sleep(0.1)` at the bottom of the loop
    # -- base_240x320.py:284-292, base_320x480.py:284-292.
    ("240x320", "display.base_240x320", 0.1, False, False, False),
    ("320x480", "display.base_320x480", 0.1, False, False, False),
]


@pytest.mark.parametrize("case", LOOP_CASES, ids=[c[0] for c in LOOP_CASES])
def test_one_iteration_of_display_loop(case):
    short, module, expected_sleep, has_monitor_display_attr, expects_monitor_display_true, expects_data_nulled = case
    base = make_base(module)

    calls = _drive_one_iteration(base)

    # _display_current fires exactly once on all three -- the one shared
    # behavior across the divergence.
    assert calls["display_current"] == 1, f"{short}: _display_current should fire exactly once per iteration"

    # The sleep duration that terminates the iteration is the crux of the
    # Task 5 divergence: 240x240 uses clear_delay (1s) on this first-render
    # path; the two large bases always use a flat 0.1s.
    assert calls["sleep_arg"] == expected_sleep, (
        f"{short}: expected the terminating time.sleep({expected_sleep!r}), got {calls['sleep_arg']!r}"
    )

    # monitor_display: only 240x240 has this attribute/flag at all.
    assert hasattr(base, "monitor_display") == has_monitor_display_attr, (
        f"{short}: monitor_display attribute presence should be {has_monitor_display_attr}"
    )
    if has_monitor_display_attr:
        assert base.monitor_display is expects_monitor_display_true, (
            f"{short}: expected monitor_display flipped to {expects_monitor_display_true} on first full render"
        )

    # in_data/status_data nulling after _display_current: only 240x240 does this.
    if expects_data_nulled:
        assert base.in_data is None, f"{short}: expected in_data nulled after _display_current"
        assert base.status_data is None, f"{short}: expected status_data nulled after _display_current"
    else:
        assert base.in_data is not None, f"{short}: does not null in_data after render today (Task 5 may change this)"
        assert base.status_data is not None, (
            f"{short}: does not null status_data after render today (Task 5 may change this)"
        )
