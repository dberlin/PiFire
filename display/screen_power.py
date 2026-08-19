"""Screen power control keyed on display kind.

Only the ``wayland`` kind is implemented: it drives ``swaymsg output * dpms
on|off`` to DPMS the compositor's outputs off/on. Unlike disabling an output
(``wlr-randr --off``), DPMS leaves the output enabled and mapped, so a touch
still routes to the app and wakes the screen — the whole reason for moving off
cage's ``wlr-randr`` path. Other kinds are safe no-ops so callers can construct
and drive a controller unconditionally.
"""

import subprocess

from display._loggers import resolve_loggers


class ScreenPowerController:
    def __init__(self, display_kind, run=subprocess.run, *, control_log=None):
        self._kind = display_kind
        self._run = run
        # The one message here is a traceback, which is the control logger's
        # territory. It used to go to a "screen_power" logger that nothing ever
        # passes to create_logger, so it reached no file at all.
        self._log = resolve_loggers(None, control_log)[1]

    def set_output_power(self, on):
        """DPMS every output on (True) or off (False). No-op if not wayland.
        Never raises into the caller."""
        if self._kind != "wayland":
            return
        state = "on" if on else "off"
        try:
            self._run(["swaymsg", "output", "*", "dpms", state], capture_output=True, text=True, timeout=5)
        except OSError, subprocess.SubprocessError:
            self._log.exception("swaymsg dpms toggle failed")
