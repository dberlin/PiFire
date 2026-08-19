"""The Qt display child is spawned, not forked, so it inherits no logging.

`multiprocessing.get_context("spawn")` starts a fresh interpreter: every logger
in the child begins with no handlers, and `create_logger` is what attaches them.
The display process does this for itself at startup; the child it spawns did
not, so anything the Qt renderer logged went nowhere at all.
"""

import logging

import display.qtquick_flex as qtquick_flex


def _detach(name):
    logger = logging.getLogger(name)
    saved = list(logger.handlers)
    for handler in saved:
        logger.removeHandler(handler)
    return logger, saved


def test_the_spawned_child_configures_the_loggers_it_will_use():
    events, events_saved = _detach("events")
    control, control_saved = _detach("control")
    try:
        assert not events.handlers and not control.handlers  # a fresh child
        qtquick_flex._configure_child_logging()
        assert events.handlers, "child left the events logger unconfigured"
        assert control.handlers, "child left the control logger unconfigured"
    finally:
        for logger, saved in ((events, events_saved), (control, control_saved)):
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
            for handler in saved:
                logger.addHandler(handler)


def test_the_child_entry_point_configures_before_it_renders():
    """Ordering matters: run_app builds the dispatcher and the backlight, both
    of which log. Configuring after that point would lose the startup messages
    the operator most wants when a display fails to come up."""
    calls = []

    def _fake_configure():
        calls.append("configure")

    def _fake_run_app(config, units):
        calls.append("run_app")

    original_configure = qtquick_flex._configure_child_logging
    qtquick_flex._configure_child_logging = _fake_configure
    import display.qtapp as qtapp

    original_run_app = qtapp.run_app
    qtapp.run_app = _fake_run_app
    try:
        qtquick_flex._run_qt_app({}, "F")
    finally:
        qtquick_flex._configure_child_logging = original_configure
        qtapp.run_app = original_run_app

    assert calls == ["configure", "run_app"]
