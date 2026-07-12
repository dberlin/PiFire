#!/usr/bin/env python3

# *****************************************
# PiFire FT232H IO-Triggered Relay Platform Interface Library
# *****************************************
#
# Description: Controls PiFire outputs on any host using an FT232H USB breakout
#   as a GPIO expander.  Each output (power, igniter, auger, fan) drives one
#   input of an IO-triggered relay board via an FT232H GPIO pin (Adafruit Blinka
#   digitalio).  An alternative to a directly-wired relay board.
#
#   The fan is selectable via fan_controller.chip:
#     'none'                 -> the fan is a plain relay (on/off).
#     'emc2101' / 'emc2301'  -> the fan relay gates power and an EMC controller
#                               on the FT232H I2C bus sets fan speed.
#
# *****************************************

import os
import threading

from common import create_logger

import busio
from adafruit_emc2101.emc2101_lut import EMC2101_LUT

from grillplat.emc2301 import EMC2301


# Default FT232H pin name per PiFire output.  The C-bank keeps the I2C pins
# (D0=SCL, D1/D2=SDA) free for the EMC fan controller.
_DEFAULT_OUTPUTS = {'power': 'C0', 'igniter': 'C1', 'auger': 'C2', 'fan': 'C3'}


def _load_ft232h(url='1'):
	"""Enable Blinka's FT232H backend and import board + digitalio.

	Isolated so importing this module never opens USB hardware, and so tests can
	patch it to inject fakes.  `url` is assigned to BLINKA_FT232H before importing
	board: '1' selects the first FT232H; a pyftdi URL selects a specific device.
	"""
	os.environ['BLINKA_FT232H'] = str(url)
	import board
	import digitalio

	return board, digitalio


class _Relay:
	"""One relay-board input driven by an FT232H GPIO pin.

	digitalio has no active_high parameter, so trigger polarity is applied
	explicitly: an active-LOW board asserts the relay by driving the pin low.
	"""

	def __init__(self, dio, active_high):
		self._dio = dio
		self._active_high = active_high
		self._state = False
		self.off()

	def on(self):
		self._dio.value = self._active_high
		self._state = True

	def off(self):
		self._dio.value = not self._active_high
		self._state = False

	@property
	def is_active(self):
		return self._state

	def close(self):
		self._dio.deinit()


