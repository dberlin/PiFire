"""Read programmed EMC electrical duty, independently of command/status caches."""

from threading import RLock
from time import monotonic


class EmcPwmReadback:
    """Observe manual electrical settings, excluding autonomous startup intervals.

    The platform owns all writes through ``write``. Startup history only gates
    evidence; it never supplies a duty value or changes a hardware command.
    This does not measure rotation or airflow, and never reads a tachometer.
    """

    def __init__(self, emc, chip):
        self.emc = emc
        self.chip = chip
        self._lock = RLock()
        self._last_raw = None
        self._startup_until = None

    def _settings(self):
        registers = (
            (b"\x2a", b"\x30", b"\x32", b"\x33", b"\x36")
            if self.chip == "emc2301"
            else (b"\x03", b"\x4a", b"\x4b", b"\x4c", b"\x4d")
        )
        settings = bytearray(5)
        with self.emc.i2c_device as i2c:
            for index, register in enumerate(registers):
                i2c.write_then_readinto(register, settings, in_start=index, in_end=index + 1)
        if self.chip == "emc2301":
            polarity, raw, config1, config2, spinup = settings
            # EN_ALGO and EN_RRC permit autonomous changes, not constant duty.
            manual = not (config1 & 0x80 or config2 & 0x40)
            # Direct mode does not retry spin-up after this maximum (§5.12).
            duration = (0.25, 0.5, 1.0, 2.0)[spinup & 0x03]
            return raw, 255, bool(polarity & 0x01), duration, manual
        config, fan_config, spinup, raw, pwm_f = settings
        manual = not config & 0x10 and bool(fan_config & 0x20)
        # SPIN_TIME=0 bypasses startup. FAST_TACH requires ALT_TCH input mode;
        # when enabled it overrides SPIN_DRIVE, but not SPIN_TIME (§6.17).
        duration = (0.0, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2)[spinup & 0x07]
        if not (spinup & 0x18 or (spinup & 0x20 and config & 0x04)):
            duration = 0.0
        # Read live PWM_F, not Adafruit's cached full-scale divisor.
        return raw & 0x3F, 2 * max(1, pwm_f & 0x1F), bool(fan_config & 0x10), duration, manual

    def _observe_startup(self, raw, duration, now):
        if raw == 0:
            self._startup_until = None
        elif duration > 0 and self._last_raw in (None, 0) and self._startup_until is None:
            # Unknown initial history gets one bounded quarantine too.
            self._startup_until = now + duration
        self._last_raw = raw

    def write(self, percent):
        """Keep the original command while recording a possible raw 0->on edge."""
        with self._lock:
            raw = None
            duration = 2.0 if self.chip == "emc2301" else 3.2
            if percent > 0:
                try:
                    raw, _, _, duration, _ = self._settings()
                    self._observe_startup(raw, duration, monotonic())
                except Exception:
                    # Missing pre-write evidence uses the chip's maximum bound.
                    # This observer must not prevent the original command.
                    self._last_raw = None
            try:
                self.emc.manual_fan_speed = percent
            except Exception:
                # A failed transfer may nevertheless have reached the chip.
                self._last_raw = None
                self._startup_until = None
                raise
            if percent == 0:
                # Never delay an off command for optional observation reads.
                # Only a fresh register read can certify that it took effect.
                self._last_raw = None
                self._startup_until = None
            if percent > 0 and raw in (None, 0) and duration > 0:
                # Time after the write is conservative; never restart this for
                # another nonzero write while the same startup is in progress.
                self._startup_until = monotonic() + duration

    def read(self, requested_percent, logger):
        """Return fresh decoded percent, or None while output is unobservable."""
        if self.chip not in ("emc2101", "emc2301"):
            return None
        with self._lock:
            # A snapshot begun before the deadline is not post-startup evidence.
            now = monotonic()
            raw, full_scale, inverted, duration, manual = self._settings()
            if not manual:
                self._last_raw = None
                self._startup_until = None
                return None
            self._observe_startup(raw, duration, monotonic())
            if self._startup_until is not None and now < self._startup_until:
                return None
            observed = min(100.0, (raw / full_scale) * 100.0)
            if inverted:
                observed = 100.0 - observed
            expected = (round((requested_percent / 100.0) * full_scale) / full_scale) * 100.0
            if observed != expected:
                logger.warning(
                    "PWM readback differs from programmed expectation: chip=%s requested=%s%% expected=%s%% observed=%s%%",
                    self.chip,
                    requested_percent,
                    expected,
                    observed,
                )
            return observed
