"""busio.SPI-compatible bus backed by an MCP2210."""


class SPI:
	def __init__(self, device):
		self._device = device
		self._locked = False
		self._baudrate = 100000
		self._mode = 0

	def try_lock(self):
		if self._locked:
			return False
		self._locked = True
		return True

	def unlock(self):
		self._locked = False

	def configure(self, *, baudrate=100000, polarity=0, phase=0, bits=8):
		if not self._locked:
			raise RuntimeError('function requires lock')
		if bits != 8:
			raise ValueError('MCP2210 supports 8 bits per word only')
		if polarity not in (0, 1) or phase not in (0, 1):
			raise ValueError('polarity and phase must be 0 or 1')
		self._baudrate = int(baudrate)
		self._mode = (polarity << 1) | phase

	@property
	def frequency(self):
		return self._baudrate

	def _exchange(self, data):
		return self._device.spi_exchange(data, bitrate=self._baudrate, mode=self._mode)

	def write(self, buffer, *, start=0, end=None):
		end = len(buffer) if end is None else end
		self._exchange(bytes(buffer[start:end]))

	def readinto(self, buffer, *, start=0, end=None, write_value=0):
		end = len(buffer) if end is None else end
		count = end - start
		rx = self._exchange(bytes([write_value]) * count)
		buffer[start:end] = rx

	def write_readinto(self, out_buffer, in_buffer, *, out_start=0, out_end=None, in_start=0, in_end=None):
		out_end = len(out_buffer) if out_end is None else out_end
		in_end = len(in_buffer) if in_end is None else in_end
		if (out_end - out_start) != (in_end - in_start):
			raise ValueError('out and in buffers must have the same length')
		rx = self._exchange(bytes(out_buffer[out_start:out_end]))
		in_buffer[in_start:in_end] = rx

	def deinit(self):
		pass
