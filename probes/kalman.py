"""
Constant-velocity Kalman filter for smoothing probe temperature readings.

Estimates both temperature and its rate of change, so it tracks ramps with
little lag while smoothing noise. A Hampel prefilter drops outliers before they
reach the estimator. Applied centrally to every probe module's output in
ProbesMain.read_probes() via ProbeInterface.apply_filters().
"""

import statistics
import time

# Tuning constants selected by units at construction.
#   R         : measurement variance (sensor noise squared)
#   q         : white-acceleration process-noise spectral density
#   mad_floor : smallest spread the outlier test will assume, so a sensor that
#               reads perfectly steady (a median absolute deviation of zero)
#               does not go on to reject every reading that follows
_TUNING = {
    "F": {"R": 4.0, "q": 0.5, "mad_floor": 0.5},
    "C": {"R": 1.25, "q": 0.15, "mad_floor": 0.28},
}

# Outlier rejection. A reading further than _HAMPEL_SIGMAS robust sigmas from
# the median of its window is replaced by that median. The window length sets
# both halves of the trade at once: it absorbs up to (_HAMPEL_WINDOW - 1) // 2
# consecutive bad samples, and admits a change that persists for
# (_HAMPEL_WINDOW + 1) // 2 samples. Judging a reading against its neighbours
# rather than against the estimate is what keeps a real jump from being
# suppressed indefinitely -- the window fills with the new level and the median
# follows it, whichever way the estimate happens to be pointing.
_HAMPEL_WINDOW = 11
_HAMPEL_SIGMAS = 3.0
_MAD_TO_SIGMA = 1.4826

_DT_MIN = 0.01
_DT_MAX = 1.0
_NONE_RESET_THRESHOLD = 3


class TempKalman:
    def __init__(self, units="F"):
        tuning = _TUNING["C"] if units == "C" else _TUNING["F"]
        self.units = units
        self.R = tuning["R"]
        self.q = tuning["q"]
        self.mad_floor = tuning["mad_floor"]
        self.reset()

    def reset(self):
        self.x = None  # temperature estimate
        self.v = 0.0  # rate estimate (deg/sec)
        self.P = [[self.R, 0.0], [0.0, self.R]]
        self.last_time = None
        self.none_streak = 0
        self.window = []  # recent raw readings, for the Hampel test
        self.outlier = False  # True if the last reading was replaced (debug)

    def _prefilter(self, reading):
        """Replace a reading that disagrees with its neighbours by the median of
        the window, so a glitch never reaches the estimator. Readings pass
        through untested until the window fills, which costs the first
        _HAMPEL_WINDOW samples after a start or a reset."""
        self.window.append(reading)
        if len(self.window) > _HAMPEL_WINDOW:
            self.window.pop(0)
        if len(self.window) < _HAMPEL_WINDOW:
            return reading

        median = statistics.median(self.window)
        mad = statistics.median([abs(w - median) for w in self.window])
        spread = max(_MAD_TO_SIGMA * mad, self.mad_floor)
        if abs(reading - median) > _HAMPEL_SIGMAS * spread:
            self.outlier = True
            return median
        return reading

    def update(self, reading, now=None):
        if reading is None:
            self.none_streak += 1
            if self.none_streak >= _NONE_RESET_THRESHOLD:
                self.reset()
            return None

        self.none_streak = 0
        self.outlier = False
        if now is None:
            now = time.monotonic()
        reading = self._prefilter(float(reading))

        # First valid reading (fresh or post-reset): initialize, don't predict.
        if self.x is None or self.last_time is None:
            self.x = reading
            self.v = 0.0
            self.P = [[self.R, 0.0], [0.0, self.R]]
            self.last_time = now
            return round(self.x, 1)

        dt = now - self.last_time
        if dt < _DT_MIN:
            dt = _DT_MIN
        elif dt > _DT_MAX:
            dt = _DT_MAX
        self.last_time = now

        # --- Predict: x = F x ; P = F P F^T + Q  (F = [[1, dt], [0, 1]]) ---
        self.x += self.v * dt
        P = self.P
        p00 = P[0][0] + dt * (P[1][0] + P[0][1]) + dt * dt * P[1][1]
        p01 = P[0][1] + dt * P[1][1]
        p10 = P[1][0] + dt * P[1][1]
        p11 = P[1][1]
        dt2 = dt * dt
        dt3 = dt2 * dt
        dt4 = dt3 * dt
        p00 += self.q * dt4 / 4.0
        p01 += self.q * dt3 / 2.0
        p10 += self.q * dt3 / 2.0
        p11 += self.q * dt2

        # --- Update (measure temperature only, H = [1, 0]) ---
        y = reading - self.x
        s = p00 + self.R
        k0 = p00 / s
        k1 = p10 / s
        self.x += k0 * y
        self.v += k1 * y
        self.P = [[(1 - k0) * p00, (1 - k0) * p01], [p10 - k1 * p00, p11 - k1 * p01]]
        return round(self.x, 1)
