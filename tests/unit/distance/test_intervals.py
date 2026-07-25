"""The hopper refresh cadence, pinned.

The dashboard no longer carries a "Refresh Status" button (the repo owner's
call: poll automatically instead). That makes these two constants the only
thing standing between the user and a stale hopper reading, so their values
-- and, more importantly, their ORDERING -- are asserted here rather than
left as literals nobody revisits.
"""

from distance.intervals import HOPPER_LEVEL_REFRESH_INTERVAL, SENSOR_SAMPLE_INTERVAL


def test_control_loop_polls_about_every_ten_seconds():
    # "Like every 10 seconds or something." A reading that is minutes old is
    # the thing the automatic refresh exists to prevent.
    assert 5 <= HOPPER_LEVEL_REFRESH_INTERVAL <= 15


def test_sensors_sample_faster_than_the_control_loop_polls():
    # The control loop reads a CACHE. If the sensors sampled more slowly than
    # the loop polls, the poll would keep re-reading the same stale sample and
    # the extra polling would buy nothing.
    assert SENSOR_SAMPLE_INTERVAL < HOPPER_LEVEL_REFRESH_INTERVAL


def test_sampling_interval_leaves_room_for_the_threads_one_second_pacing():
    # _sensing_loop sleeps 1s between checks, so the real period is the
    # interval plus up to a second. Anything near 1s would be dominated by
    # that granularity rather than controlled by the constant.
    assert SENSOR_SAMPLE_INTERVAL >= 4
