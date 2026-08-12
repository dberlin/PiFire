"""Sampling helpers shared by the transport test modules.

_await_sample was copy-pasted identically into all three transport test
files. Named without a `test_` prefix so pytest does not collect it.
"""

import time as _real_time


def await_sample(hopper, count=1, timeout=2.0):
    """Wait until the sampling thread has completed `count` samples.

    Polls `sample_count` rather than waiting on the driver, because the driver
    deliberately offers NO way to wait for a measurement -- that blocking
    primitive was removed so the control loop cannot reacquire it. Tests are
    allowed to wait; the control loop is not."""
    deadline = _real_time.monotonic() + timeout
    while hopper.sample_count < count:
        if _real_time.monotonic() > deadline:
            raise AssertionError(f"sampling thread produced {hopper.sample_count} samples, wanted {count}")
        _real_time.sleep(0.005)
    return hopper.get_level()
