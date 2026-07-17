"""Characterization test for `_display_loop`'s per-iteration behavior.

`_display_loop` is an infinite `while True` loop that the pixel-snapshot
harness (test_fixed_base_golden.py) cannot reach -- it never returns. This
test drives exactly ONE iteration deterministically by making the loop's
own `time.sleep` call raise a sentinel exception on its first invocation,
then asserts what that one iteration actually did.

This is the "after" side of Phase B Task 5, which reconciled the two
divergent loops into ONE: base_fixed now carries the richer (former
240x240) loop for every resolution -- a `self.monitor_display` flag,
distinct `self.loop_delay` / `self.clear_delay` sleeps, a `continue`
after each state transition, and nulling `in_data`/`status_data` after
render. The only knob that still varies per display is the
post-transition settle, exposed as the `min_transition_delay` class
attribute and copied into `self.clear_delay` by `_init_globals`:
1.0s on the 240x240 shim (st7789e's existing pacing) and 0.1s on the two
large shims. The 15 large-base panels thus moved from a flat
`time.sleep(0.1)` loop to the richer loop with a 0.1s (i.e. effectively
unchanged, == the steady cadence) transition settle. The assertions
below encode that now-unified behavior.

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


# (short_name, module, expected_sleep_seconds)
#
# After Task 5 all three run the SAME richer loop: each renders once via the
# `elif ... display_active` branch, nulls in_data/status_data, and -- since
# monitor_display starts False -- flips it True and sleeps `clear_delay` on
# this first full render. clear_delay == the shim's `min_transition_delay`,
# the ONLY per-resolution difference: 1.0s for the slow 240x240 (st7789e),
# 0.1s for the two large panels (their former flat-loop cadence, so their
# transition settle is effectively unchanged).
LOOP_CASES = [
    ("240x240", "display.base_240x240", 1.0),
    ("240x320", "display.base_240x320", 0.1),
    ("320x480", "display.base_320x480", 0.1),
]


@pytest.mark.parametrize("case", LOOP_CASES, ids=[c[0] for c in LOOP_CASES])
def test_one_iteration_of_display_loop(case):
    short, module, expected_sleep = case
    base = make_base(module)

    calls = _drive_one_iteration(base)

    # _display_current fires exactly once on all three -- the shared render step.
    assert calls["display_current"] == 1, f"{short}: _display_current should fire exactly once per iteration"

    # The sleep that terminates this first-render iteration is `clear_delay`,
    # which equals the shim's `min_transition_delay`: 1.0s for 240x240, 0.1s
    # for the two large panels.
    assert calls["sleep_arg"] == expected_sleep, (
        f"{short}: expected the terminating time.sleep({expected_sleep!r}), got {calls['sleep_arg']!r}"
    )
    assert base.clear_delay == expected_sleep == base.min_transition_delay, (
        f"{short}: clear_delay/min_transition_delay should both be {expected_sleep!r}"
    )

    # All three now run the richer loop: monitor_display exists and flips True
    # on the first full render.
    assert hasattr(base, "monitor_display"), f"{short}: unified loop must set the monitor_display flag"
    assert base.monitor_display is True, f"{short}: expected monitor_display flipped True on first full render"

    # All three now null in_data/status_data after _display_current.
    assert base.in_data is None, f"{short}: expected in_data nulled after _display_current"
    assert base.status_data is None, f"{short}: expected status_data nulled after _display_current"
