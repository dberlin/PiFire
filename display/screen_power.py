"""Screen power control keyed on display kind.

Only the ``wayland`` kind is implemented: it drives ``wlr-randr`` to power the
compositor output off/on (cage supports zwlr_output_manager_v1). Other kinds are
safe no-ops so callers can construct and drive a controller unconditionally.
"""

import logging
import subprocess

log = logging.getLogger("screen_power")


class ScreenPowerController:
    def __init__(self, display_kind, run=subprocess.run):
        self._kind = display_kind
        self._run = run
        self._output = None

    def resolve_output(self):
        """Return the compositor output name (cached), or None if unavailable."""
        if self._kind != "wayland":
            return None
        if self._output:
            return self._output
        try:
            proc = self._run(["wlr-randr"], capture_output=True, text=True, timeout=5)
        except OSError, subprocess.SubprocessError:
            log.exception("wlr-randr failed to run")
            return None
        self._output = self._parse_output_name(proc.stdout)
        return self._output

    @staticmethod
    def _parse_output_name(text):
        # wlr-randr prints each head starting at column 0: `DP-1 "..."`;
        # indented lines are that head's properties. Take the first head.
        for line in text.splitlines():
            if line and not line[0].isspace():
                return line.split()[0]
        return None

    def set_output_power(self, on):
        """Power the output on (True) or off (False). No-op if not wayland or
        no output could be resolved. Never raises into the caller."""
        if self._kind != "wayland":
            return
        name = self.resolve_output()
        if not name:
            return
        flag = "--on" if on else "--off"
        try:
            self._run(["wlr-randr", "--output", name, flag], capture_output=True, text=True, timeout=5)
        except OSError, subprocess.SubprocessError:
            log.exception("wlr-randr power toggle failed")
