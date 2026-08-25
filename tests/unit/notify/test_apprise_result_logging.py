"""Pins success/failure logging for _send_apprise_notifications.

F841 latent bug: the Apprise ``.notify()`` result was assigned but never
checked, so the generic Apprise path logged neither success nor failure (and had
no exception guard) -- unlike the sibling _send_apprise_url. These tests mirror
the sibling's behavior: result True -> debug success, result False -> warning
failure, and an exception is guarded and logged as a warning.
"""

from unittest.mock import MagicMock

import notify.notifications as N


def _settings():
    return {
        "globals": {"debug_mode": True},
        "notify_services": {"apprise": {"locations": ["json://localhost"], "enabled": True}},
    }


def _patch(monkeypatch, notify_return=None, notify_side_effect=None):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)

    handler = MagicMock()
    if notify_side_effect is not None:
        handler.notify.side_effect = notify_side_effect
    else:
        handler.notify.return_value = notify_return
    monkeypatch.setattr(N.apprise, "Apprise", lambda: handler)
    return logger, handler


def test_apprise_success_logs_debug(monkeypatch):
    logger, handler = _patch(monkeypatch, notify_return=True)

    N._send_apprise_notifications(_settings(), "Title", "Body")

    handler.notify.assert_called_once()
    assert any("success" in str(c.args[0]).lower() for c in logger.debug.call_args_list)
    # No failure warning on success.
    assert not any("failed" in str(c.args[0]).lower() for c in logger.warning.call_args_list)


def test_apprise_failure_logs_warning(monkeypatch):
    logger, _handler = _patch(monkeypatch, notify_return=False)

    N._send_apprise_notifications(_settings(), "Title", "Body")

    assert any("failed" in str(c.args[0]).lower() for c in logger.warning.call_args_list)


def test_apprise_exception_is_guarded_and_logged(monkeypatch):
    logger, _handler = _patch(monkeypatch, notify_side_effect=RuntimeError("boom"))

    # Must not propagate -- sibling guards notify() in try/except.
    N._send_apprise_notifications(_settings(), "Title", "Body")

    assert any("failed" in str(c.args[0]).lower() for c in logger.warning.call_args_list)
