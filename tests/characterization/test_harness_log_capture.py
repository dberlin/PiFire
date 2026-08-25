"""Pins WHAT the characterization harness captures from a work cycle's logging.

The harness used to redirect controller logging by rebinding the
`control.eventLogger` / `control.controlLogger` module globals. The modes now
log through the injected `ControllerContext`, so the harness redirects by
injecting the `characterization` logger instead. This test pins the captured
records so the two mechanisms can be compared directly: it passes before and
after that migration, and fails if the harness stops capturing.
"""

import logging

from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization.harness import make_ctx, run_mode
from tests.fakes.probes import FakeProbes


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append((record.levelname, record.getMessage()))


def _captured_messages(records, levels=("INFO", "DEBUG")):
    return [message for level, message in records if level in levels]


def test_harness_captures_the_work_cycles_own_logging():
    logger = logging.getLogger("characterization")
    handler = _Capture()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        run_mode(
            "Monitor",
            settings=base_settings(),
            control_data=base_control(mode="Monitor"),
            pellet_db=base_pellet_db(),
            probes=FakeProbes().script([120] * 10),
            probe_cap=3,
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    messages = _captured_messages(handler.records)
    # The shared skeleton's start/end pair (base.ControlMode.run) ...
    assert "Monitor Mode started." in messages
    assert "Monitor mode ended." in messages
    # ... and the mode's own setup/teardown lines.
    assert "Power OFF, Fan OFF, Igniter OFF, Auger OFF" in messages
    assert "Fan OFF, Power OFF" in messages


def test_the_harness_context_carries_the_capture_logger():
    """The injected context carries the capture logger the globals once held."""
    ctx, _grill, _notifier = make_ctx(
        base_settings(),
        base_control(mode="Monitor"),
        base_pellet_db(),
        FakeProbes().script([120]),
    )

    assert ctx.event_log is logging.getLogger("characterization")
    assert ctx.control_log is logging.getLogger("characterization")
