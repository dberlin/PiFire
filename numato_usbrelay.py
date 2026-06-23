#!/usr/bin/env python3

'''
*****************************************
PiFire Numato 4 Channel USB Solid State Relay Module Driver
*****************************************

Description:
  Driver for the Numato Lab 4 Channel USB Solid State Relay Module.
  The board enumerates as a USB CDC serial (tty) device and is driven
  by a simple ASCII command protocol terminated with a carriage return.

  Reference: https://numato.com/docs/4-channel-usb-solid-state-relay-module/

  The board provides:
    - 4 solid state relays   (index 0-3)
    - 4 GPIO pins            (IO0-IO3, index 0-3)
    - 4 ADC channels         (ADC0-ADC3, multiplexed with the GPIO pins,
                              10-bit, 0-1023, 0-5V input range)

  Serial parameters (other than flow control) are not significant for this
  board.  Flow control must be 'None'.  921600 8N1 (the largest legal baud
  rate) is used by default.

  Requires: pip3 install pyserial

  Example:

    from numato_usbrelay import NumatoUSBRelay

    with NumatoUSBRelay('/dev/ttyACM0') as board:
        board.relay_on(0)
        print(board.relay_read(0))        # True
        print(board.relay_read_all())     # [True, False, False, False]
        board.gpio_set(1)
        print(board.adc_read(2))          # 0-1023
        board.reset()                     # all relays off
'''

'''
*****************************************
 Imported Libraries
*****************************************
'''
import threading

import serial


'''
*****************************************
 Constants
*****************************************
'''
NUM_RELAYS = 4
NUM_GPIO = 4
NUM_ADC = 4

# The board terminates commands with a carriage return and emits a '>' prompt
# after each response.
_TERMINATOR = b'\r'
_PROMPT = b'>'


'''
*****************************************
 Exceptions
*****************************************
'''
class NumatoError(Exception):
	''' Base exception for the Numato relay driver. '''
	pass


class NumatoResponseError(NumatoError):
	''' Raised when the board returns an unexpected or unparseable response. '''
	pass


