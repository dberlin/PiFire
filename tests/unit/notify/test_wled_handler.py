"""Unit tests for notify.wled_handler suggested-preset color selection.

Pins the fix for the F841 latent bug where the cooking branch hardcoded
``orange_cooking`` and ignored the user-configurable ``cooking_color``.
"""

import logging
from unittest.mock import MagicMock

from notify.wled_handler import WLEDNotificationHandler


def _make_handler():
    """Build a handler without running __init__ (which does network I/O)."""
    handler = WLEDNotificationHandler.__new__(WLEDNotificationHandler)
    handler.device_address = "1.2.3.4"
    handler.logger = logging.getLogger("test-wled")
    handler.last_updated = 0
    return handler


def test_cooking_uses_configured_green_color():
    handler = _make_handler()
    handler.send_direct_command = MagicMock()

    handler.send_suggested_preset("cooking", {"cooking_color": "green"})

    assert handler.send_direct_command.called
    assert handler.send_direct_command.call_args.kwargs["color"] == "green"


def test_cooking_defaults_to_blue_when_unconfigured():
    handler = _make_handler()
    handler.send_direct_command = MagicMock()

    handler.send_suggested_preset("cooking", {})

    assert handler.send_direct_command.call_args.kwargs["color"] == "blue"


def test_cooking_does_not_hardcode_orange_cooking():
    """Old buggy behavior always passed color='orange_cooking'; this pins the fix."""
    handler = _make_handler()
    handler.send_direct_command = MagicMock()

    handler.send_suggested_preset("cooking", {"cooking_color": "green"})

    assert handler.send_direct_command.call_args.kwargs["color"] != "orange_cooking"