class GrillPlatform:
	def __init__(self, config):
		self.logger = create_logger('control')
		self.config = config

		outputs = config.get('outputs', {}) or {}
		self.pin_map = {name: str(outputs.get(name, default)) for name, default in _DEFAULT_OUTPUTS.items()}

		ft232h_cfg = config.get('ft232h', {}) or {}
		self.url = ft232h_cfg.get('url', '1')

		fan_cfg = config.get('fan_controller', {}) or {}
		self.chip = str(fan_cfg.get('chip', 'none')).lower()
		self.pwm_fan = self.chip in ('emc2101', 'emc2301')

		address = fan_cfg.get('address')
		if address is None:
			address = 0x2F if self.chip == 'emc2301' else 0x4C
		elif isinstance(address, str):
			address = int(address, 16)
		self.emc_address = address

		self.frequency = config.get('frequency', 25000)
		self.standalone = config.get('standalone', True)

		active_high = config.get('triggerlevel', 'LOW') == 'HIGH'

		# Cached commanded output state (avoids reading hardware per poll).
		self._output_state = {'auger': False, 'fan': False, 'igniter': False, 'power': False}
		self._fan_speed_percent = 0

		# Fan ramp control (EMC mode).
		self._ramp_thread = None
		self._ramp_stop = threading.Event()

		# Open the FT232H and create one output pin per PiFire output.
		board, digitalio = _load_ft232h(self.url)
		self.relays = {}
		for name, pin_name in self.pin_map.items():
			try:
				pin = getattr(board, pin_name)
			except AttributeError:
				raise ValueError(f'Unknown FT232H pin {pin_name!r} for output {name!r}')
			dio = digitalio.DigitalInOut(pin)
			dio.direction = digitalio.Direction.OUTPUT
			self.relays[name] = _Relay(dio, active_high)

		# Open the fan controller if PWM fan mode is selected (Task 2).
		self.emc = None
		if self.pwm_fan:
			self._init_fan_controller(board)

	def _init_fan_controller(self, board):
		# Implemented in Task 2.
		pass

	# MARK: Output control
	def _set_output(self, name, state):
		relay = self.relays[name]
		if state:
			relay.on()
		else:
			relay.off()
		self._output_state[name] = state

	def auger_on(self):
		self.logger.debug('auger_on: Turning on auger')
		self._set_output('auger', True)

	def auger_off(self):
		self.logger.debug('auger_off: Turning off auger')
		self._set_output('auger', False)

	def igniter_on(self):
		self.logger.debug('igniter_on: Turning on igniter')
		self._set_output('igniter', True)

	def igniter_off(self):
		self.logger.debug('igniter_off: Turning off igniter')
		self._set_output('igniter', False)

	def power_on(self):
		self.logger.debug('power_on: Powering on grill platform')
		self._set_output('power', True)

	def power_off(self):
		self.logger.debug('power_off: Powering off grill platform')
		self._set_output('power', False)

	def get_input_status(self):
		# No selector/shutdown inputs on this platform.
		return False

	# MARK: Fan / PWM control
	def fan_on(self, fan_speed_percent=100):
		self.logger.debug('fan_on: Enabling fan power, speed ' + str(fan_speed_percent))
		self._set_output('fan', True)
		if self.pwm_fan:
			self._stop_ramp()
			self.set_duty_cycle(fan_speed_percent)

	def fan_off(self):
		self.logger.debug('fan_off: Stopping fan and removing power')
		if self.pwm_fan:
			self._stop_ramp()
			self.emc.manual_fan_speed = 0
			self._fan_speed_percent = 0
		self._set_output('fan', False)

	def fan_toggle(self):
		if self._output_state['fan']:
			self.fan_off()
		else:
			self.fan_on()

	def set_duty_cycle(self, fan_speed_percent, override_ramping=True):
		if not self.pwm_fan:
			return
		if override_ramping:
			self._stop_ramp()
		fan_speed_percent = max(0, min(100, fan_speed_percent))
		self.emc.manual_fan_speed = fan_speed_percent
		self._fan_speed_percent = fan_speed_percent

	def set_pwm_frequency(self, frequency=25000):
		# Record the requested value so control.py's "re-apply if changed"
		# comparison settles even in relay-only mode.
		self.frequency = frequency
		if not self.pwm_fan:
			return
		try:
			if self.chip == 'emc2301':
				self.emc.pwm_frequency = frequency
			else:
				pwm_f = max(1, min(31, round(360000 / (2 * frequency))))
				self.emc.set_pwm_clock(use_preset=False, use_slow=False)
				self.emc.pwm_frequency_divisor = 1
				self.emc.pwm_frequency = pwm_f
		except (ValueError, OSError, AttributeError) as exc:
			self.logger.warning('set_pwm_frequency: controller rejected frequency: ' + str(exc))

	def _stop_ramp(self):
		if self._ramp_thread is not None:
			self._ramp_stop.set()
			if self._ramp_thread is not threading.current_thread():
				self._ramp_thread.join(timeout=5)
			self._ramp_thread = None

	def pwm_fan_ramp(self, on_time=5, min_duty_cycle=20, max_duty_cycle=100):
		self._set_output('fan', True)
		if not self.pwm_fan:
			return
		self._start_ramp(on_time, min_duty_cycle, max_duty_cycle)

	def _start_ramp(self, on_time, min_duty_cycle, max_duty_cycle):
		self._stop_ramp()
		self._ramp_stop = threading.Event()
		self._ramp_thread = threading.Thread(
			target=self._ramp_device, args=(on_time, min_duty_cycle, max_duty_cycle), daemon=True
		)
		self._ramp_thread.start()

	def _ramp_device(self, on_time, min_duty_cycle, max_duty_cycle, fps=25):
		steps = max(int(fps * on_time), 1)
		for i in range(steps):
			fraction = i / steps
			percent = min_duty_cycle + (max_duty_cycle - min_duty_cycle) * fraction
			self.set_duty_cycle(round(percent, 2), override_ramping=False)
			if self._ramp_stop.wait(1.0 / fps):
				break
		self.set_duty_cycle(max_duty_cycle, override_ramping=False)

	# MARK: Lifecycle
	def cleanup(self):
		self.logger.debug('cleanup: Shutting down outputs')
		self._stop_ramp()
		if self.pwm_fan and self.emc is not None:
			try:
				self.emc.manual_fan_speed = 0
			except Exception:
				pass
		for relay in self.relays.values():
			try:
				relay.off()
			finally:
				relay.close()

	def get_output_status(self):
		self.current = {
			'auger': self._output_state['auger'],
			'igniter': self._output_state['igniter'],
			'power': self._output_state['power'],
			'fan': self._output_state['fan'],
		}
		if self.pwm_fan:
			self.current['pwm'] = self._fan_speed_percent
			self.current['frequency'] = self.frequency
		return self.current