'''
*****************************************
 Class Definitions
*****************************************
'''
class NumatoUSBRelay:
	'''
	Driver for the Numato 4 Channel USB Solid State Relay Module.

	The board is addressed over its USB CDC serial (tty) interface.  All
	access is serialized with a lock so the object may be shared between
	threads.
	'''

	def __init__(self, device, baudrate=921600, timeout=1.0):
		'''
		:param device:   Path to the tty device representing the serial port
		                 (e.g. '/dev/ttyACM0' or 'COM3').
		:param baudrate: Serial baud rate.  Any standard rate works; the board
		                 ignores it.  Defaults to 921600 (the largest legal
		                 value).
		:param timeout:  Read timeout in seconds for a single response.
		'''
		self.device = device
		self._lock = threading.Lock()
		# Flow control must be None for this board; pyserial defaults
		# rtscts/xonxoff/dsrdtr to False, which satisfies that requirement.
		self._serial = serial.Serial(
			port=device,
			baudrate=baudrate,
			bytesize=serial.EIGHTBITS,
			parity=serial.PARITY_NONE,
			stopbits=serial.STOPBITS_ONE,
			timeout=timeout,
		)

	'''
	*****************************************
	 Context manager / lifecycle
	*****************************************
	'''
	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc_value, traceback):
		self.close()

	def close(self):
		''' Close the underlying serial port. '''
		if self._serial is not None and self._serial.is_open:
			self._serial.close()

	'''
	*****************************************
	 Low level command handling
	*****************************************
	'''
	def _send_command(self, command):
		'''
		Write a command to the board and return the meaningful portion of the
		response as a string.

		The board echoes the command back, then emits the response (if any),
		then a '>' prompt.  This method strips the echo and the prompt and
		returns only the response token(s).
		'''
		payload = command.encode('ascii') + _TERMINATOR
		with self._lock:
			self._serial.reset_input_buffer()
			self._serial.write(payload)
			self._serial.flush()
			raw = self._read_until_prompt()
		return self._extract_response(command, raw)

	def _read_until_prompt(self):
		''' Read bytes from the board until the '>' prompt or read timeout. '''
		buffer = bytearray()
		while True:
			chunk = self._serial.read(1)
			if not chunk:
				# Timed out waiting for more data / the prompt.
				break
			if chunk == _PROMPT:
				break
			buffer += chunk
		return bytes(buffer)

	@staticmethod
	def _extract_response(command, raw):
		'''
		Given the echoed command and raw bytes returned by the board, strip the
		echo and surrounding whitespace and return the response string.
		'''
		text = raw.decode('ascii', errors='replace')
		# Normalise the board's CR/LF echoing into clean lines.
		lines = [line.strip() for line in text.replace('\r', '\n').split('\n')]
		lines = [line for line in lines if line]
		if not lines:
			return ''
		# The first non-empty line is the echoed command; drop it.
		if lines[0] == command.strip():
			lines = lines[1:]
		return ' '.join(lines).strip()

	'''
	*****************************************
	 Validation helpers
	*****************************************
	'''
	@staticmethod
	def _validate_index(index, count, label):
		if not isinstance(index, int) or index < 0 or index >= count:
			raise ValueError(f'{label} index must be an integer in 0..{count - 1}, got {index!r}')

	@staticmethod
	def _parse_on_off(response):
		'''
		Parse an 'on'/'off' style response into a boolean.  The board may
		include extra tokens, so search for the keyword.
		'''
		tokens = response.lower().split()
		if 'on' in tokens:
			return True
		if 'off' in tokens:
			return False
		raise NumatoResponseError(f'Expected "on" or "off", got {response!r}')

	'''
	*****************************************
	 Relay control
	*****************************************
	'''
	def relay_on(self, index):
		''' Turn on a single relay (0-3). '''
		self._validate_index(index, NUM_RELAYS, 'relay')
		self._send_command(f'relay on {index}')

	def relay_off(self, index):
		''' Turn off a single relay (0-3). '''
		self._validate_index(index, NUM_RELAYS, 'relay')
		self._send_command(f'relay off {index}')

	def relay_set(self, index, state):
		''' Set a single relay (0-3) to the given boolean state. '''
		if state:
			self.relay_on(index)
		else:
			self.relay_off(index)

	def relay_read(self, index):
		''' Read a single relay (0-3); returns True if on, False if off. '''
		self._validate_index(index, NUM_RELAYS, 'relay')
		response = self._send_command(f'relay read {index}')
		return self._parse_on_off(response)

	def relay_read_all(self):
		'''
		Read the state of all relays.

		Returns a list of booleans indexed by relay number, where index 0 is
		the least significant bit of the board's hex status value.
		'''
		response = self._send_command('relay readall')
		value = self._parse_hex(response, NUM_RELAYS, 'relay readall')
		return [bool(value & (1 << i)) for i in range(NUM_RELAYS)]

	def relay_write_all(self, mask):
		'''
		Set the state of all relays at once.

		:param mask: Either an integer bitmask (bit 0 -> relay 0) or an iterable
		             of booleans indexed by relay number.
		'''
		if not isinstance(mask, int):
			value = 0
			for i, state in enumerate(mask):
				if i >= NUM_RELAYS:
					raise ValueError(f'relay_write_all accepts at most {NUM_RELAYS} states')
				if state:
					value |= (1 << i)
		else:
			value = mask
		if value < 0 or value > (1 << NUM_RELAYS) - 1:
			raise ValueError(f'relay mask must be in 0..{(1 << NUM_RELAYS) - 1}, got {value}')
		# The board expects a hex value; a single hex digit covers 4 relays.
		self._send_command(f'relay writeall {value:x}')

	def reset(self):
		''' Reset all relays to the OFF state. '''
		self._send_command('reset')

	'''
	*****************************************
	 GPIO control
	*****************************************
	'''
	def gpio_set(self, index):
		''' Drive a GPIO pin (0-3) HIGH.  Puts the pin in output mode. '''
		self._validate_index(index, NUM_GPIO, 'gpio')
		self._send_command(f'gpio set {index}')

	def gpio_clear(self, index):
		''' Drive a GPIO pin (0-3) LOW.  Puts the pin in output mode. '''
		self._validate_index(index, NUM_GPIO, 'gpio')
		self._send_command(f'gpio clear {index}')

	def gpio_write(self, index, state):
		''' Drive a GPIO pin (0-3) to the given boolean state. '''
		if state:
			self.gpio_set(index)
		else:
			self.gpio_clear(index)

	def gpio_read(self, index):
		'''
		Read a GPIO pin (0-3); returns True if HIGH, False if LOW.  Puts the
		pin in input mode.
		'''
		self._validate_index(index, NUM_GPIO, 'gpio')
		response = self._send_command(f'gpio read {index}')
		return self._parse_on_off(response)

	'''
	*****************************************
	 ADC
	*****************************************
	'''
	def adc_read(self, index):
		'''
		Read an ADC channel (0-3).  Returns the raw 10-bit value (0-1023).
		ADC channels are multiplexed with the GPIO pins (ADCx <-> IOx).
		'''
		self._validate_index(index, NUM_ADC, 'adc')
		response = self._send_command(f'adc read {index}')
		try:
			value = int(response.split()[-1])
		except (ValueError, IndexError):
			raise NumatoResponseError(f'Expected an ADC value, got {response!r}')
		return value

	def adc_read_voltage(self, index, reference=5.0):
		'''
		Read an ADC channel (0-3) and convert it to volts.

		:param reference: Full scale reference voltage (default 5.0V).
		'''
		return self.adc_read(index) * reference / 1023.0

	'''
	*****************************************
	 System / informational
	*****************************************
	'''
	def version(self):
		''' Return the firmware version string reported by the board. '''
		return self._send_command('ver')

	def id_get(self):
		''' Return the module ID string. '''
		return self._send_command('id get')

	def id_set(self, module_id):
		''' Set the module ID (exactly 8 alphanumeric characters). '''
		module_id = str(module_id)
		if len(module_id) != 8 or not module_id.isalnum():
			raise ValueError('module id must be exactly 8 alphanumeric characters')
		self._send_command(f'id set {module_id}')

	'''
	*****************************************
	 Internal parsing helpers
	*****************************************
	'''
	@staticmethod
	def _parse_hex(response, num_bits, label):
		'''
		Parse a hex status value from a board response, tolerating extra tokens.
		'''
		for token in response.split():
			try:
				value = int(token, 16)
			except ValueError:
				continue
			if 0 <= value <= (1 << num_bits) - 1:
				return value
		raise NumatoResponseError(f'Could not parse hex value from {label} response {response!r}')
