"""
*****************************************
PiFire Qt Quick Display Interface Library
*****************************************

 Description: PySide6 / Qt Quick (QML) display for a DSI touch display.
 Drop-in replacement for the pygame flex display: reuses DisplayBase's Redis
 command-dispatch logic, but renders with Qt Quick in a spawned child process.
 Layout lives in QML; the paired JSON carries metadata only.

 The parent (control.py) process only spawns the Qt child and exposes the
 no-op display stubs. All rendering, data polling, and command dispatch happen
 inside the spawned child process (see display/qtapp.py). Qt is never imported
 or instantiated in the parent process.

*****************************************
"""
import logging
import multiprocessing

from display.base_flex import DisplayBase
from common import is_real_hardware, read_control, read_status, write_control


class Display(DisplayBase):
	def __init__(self, dev_pins, buttonslevel='HIGH', rotation=0, units='F', config={}):
		# Intentionally do NOT call super().__init__(): the parent process only
		# needs to spawn the Qt child and expose no-op stubs. Full DisplayBase
		# init (PIL canvas, assets, pygame menu JSON) is neither needed nor
		# desired here, and would import/instantiate Qt in the wrong process.
		self.dev_pins = dev_pins
		self.buttonslevel = buttonslevel
		self.units = units
		self.config = config
		self.rotation = config.get('rotation', 0)
		self._init_dispatch_state()
		self._start_qt_process()

	@classmethod
	def for_dispatch(cls, config, units):
		"""Build a dispatch-only instance (no Qt process) for use in the child.

		Provides just the attributes DisplayBase._command_handler needs so the
		delegated (complex) commands can be reused verbatim.
		"""
		self = cls.__new__(cls)
		self.config = config
		self.units = units
		self._init_dispatch_state()
		return self

	def _init_dispatch_state(self):
		self.command = None
		self.command_data = None
		self.display_object_list = []
		self.last_status_data = {}
		self.real_hardware = bool(is_real_hardware())
		self.eventLogger = logging.getLogger('control')
		self.display_active = None
		self.display_init = False
		self.display_timeout = None
		self.display_loop_active = True

	def _start_qt_process(self):
		ctx = multiprocessing.get_context('spawn')
		self._qt_process = ctx.Process(
			target=_run_qt_app, args=(self.config, self.units), daemon=True
		)
		self._qt_process.start()

	# ------------------------------------------------------------------
	# Command adapter
	#
	# The 6 commands below are special-cased because the inherited
	# _command_handler couples them to pygame-only state: `cmd_hold`/`cmd_notify`
	# read their value from the pygame display_object_list, `cmd_stop` calls
	# _init_framework() (which needs the pygame menu/input JSON we do not ship),
	# `cmd_splus` reads a stale last_status_data, and `cmd_primestartup`/
	# `cmd_primeonly` collide with the substring 'startup' in _command_handler.
	# Every other command is collision-safe and delegates to the inherited
	# handler verbatim, preserving its tested logic (settings writes, recipe
	# next-step handling, reboot/poweroff/restart, and the manual API toggles).
	# ------------------------------------------------------------------
	def _dispatch_command(self, command, command_data):
		if 'hold' in command:
			temp = int(command_data) if command_data else 0
			if temp:
				write_control(
					{'updated': True, 'mode': 'Hold', 'primary_setpoint': temp},
					origin='display',
				)
			return
		if 'notify' in command:
			origin = command_data.get('origin') if isinstance(command_data, dict) else None
			target = command_data.get('target', 0) if isinstance(command_data, dict) else 0
			control = read_control()
			for entry in control['notify_data']:
				if entry['name'] == origin:
					entry['target'] = target
					entry['req'] = bool(target)
					break
			write_control({'notify_data': control['notify_data']}, origin='display')
			return
		if command == 'cmd_stop':
			write_control({'updated': True, 'mode': 'Stop'}, origin='display')
			return
		if command == 'cmd_splus':
			status = read_status()
			toggle = not bool(status.get('s_plus', False)) if status else True
			write_control({'s_plus': toggle}, origin='display')
			return
		if command == 'cmd_primestartup':
			write_control(
				{'updated': True, 'mode': 'Prime', 'prime_amount': command_data, 'next_mode': 'Startup'},
				origin='display',
			)
			return
		if command == 'cmd_primeonly':
			write_control(
				{'updated': True, 'mode': 'Prime', 'prime_amount': command_data, 'next_mode': 'Stop'},
				origin='display',
			)
			return
		# Everything else: reuse the inherited handler verbatim.
		self.command = command
		self.command_data = command_data
		self._command_handler()

	# ------------------------------------------------------------------
	# control.py contract stubs (no-ops; the UI is self-contained)
	# ------------------------------------------------------------------
	def display_status(self, in_data, status_data):
		pass

	def display_text(self, text):
		pass

	def clear_display(self):
		pass


def _run_qt_app(config, units):
	"""Entry point executed inside the spawned child process."""
	from display.qtapp import run_app

	run_app(config, units)
