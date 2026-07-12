#!/usr/bin/env python3

"""
Constant-velocity Kalman filter for smoothing probe temperature readings.

Replaces the standard-deviation-gated moving average (TempQueue). Estimates
both temperature and its rate of change, so it tracks ramps with little lag
while smoothing noise.
"""

import time

# Tuning constants selected by units at construction.
#   R    : measurement variance (sensor noise squared)
#   q    : white-acceleration process-noise spectral density
#   gate : reject readings farther than this many sigma from the prediction
_TUNING = {'F': {'R': 4.0, 'q': 0.5, 'gate': 5.0}, 'C': {'R': 1.25, 'q': 0.15, 'gate': 5.0}}

_DT_MIN = 0.01
_DT_MAX = 1.0
_NONE_RESET_THRESHOLD = 3


class TempKalman:
	def __init__(self, units='F'):
		tuning = _TUNING['C'] if units == 'C' else _TUNING['F']
		self.units = units
		self.R = tuning['R']
		self.q = tuning['q']
		self.gate2 = tuning['gate'] ** 2
		self.reset()

	def reset(self):
		self.x = None  # temperature estimate
		self.v = 0.0  # rate estimate (deg/sec)
		self.P = [[self.R, 0.0], [0.0, self.R]]
		self.last_time = None
		self.none_streak = 0
		self.gated = False  # True if the last reading was rejected by the gate (debug)

	def update(self, reading, now=None):
		if reading is None:
			self.none_streak += 1
			if self.none_streak >= _NONE_RESET_THRESHOLD:
				self.reset()
			return None

		self.none_streak = 0
		self.gated = False
		if now is None:
			now = time.monotonic()

		# First valid reading (fresh or post-reset): initialize, don't predict.
		if self.x is None or self.last_time is None:
			self.x = float(reading)
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

		# --- Gate: reject readings too far from the prediction ---
		# The gate cannot latch open: on a reject we keep the predicted P (grown
		# by Q), so s grows and the next reading's y^2/s shrinks -- a sustained
		# real change is admitted within a sample or two.
		y = reading - self.x
		s = p00 + self.R
		if (y * y) / s > self.gate2:
			self.gated = True
			self.P = [[p00, p01], [p10, p11]]
			return round(self.x, 1)

		# --- Update (measure temperature only, H = [1, 0]) ---
		k0 = p00 / s
		k1 = p10 / s
		self.x += k0 * y
		self.v += k1 * y
		self.P = [[(1 - k0) * p00, (1 - k0) * p01], [p10 - k1 * p00, p11 - k1 * p01]]
		return round(self.x, 1)
