"""
*****************************************
PiFire Flexible Display Interface Library
*****************************************

 Description:
   This is a base class for displays, with
 a modular/flexible display size and layout.
 Other display libraries will inherit this
 base class and add device specific features.

*****************************************
"""

"""
 Imported Libraries
"""
import time
import logging
import socket
import os
import requests
from display.flexobject import *
from PIL import Image
from common import (
	read_control,
	write_control,
	WriteKind,
	is_real_hardware,
	read_generic_json,
	read_settings,
	write_settings,
	read_status,
	read_current,
)

"""
==================================================================================
Display base class definition
==================================================================================
"""


# FlexObject types added for the "ember" dashboard redesign (Tasks 16-23).
# Objects of these types receive the resolved accent palette at build time.
NEW_EMBER_FLEX_TYPES = {
	'probe_card',
	'gauge_ember',
	'system_card',
	'duty_pill',
	'hopper_vertical',
	'header_bar',
	'button_row',
}


class DisplayBase:
	def __init__(self, dev_pins, buttonslevel='HIGH', rotation=0, units='F', config={}):
		# Init Global Variables and Constants
		self.config = config

		# Ember dashboard accent palette (Task 24), used by the new flex object types.
		self.accent = resolve_accent(self.config.get('accent_theme', 'Ember'))

		self.dev_pins = dev_pins
		self.units = units

		self.in_data = None
		self.last_in_data = {}
		self.status_data = None
		self.last_status_data = {}

		self.input_enabled = False
		self.input_origin = None
		self.input_button = True if 'button' in self.config.get('input_types_supported', []) else False
		self.input_encoder = True if 'encoder' in self.config.get('input_types_supported', []) else False
		self.input_touch = True if 'touch' in self.config.get('input_types_supported', []) else False

		self.display_active = None
		self.display_timeout = None
		self.TIMEOUT = 10
		self.command = 'splash'
		self.command_data = None

		self.real_hardware = True if is_real_hardware() else False
		# Attempt to set the log level of PIL so that it does not pollute the logs
		logging.getLogger('PIL').setLevel(logging.CRITICAL + 1)

		# Setup logger
		self.eventLogger = logging.getLogger('control')
		# Init Display Device, Input Device, Assets
		self._init_globals()
		self._init_framework()
		self._init_display_canvas()
		self._init_input()
		self._init_display_device()

	def _init_globals(self):
		# Init constants and variables
		self.buttonslevel = self.config.get('buttonslevel', 'HIGH')
		if self.display_profile == None:
			self.display_profile = self.config.get('default_profile', 'profile_1')

		""" Get Local IP Address """
		try:
			s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
			s.settimeout(1)
			# doesn't even have to be reachable
			s.connect(('10.254.254.254', 1))
			self.ip_address = s.getsockname()[0]
		except Exception:
			self.ip_address = '127.0.0.1'
			self.eventLogger.error('Unable to get IP address of the system.')
		finally:
			s.close()

	def _init_framework(self):
		"""
		Initialize the dash/home/menu framework
		"""
		self.display_data = read_generic_json(self.config['display_data_filename'])
		self.ROTATION = self.config.get('rotation', 0)
		if self.ROTATION in [0, 180, 2]:
			self.WIDTH = self.display_data['metadata'].get('screen_width', 800)
			self.HEIGHT = self.display_data['metadata'].get('screen_height', 480)
		else:
			self.WIDTH = self.display_data['metadata'].get('screen_height', 480)
			self.HEIGHT = self.display_data['metadata'].get('screen_width', 800)
		self.SPLASH_DELAY = self.display_data['metadata'].get('splash_delay', 1000)
		self.FRAMERATE = self.display_data['metadata'].get('framerate', 30)
		if self.display_data[self.display_profile].get('home', []) == []:
			self.HOME_ENABLED = False
		else:
			self.HOME_ENABLED = True
		self.display_data[self.display_profile]['menus']['qrcode']['ip_address'] = self.ip_address

		self._fixup_display_data()

		self._init_assets()

	def _fixup_display_data(self):
		for index, object in enumerate(self.display_data[self.display_profile]['home']):
			for key in list(object.keys()):
				if key in [
					'position',
					'size',
					'fg_color',
					'bg_color',
					'color',
					'active_color',
					'inactive_color',
					'sp_color',
					'np_color',
				]:
					self.display_data[self.display_profile]['home'][index][key] = tuple(object[key])
		for index, object in enumerate(self.display_data[self.display_profile]['dash']):
			# print(f'Object Name: {object["name"]}')
			for key in list(object.keys()):
				if key in [
					'position',
					'size',
					'fg_color',
					'bg_color',
					'color',
					'active_color',
					'inactive_color',
					'sp_color',
					'np_color',
				]:
					# print(f'[{key}] = {object[key]}')
					self.display_data[self.display_profile]['dash'][index][key] = tuple(object[key])
					# print(f'converted = {tuple(object[key])}')
				if key in ['color_levels']:
					color_level_list = []
					for item in self.display_data[self.display_profile]['dash'][index][key]:
						color_level_list.append(tuple(item))
					self.display_data[self.display_profile]['dash'][index][key] = color_level_list
				if key in ['units']:
					""" Ensure we start with the right units displayed """
					self.display_data[self.display_profile]['dash'][index][key] = self.units
		for menu, object in self.display_data[self.display_profile]['menus'].items():
			for key in list(object.keys()):
				if key in [
					'position',
					'size',
					'fg_color',
					'bg_color',
					'color',
					'active_color',
					'inactive_color',
					'sp_color',
					'np_color',
				]:
					self.display_data[self.display_profile]['menus'][menu][key] = tuple(object[key])
		for input, object in self.display_data[self.display_profile]['input'].items():
			for key in list(object.keys()):
				if key in [
					'position',
					'size',
					'fg_color',
					'bg_color',
					'color',
					'active_color',
					'inactive_color',
					'sp_color',
					'np_color',
				]:
					self.display_data[self.display_profile]['input'][input][key] = tuple(object[key])
		# print(f'Fixed Up: \n{self.display_data[self.display_profile]["menus"]}')

	def _init_display_device(self):
		"""
		Inheriting classes will override this function to init the display device and start the display thread.
		"""
		pass

	def _init_display_canvas(self):
		"""
		Setup display canvas to be used by PIL to display objects
		"""
		self.display_canvas = Image.new('RGBA', (self.WIDTH, self.HEIGHT))

	def _init_input(self):
		"""
		Inheriting classes will override this function to setup the inputs.
		"""
		self.input_enabled = False  # If the inheriting class does not implement input, then clear this flag
		self.input_counter = 0

	def _display_loop(self):
		"""
		Main display loop
		"""
		while True:
			time.sleep(0.1)

	def _zero_dash_data(self):
		# self.last_in_data = {}
		# self.last_status_data = {}
		if self.status_data is not None or self.in_data is not None:
			self.status_data['mode'] = 'Stop'
			for outpin in self.status_data['outpins']:
				if outpin != 'pwm':
					self.status_data['outpins'][outpin] = False
			for probe in self.in_data['P']:
				self.in_data['P'][probe] = 0
			for probe in self.in_data['F']:
				self.in_data['F'][probe] = 0
			for probe in self.in_data['AUX']:
				self.in_data['AUX'][probe] = 0

			self.in_data['PSP'] = 0

			for probe in self.in_data['NT']:
				self.in_data['NT'][probe] = 0

	def _store_dash_objects(self):
		"""Store the dash object list so that it does not need to be rebuilt"""
		self.dash_object_list = self.display_object_list.copy()

	def _restore_dash_objects(self):
		"""Restore the dash object list to the main working display_object_list"""
		self.display_object_list = self.dash_object_list.copy()

	"""
    ============== Input Callbacks ============= 
    
    Inheriting classes will override these functions for all inputs.
    """

	def _enter_callback(self):
		"""
		Inheriting classes will override this function.
		"""
		pass

	def _up_callback(self, held=False):
		"""
		Inheriting classes will override this function to clear the display device.
		"""
		pass

	def _down_callback(self, held=False):
		"""
		Inheriting classes will override this function to clear the display device.
		"""
		pass

	"""
    ============== Graphics / Display / Draw Methods ============= 
    """

	def _init_assets(self):
		self._init_background()
		self._init_splash()

	def _init_background(self):
		background_image_path = self.display_data['metadata']['dash_background']
		self.background = Image.open(background_image_path)
		self.background = self.background.resize((self.WIDTH, self.HEIGHT))

	def _init_splash(self):
		splash_image_path = self.display_data['metadata']['splash_image']
		self.splash = Image.open(splash_image_path)
		width, height = self.splash.size
		if width > self.WIDTH or height > self.HEIGHT:
			# Scale the splash image to fit the display
			# Calculate the scaling factor based on aspect ratio
			ratio_w = self.WIDTH / float(width)
			ratio_h = self.HEIGHT / float(height)
			ratio = min(ratio_w, ratio_h)

			# Apply the scaling factor
			new_width = int(width * ratio)
			new_height = int(height * ratio)
			self.splash = self.splash.resize((new_width, new_height))

	def _wake_display(self):
		"""
		Inheriting classes will override this function to wake the display device.
		"""
		pass

	def _sleep_display(self):
		"""
		Inheriting classes will override this function to sleep the display device.
		"""
		pass

	def _display_clear(self):
		self.display_canvas.paste((0, 0, 0, 255), (0, 0, self.WIDTH, self.HEIGHT))

	def _display_canvas(self, canvas):
		"""
		Inheriting classes will override this function to show the canvas on the display device.
		"""
		pass

	def _display_splash(self):
		width, height = self.splash.size
		self.display_canvas.paste(self.splash, ((self.WIDTH // 2) - (width // 2), (self.HEIGHT // 2) - (height // 2)))
		self._display_canvas()

	def _display_background(self):
		"""
		Inheriting classes will override this function to display the stored background image.
		"""
		pass

	def _display_menu_background(self):
		"""
		Inheriting classes will override this function to display menu background
		"""
		pass

	def _build_objects(self, background=None):
		"""
		Inheriting classes may override this function to ensure the right object type is loaded
		"""
		self.display_object_list = []

		if self.display_active in ['home', 'dash']:
			section_data = self.display_data[self.display_profile][self.display_active]
		elif 'menu_' in self.display_active:
			section_data = [self.display_data[self.display_profile]['menus'][self.display_active.replace('menu_', '')]]
		elif 'input_' in self.display_active:
			section_data = [self.display_data[self.display_profile]['input'][self.display_active.replace('input_', '')]]
			section_data[0]['data']['origin'] = self.input_origin
		else:
			return

		for object_data in section_data:
			""" Add the object to the display object list """

			if self.status_data.get('hopper_level_enabled', False) == False and object_data['type'] == 'hopper_status':
				# Hopper Status is not enabled
				continue
			if self.status_data.get('hopper_level', None) != None and object_data['type'] == 'hopper_status':
				object_data['data']['level'] = self.status_data[
					'hopper_level'
				]  # Add the current hopper level to the object

			if (
				self.status_data.get('hopper_level_enabled', False) == False
				and object_data['type'] == 'hopper_vertical'
			):
				# D1: Hopper level reporting is not enabled - skip the ember hopper card entirely
				continue
			if self.status_data.get('hopper_level', None) != None and object_data['type'] == 'hopper_vertical':
				object_data.setdefault('data', {})
				object_data['data']['level'] = self.status_data['hopper_level']
				object_data['data']['enabled'] = self.status_data.get('hopper_level_enabled', False)

			if object_data['type'] in NEW_EMBER_FLEX_TYPES:
				# Inject the resolved accent palette (Task 24) for the new ember dash objects
				object_data['accent'] = self.accent

			FlexObject_ClassName = FlexObject_TypeMap[object_data['type']]
			FlexObject_Constructor = globals()[FlexObject_ClassName]
			self.display_object_list.append(FlexObject_Constructor(object_data['type'], object_data, background))

	def _configure_dash(self):
		"""Build Food Probe Map"""
		num_food_probes = min(len(self.config['probe_info']['food']), self.display_data['metadata']['max_food_probes'])
		self.food_probe_label_map = {}
		self.food_probe_name_map = {}
		# Ember dashboard probe_card_N slots (Task 24) mirror the food_probe_gauge_N mapping above.
		self.probe_card_label_map = {}
		self.probe_card_name_map = {}
		for index in range(num_food_probes):
			self.food_probe_label_map[f'food_probe_gauge_{index}'] = self.config['probe_info']['food'][index]['label']
			self.food_probe_name_map[f'food_probe_gauge_{index}'] = self.config['probe_info']['food'][index]['name']
			self.probe_card_label_map[f'probe_card_{index}'] = self.config['probe_info']['food'][index]['label']
			self.probe_card_name_map[f'probe_card_{index}'] = self.config['probe_info']['food'][index]['name']

		""" Remove Unused Food Probes & Rename Used Food Probes"""
		display_data_dash_list = []
		for object in self.display_data[self.display_profile]['dash']:
			if 'food_probe_gauge_' in object['name'] and object['name'] not in list(self.food_probe_label_map.keys()):
				pass
			elif 'probe_card_' in object['name'] and object['name'] not in list(self.probe_card_label_map.keys()):
				# D1/no-probes handling: hide any probe_card slot with no configured food probe
				pass
			else:
				if 'food_probe_gauge_' in object['name']:
					""" Rename Displayed Food Probes """
					object['label'] = self.food_probe_name_map[object['name']]
					object['units'] = self.units
					object['button_value'] = [object['label']]
				elif 'probe_card_' in object['name']:
					""" Rename Displayed Probe Cards """
					object.setdefault('data', {})
					object['data']['name'] = self.probe_card_name_map[object['name']]
					object['units'] = self.units
				elif object['name'] == 'primary_gauge':
					object['label'] = self.config['probe_info']['primary']['name']
					object['button_value'] = [object['label']]

				display_data_dash_list.append(object)

		self.display_data[self.display_profile]['dash'] = display_data_dash_list

	"""
    ============== Ember Dashboard Helpers (Task 24) =============

    Pure(ish) helpers that compute the live-data payload for the new ember
    flex objects. Kept as staticmethods so they can be unit-tested directly
    without constructing a full DisplayBase (which requires pygame/config).
    """

	@staticmethod
	def _button_row_for_mode(mode, recipe, recipe_paused):
		"""Returns (button_type, button_list, button_active) for the button_row
		object, mirroring the existing control_panel branch and Qt's
		Menus.controlPanelForMode (display/qml/Menus.js)."""
		if recipe and mode != 'Shutdown':
			type_item = 'Error'
			if mode in ('Startup', 'Reignite'):
				type_item = 'Startup'
			elif mode == 'Smoke':
				type_item = 'Smoke'
			elif mode == 'Hold':
				type_item = 'Hold'
			button_type = ['Next', type_item, 'Stop', 'Shutdown']
			button_list = ['cmd_next_step', 'cmd_none', 'cmd_stop', 'cmd_shutdown']
			button_active = 'Next' if recipe_paused else mode
			return button_type, button_list, button_active

		if mode in ('Startup', 'Reignite'):
			return ['Startup', 'Smoke', 'Hold', 'Stop'], ['cmd_startup', 'cmd_smoke', 'input_hold', 'cmd_stop'], mode
		if mode == 'Smoke':
			return (
				['Set Temp', 'Hold', 'Stop', 'Shutdown'],
				['input_hold', 'input_hold', 'cmd_stop', 'cmd_shutdown'],
				mode,
			)
		if mode == 'Hold':
			return (
				['Set Temp', 'Smoke', 'Stop', 'Shutdown'],
				['input_hold', 'cmd_smoke', 'cmd_stop', 'cmd_shutdown'],
				mode,
			)
		if mode == 'Shutdown':
			return (
				['Smoke', 'Hold', 'Stop', 'Shutdown'],
				['cmd_smoke', 'input_hold', 'cmd_stop', 'cmd_shutdown'],
				mode,
			)
		# Stop / Prime / Monitor
		return ['Prime', 'Startup', 'Monitor', 'Stop'], ['menu_prime', 'menu_startup', 'cmd_monitor', 'cmd_stop'], mode

	@staticmethod
	def _duty_pills(status_data):
		"""Returns (left_data, right_data) dicts for duty_pill_left/duty_pill_right:
		AUGER/FAN DUTY while Hold is active, otherwise P-MODE/SMOKE+."""
		mode = status_data.get('mode', 'Stop')
		outpins = status_data.get('outpins', {})
		if mode == 'Hold':
			left = {
				'label': 'AUGER DUTY',
				'value': f'{round(status_data.get("cycle_ratio", 0) * 100)}%',
				'highlight': False,
			}
			right = {
				'label': 'FAN DUTY',
				'value': f'{round(status_data.get("fan_duty", 0))}%',
				'highlight': bool(outpins.get('fan', False)),
			}
		else:
			left = {'label': 'P-MODE', 'value': f'P-{status_data.get("p_mode", 0)}', 'highlight': False}
			s_plus = bool(status_data.get('s_plus', False))
			right = {'label': 'SMOKE+', 'value': 'ON' if s_plus else 'OFF', 'highlight': s_plus}
		return left, right

	@staticmethod
	def _timer_seconds_and_label(status_data, now):
		"""Mirrors the countdown computation used by the legacy 'timer' dash
		branch (Prime/Startup/Reignite/Shutdown countdown, or Hold lid-open
		pause countdown). Returns (seconds, label); seconds is 0 and label is
		'' when no countdown is active."""
		mode = status_data.get('mode', 'Stop')
		if mode in ('Prime', 'Startup', 'Reignite', 'Shutdown'):
			if mode in ('Startup', 'Reignite'):
				duration = status_data.get('start_duration', 0)
			elif mode == 'Prime':
				duration = status_data.get('prime_duration', 0)
			else:
				duration = status_data.get('shutdown_duration', 0)
			countdown = int(duration - (now - status_data.get('start_time', now)))
			return max(countdown, 0), 'Timer'
		elif mode == 'Hold' and status_data.get('lid_open_detected'):
			countdown = int(status_data.get('lid_open_endtime', now) - now)
			return max(countdown, 0), 'Lid Pause'
		return 0, ''

	@staticmethod
	def _cook_time_data(status_data, now):
		"""Returns {'label':..., 'value':...} for the cook_time object (D2):
		the active countdown (mm:ss) when a timer is running, else the
		elapsed cook time (H:MM:SS) computed from startup_timestamp, mirroring
		display/qtbackend.py's _update_timer_text/_update_cook_elapsed.

		NOTE: Task 25 (the bespoke 1280x720 layout, not yet written as of this
		task) has not yet defined the FlexObject type/contract for the
		'cook_time' object name. This shape (data.label/data.value) matches
		the duty_pill contract as the closest existing analog; if Task 25
		instead reuses the 'timer' type (TimerStatus), that widget reads
		top-level 'label' and data['seconds'] rather than data['value'], so
		_update_dash_objects also mirrors 'label' at the top level for
		compatibility. This should be revisited once Task 25 lands.
		"""
		seconds, label = DisplayBase._timer_seconds_and_label(status_data, now)
		if seconds > 0:
			value = f'{seconds // 60:02d}:{seconds % 60:02d}'
			return {'label': label, 'value': value}

		timestamp = status_data.get('startup_timestamp', 0) or 0
		mode = status_data.get('mode', 'Stop')
		if timestamp and mode not in ('Stop', 'Monitor'):
			elapsed = max(int(now - timestamp), 0)
			hours, minutes, secs = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
			value = (f'{hours}:' if hours else '') + f'{minutes:02d}:{secs:02d}'
		else:
			value = '00:00'
		return {'label': 'COOK TIME', 'value': value}

	def _build_dash_map(self):
		"""Setup dash object mapping"""
		self.dash_map = {}

		# print('Setting up Dash Map:')
		for index, object in enumerate(self.display_object_list):
			objectData = object.get_object_data()
			self.dash_map[objectData['name']] = index
			# print(f' - Index: {index}, Maps to: {objectData["name"]}')

	def _update_dash_objects(self):

		if self.in_data is not None and self.status_data is not None:
			""" Update Mode Bar and Control Panel """
			if (self.status_data['mode'] != self.last_status_data.get('mode', 'None')) or (
				self.status_data['recipe_paused'] != self.last_status_data.get('recipe_paused', 'None')
			):
				""" Disable Screen Timeout When not in Stop Mode """
				if self.status_data['mode'] not in ['Stop']:
					self.display_timeout = None
				else:
					self.display_timeout = time.time() + self.TIMEOUT

				""" Mode Bar Update """
				if 'mode_bar' in self.dash_map.keys():
					object_data = self.display_object_list[self.dash_map['mode_bar']].get_object_data()

					if self.status_data['recipe'] and self.status_data['mode'] != 'Shutdown':
						object_data['text'] = 'Recipe: ' + self.status_data['mode']
					else:
						object_data['text'] = self.status_data['mode']
					self.display_object_list[self.dash_map['mode_bar']].update_object_data(object_data)
				""" Control Panel Update """
				if 'control_panel' in self.dash_map.keys():
					object_data = self.display_object_list[self.dash_map['control_panel']].get_object_data()
					object_data['button_active'] = self.status_data['mode']
					if self.status_data['recipe']:
						""" Recipe Mode """
						list_item = 'cmd_none'
						type_item = 'Error'
						if self.status_data['mode'] in ['Startup', 'Reignite']:
							type_item = 'Startup'
						elif self.status_data['mode'] == 'Smoke':
							type_item = 'Smoke'
						elif self.status_data['mode'] == 'Hold':
							type_item = 'Hold'
						elif self.status_data['mode'] == 'Shutdown':
							type_item = 'None'
						object_data['button_list'] = ['cmd_next_step', list_item, 'cmd_stop', 'cmd_shutdown']
						object_data['button_type'] = ['Next', type_item, 'Stop', 'Shutdown']
						if self.status_data['recipe_paused']:
							object_data['button_active'] = 'Next'
					elif self.status_data['mode'] in ['Startup', 'Reignite']:
						""" Startup Mode """
						object_data['button_list'] = ['cmd_startup', 'cmd_smoke', 'input_hold', 'cmd_stop']
						object_data['button_type'] = ['Startup', 'Smoke', 'Hold', 'Stop']
					elif self.status_data['mode'] in ['Smoke', 'Hold', 'Shutdown']:
						""" Smoke, Hold or Shutdown Modes """
						object_data['button_list'] = ['cmd_smoke', 'input_hold', 'cmd_stop', 'cmd_shutdown']
						object_data['button_type'] = ['Smoke', 'Hold', 'Stop', 'Shutdown']
					else:
						""" Stopped, Prime, Monitor Modes """
						object_data['button_list'] = ['menu_prime', 'menu_startup', 'cmd_monitor', 'cmd_stop']
						object_data['button_type'] = ['Prime', 'Startup', 'Monitor', 'Stop']

					self.display_object_list[self.dash_map['control_panel']].update_object_data(object_data)
				""" Button Row Update (ember dash) """
				if 'button_row' in self.dash_map.keys():
					object_data = self.display_object_list[self.dash_map['button_row']].get_object_data()
					button_type, button_list, button_active = self._button_row_for_mode(
						self.status_data['mode'], self.status_data['recipe'], self.status_data['recipe_paused']
					)
					object_data['button_type'] = button_type
					object_data['button_list'] = button_list
					object_data['button_active'] = button_active
					self.display_object_list[self.dash_map['button_row']].update_object_data(object_data)
				""" Primary Gauge Mode Label Update (ember dash - gauge_ember) """
				if 'primary_gauge' in self.dash_map.keys():
					object_data = self.display_object_list[self.dash_map['primary_gauge']].get_object_data()
					object_data.setdefault('data', {})
					object_data['data']['mode_label'] = self.status_data['mode'].upper()
					self.display_object_list[self.dash_map['primary_gauge']].update_object_data(object_data)
				""" Lid Open Button Update """
				if 'lid_open_button' in self.dash_map.keys() and self.status_data['mode'] == 'Hold':
					object_data = self.display_object_list[self.dash_map['lid_open_button']].get_object_data()
					if self.status_data.get('lid_open_detected', False):
						object_data['active'] = True
					else:
						object_data['active'] = False
					self.display_object_list[self.dash_map['lid_open_button']].update_object_data(object_data)

			""" Header Bar Update (ember dash) - clock/IP/live-cooking-dot, independent of mode change """
			if 'header_bar' in self.dash_map.keys():
				object_data = self.display_object_list[self.dash_map['header_bar']].get_object_data()
				object_data.setdefault('data', {})
				new_clock = time.strftime('%H:%M')
				new_cooking = self.status_data['mode'] in ('Startup', 'Reignite', 'Smoke', 'Hold', 'Recipe')
				if (
					object_data['data'].get('clock') != new_clock
					or object_data['data'].get('ip') != self.ip_address
					or object_data['data'].get('cooking') != new_cooking
				):
					object_data['data']['ip'] = self.ip_address
					object_data['data']['clock'] = new_clock
					object_data['data']['cooking'] = new_cooking
					self.display_object_list[self.dash_map['header_bar']].update_object_data(object_data)

			if self.last_in_data != {}:
				""" Update Primary Gauge Values """
				primary_key = list(self.in_data['P'].keys())[0]  # Get the key for the primary gauge
				if (
					(self.in_data['P'] != self.last_in_data['P'])
					or (self.in_data['PSP'] != self.last_in_data['PSP'])
					or (self.in_data['NT'][primary_key] != self.last_in_data['NT'].get(primary_key))
				):
					""" Update the Primary Gauge """
					object_data = self.display_object_list[self.dash_map['primary_gauge']].get_object_data()
					object_data['temps'][0] = (
						self.in_data['P'][primary_key] if self.in_data['P'][primary_key] is not None else 0
					)
					object_data['temps'][1] = self.in_data['NT'][primary_key]
					object_data['temps'][2] = self.in_data['PSP']
					object_data['units'] = self.units
					# object_data['label'] = primary_key
					self.display_object_list[self.dash_map['primary_gauge']].update_object_data(object_data)

				""" Update Food Probe Gauges and Values """
				food_gauge_keys = list(self.food_probe_label_map.keys())
				for gauge in food_gauge_keys:
					if gauge not in self.dash_map.keys():
						# Layouts built entirely around probe_card_N (ember dash) have no
						# food_probe_gauge_N objects at all - nothing to update here.
						continue
					key = self.food_probe_label_map[gauge]
					if (
						self.last_in_data['F'][key] != self.in_data['F'][key]
						or self.last_in_data['NT'][key] != self.in_data['NT'][key]
					):
						""" Update this food gauge """
						object_data = self.display_object_list[self.dash_map[gauge]].get_object_data()
						object_data['temps'][0] = self.in_data['F'][key] if self.in_data['F'][key] is not None else 0
						object_data['temps'][1] = self.in_data['NT'][key]
						object_data['temps'][2] = 0  # There is no set temp for food probes
						object_data['units'] = self.units
						self.display_object_list[self.dash_map[gauge]].update_object_data(object_data)

				""" Update Probe Cards (ember dash) """
				probe_card_keys = list(self.probe_card_label_map.keys())
				for card in probe_card_keys:
					if card not in self.dash_map.keys():
						continue
					key = self.probe_card_label_map[card]
					if (
						self.last_in_data['F'][key] != self.in_data['F'][key]
						or self.last_in_data['NT'][key] != self.in_data['NT'][key]
					):
						""" Update this probe card """
						object_data = self.display_object_list[self.dash_map[card]].get_object_data()
						object_data.setdefault('data', {})
						object_data['data']['name'] = self.probe_card_name_map[card]
						object_data['data']['temp'] = (
							self.in_data['F'][key] if self.in_data['F'][key] is not None else 0
						)
						object_data['data']['target'] = (
							self.in_data['NT'][key] if self.in_data['NT'][key] is not None else 0
						)
						object_data['units'] = self.units
						self.display_object_list[self.dash_map[card]].update_object_data(object_data)

			""" Update Output Status Icons """
			if self.last_status_data.get('outpins') is None:
				self.last_status_data['outpins'] = self.status_data['outpins'].copy()
				for output in self.last_status_data['outpins']:
					self.last_status_data['outpins'][output] = (
						True if self.status_data['outpins'][output] == False else False
					)
			for output in self.status_data['outpins']:
				if self.status_data['outpins'][output] != self.last_status_data['outpins'].get(output):
					if output == 'auger' and 'auger_status' in self.dash_map.keys():
						object_data = self.display_object_list[self.dash_map['auger_status']].get_object_data()
						object_data['animation_enabled'] = True if self.status_data['outpins'][output] else False
						object_data['active'] = True if self.status_data['outpins'][output] else False
						self.display_object_list[self.dash_map['auger_status']].update_object_data(object_data)
					if output == 'fan' and 'fan_status' in self.dash_map.keys():
						object_data = self.display_object_list[self.dash_map['fan_status']].get_object_data()
						object_data['animation_enabled'] = True if self.status_data['outpins'][output] else False
						object_data['active'] = True if self.status_data['outpins'][output] else False
						self.display_object_list[self.dash_map['fan_status']].update_object_data(object_data)
					if output == 'igniter' and 'igniter_status' in self.dash_map.keys():
						object_data = self.display_object_list[self.dash_map['igniter_status']].get_object_data()
						object_data['animation_enabled'] = True if self.status_data['outpins'][output] else False
						object_data['active'] = True if self.status_data['outpins'][output] else False
						self.display_object_list[self.dash_map['igniter_status']].update_object_data(object_data)

			""" Update System Card (ember dash - fan/auger/igniter combined) """
			if 'system_card' in self.dash_map.keys() and self.status_data['outpins'] != self.last_status_data.get(
				'outpins'
			):
				object_data = self.display_object_list[self.dash_map['system_card']].get_object_data()
				object_data['data'] = {
					'fan': bool(self.status_data['outpins'].get('fan', False)),
					'auger': bool(self.status_data['outpins'].get('auger', False)),
					'igniter': bool(self.status_data['outpins'].get('igniter', False)),
				}
				self.display_object_list[self.dash_map['system_card']].update_object_data(object_data)

			""" Update Timer Output """
			if self.status_data['mode'] in ['Prime', 'Startup', 'Reignite', 'Shutdown']:
				if self.status_data['mode'] in ['Startup', 'Reignite']:
					duration = self.status_data['start_duration']
				elif self.status_data['mode'] in ['Prime']:
					duration = self.status_data['prime_duration']
				else:
					duration = self.status_data['shutdown_duration']

				countdown = (
					int(duration - (time.time() - self.status_data['start_time']))
					if int(duration - (time.time() - self.status_data['start_time'])) > 0
					else 0
				)
				if 'timer' in self.dash_map.keys():
					object_data = self.display_object_list[self.dash_map['timer']].get_object_data()

					if countdown != object_data['data']['seconds']:
						object_data['data']['seconds'] = countdown
						object_data['label'] = 'Timer'
						self.display_object_list[self.dash_map['timer']].update_object_data(object_data)

			elif self.status_data['mode'] in ['Hold'] and self.status_data['lid_open_detected']:
				""" In Hold Mode, use timer for lid open detection """
				countdown = (
					int(self.status_data['lid_open_endtime'] - time.time())
					if int(self.status_data['lid_open_endtime'] - time.time()) > 0
					else 0
				)
				if 'timer' in self.dash_map.keys():
					object_data = self.display_object_list[self.dash_map['timer']].get_object_data()
					if countdown != object_data['data']['seconds']:
						object_data['data']['seconds'] = countdown
						object_data['label'] = 'Lid Pause'
						self.display_object_list[self.dash_map['timer']].update_object_data(object_data)

			else:
				""" Clear the timer in other modes. """
				if 'timer' in self.dash_map.keys():
					object_data = self.display_object_list[self.dash_map['timer']].get_object_data()
					if object_data['data']['seconds'] != 0:
						object_data['data']['seconds'] = 0
						self.display_object_list[self.dash_map['timer']].update_object_data(object_data)

			""" Update Cook Time (ember dash, D2) - active countdown, else elapsed cook time """
			if 'cook_time' in self.dash_map.keys():
				object_data = self.display_object_list[self.dash_map['cook_time']].get_object_data()
				new_data = self._cook_time_data(self.status_data, time.time())
				if object_data.get('data') != new_data:
					object_data.setdefault('data', {})
					object_data['data']['label'] = new_data['label']
					object_data['data']['value'] = new_data['value']
					object_data['label'] = new_data['label']  # top-level fallback if type is 'timer'-like
					self.display_object_list[self.dash_map['cook_time']].update_object_data(object_data)

			""" In Hold Mode, Check Lid Indicator """
			if (
				self.status_data['mode'] in ['Hold']
				and self.last_status_data['lid_open_detected'] != self.status_data['lid_open_detected']
				and 'lid_indicator' in self.dash_map.keys()
			):
				object_data = self.display_object_list[self.dash_map['lid_indicator']].get_object_data()
				if self.status_data['lid_open_detected']:
					object_data['active'] = True
				else:
					object_data['active'] = False
				self.display_object_list[self.dash_map['lid_indicator']].update_object_data(object_data)

			""" Lid Alert Update (ember dash) - active only while lid_open_detected, mirrors lid_indicator """
			if 'lid_alert' in self.dash_map.keys():
				object_data = self.display_object_list[self.dash_map['lid_alert']].get_object_data()
				new_active = bool(self.status_data.get('lid_open_detected', False))
				if object_data.get('active') != new_active:
					object_data['active'] = new_active
					self.display_object_list[self.dash_map['lid_alert']].update_object_data(object_data)

			""" In Hold Mode, Show Lid Indicator Button (when lid_open_detected is False)"""
			if (
				self.status_data['mode'] in ['Hold']
				and self.last_status_data['lid_open_detected'] != self.status_data['lid_open_detected']
				and 'lid_open_button' in self.dash_map.keys()
			):
				object_data = self.display_object_list[self.dash_map['lid_open_button']].get_object_data()
				if self.status_data['lid_open_detected']:
					object_data['active'] = True
				else:
					object_data['active'] = False
				self.display_object_list[self.dash_map['lid_open_button']].update_object_data(object_data)

			""" Update PMode """
			if (
				self.status_data['mode'] in ['Startup', 'Reignite', 'Smoke']
				and (
					(self.status_data['mode'] != self.last_status_data.get('mode', 'None'))
					or (self.status_data['p_mode'] != self.last_status_data.get('p_mode', 'None'))
				)
				and 'p_mode' in self.dash_map.keys()
			):
				object_data = self.display_object_list[self.dash_map['p_mode']].get_object_data()
				object_data['active'] = True
				object_data['data']['pmode'] = self.status_data['p_mode']
				self.display_object_list[self.dash_map['p_mode']].update_object_data(object_data)

			elif (
				self.status_data['mode'] != self.last_status_data.get('mode', 'None')
				and 'p_mode' in self.dash_map.keys()
			):
				object_data = self.display_object_list[self.dash_map['p_mode']].get_object_data()
				object_data['active'] = False
				self.display_object_list[self.dash_map['p_mode']].update_object_data(object_data)

			""" Update Smoke Plus """
			if (
				self.status_data['s_plus'] != self.last_status_data.get('s_plus', None)
				and 'smoke_plus' in self.dash_map.keys()
			):
				object_data = self.display_object_list[self.dash_map['smoke_plus']].get_object_data()

				object_data['active'] = self.status_data['s_plus']
				object_data['button_value'][0] = 'off' if self.status_data['s_plus'] else 'on'

				self.display_object_list[self.dash_map['smoke_plus']].update_object_data(object_data)

			""" Update Hopper Info """
			if (
				self.status_data['hopper_level'] != self.last_status_data.get('hopper_level', None)
				and 'hopper' in self.dash_map.keys()
			):
				object_data = self.display_object_list[self.dash_map['hopper']].get_object_data()
				object_data['data']['level'] = max(self.status_data['hopper_level'], 0)
				object_data['data']['level'] = min(object_data['data']['level'], 100)

				self.display_object_list[self.dash_map['hopper']].update_object_data(object_data)

			""" Update Hopper Vertical (ember dash) - D1: object is absent from dash_map entirely
			when hopper_level_enabled is False (see _build_objects), so this naturally no-ops then. """
			if (
				self.status_data['hopper_level'] != self.last_status_data.get('hopper_level', None)
				and 'hopper_vertical' in self.dash_map.keys()
			):
				object_data = self.display_object_list[self.dash_map['hopper_vertical']].get_object_data()
				object_data.setdefault('data', {})
				object_data['data']['level'] = max(min(self.status_data['hopper_level'], 100), 0)
				object_data['data']['enabled'] = self.status_data.get('hopper_level_enabled', False)
				self.display_object_list[self.dash_map['hopper_vertical']].update_object_data(object_data)

			""" Update Duty Pills (ember dash) - AUGER/FAN DUTY in Hold, else P-MODE/SMOKE+ """
			if 'duty_pill_left' in self.dash_map.keys() or 'duty_pill_right' in self.dash_map.keys():
				left_data, right_data = self._duty_pills(self.status_data)
				if 'duty_pill_left' in self.dash_map.keys():
					object_data = self.display_object_list[self.dash_map['duty_pill_left']].get_object_data()
					if object_data.get('data') != left_data:
						object_data['data'] = left_data
						self.display_object_list[self.dash_map['duty_pill_left']].update_object_data(object_data)
				if 'duty_pill_right' in self.dash_map.keys():
					object_data = self.display_object_list[self.dash_map['duty_pill_right']].get_object_data()
					if object_data.get('data') != right_data:
						object_data['data'] = right_data
						self.display_object_list[self.dash_map['duty_pill_right']].update_object_data(object_data)

			""" After all the updates, update the last states/data """
			self.last_in_data = self.in_data.copy()
			self.last_status_data = self.status_data.copy()

	def _draw_objects(self):
		for object in self.display_object_list:
			objectData = object.get_object_data()
			objectState = object.get_object_state()

			if objectData['animation_enabled'] and objectState['animation_active']:
				object_image = object.update_object_data()
				self.display_updated = True
			else:
				object_image = object.get_object_canvas()

			if objectData['glow']:
				object_image_glow = object_image.copy()
				object_image_glow = object_image_glow.filter(ImageFilter.GaussianBlur(radius=3))
				self.display_canvas.paste(object_image, objectData['position'], object_image_glow)
			self.display_canvas.paste(object_image, objectData['position'], object_image)

	"""
        ====================== Input/Event Handling ========================
    """

	def _fetch_data(self):
		"""
		- Updates the current data for the display loop, if in a work mode
		"""
		if self.in_data is None:
			self.last_in_data = {}
		self.in_data = read_current()

		if self.status_data is None:
			self.last_status_data = {}
		self.status_data = read_status()

		self.units = self.status_data['units']

		""" Wake the display to the dash if it's currently off """
		if self.display_active == None and self.status_data['mode'] != 'Stop':
			self.display_active = 'dash'
			self.display_init = True
			self._wake_display()
			self.display_timeout = None

	def _event_detect(self):
		"""
		Called to detect input events from buttons, encoder, touch, etc.
		This function should be overridden by the inheriting class.
		"""
		pass

	def _command_handler(self):
		"""
		Called to handle commands
		"""
		# print(' > Command Handler Called < ')
		if 'monitor' in self.command:
			data = {'updated': True, 'mode': 'Monitor'}
			write_control(data, WriteKind.MERGE, origin='display')
			# print('Sent Monitor Mode Command!')
			self.display_active = 'dash'
			self.display_init = True

		if 'startup' in self.command:
			data = {'updated': True, 'mode': 'Startup'}
			write_control(data, WriteKind.MERGE, origin='display')
			# print('Sent Startup Mode Command!')
			self.display_active = 'dash'
			self.display_init = True

		if 'smoke' in self.command:
			data = {'updated': True, 'mode': 'Smoke'}
			write_control(data, WriteKind.MERGE, origin='display')
			self.display_active = 'dash'
			self.display_init = True

		if 'hold' in self.command:
			""" Set hold target for primary probe """
			primary_setpoint = 0
			for pointer, object in enumerate(self.display_object_list):
				objectData = object.get_object_data()
				if objectData['data'].get('value', False):
					primary_setpoint = objectData['data'].get('value', False)
					break

			if primary_setpoint:
				data = {'updated': True, 'mode': 'Hold', 'primary_setpoint': primary_setpoint}
				write_control(data, WriteKind.MERGE, origin='display')
			self.display_active = 'dash'
			self.display_init = True

		if 'notify' in self.command:
			""" Set notification targets for probes/grill """
			notify_target = 0
			for pointer, object in enumerate(self.display_object_list):
				objectData = object.get_object_data()
				if objectData['data'].get('value', False):
					notify_target = objectData['data'].get('value', False)
					break

			control = read_control()
			for index, notify_source in enumerate(control['notify_data']):
				if notify_source['name'] == self.input_origin:
					control['notify_data'][index]['target'] = notify_target
					control['notify_data'][index]['req'] = True if notify_target else False
					break

			data = {'notify_data': control['notify_data']}
			write_control(data, WriteKind.MERGE, origin='display')

			self.input_origin = None
			self.display_active = 'dash'
			self.display_init = True

		if 'shutdown' in self.command:
			data = {'updated': True, 'mode': 'Shutdown'}
			write_control(data, WriteKind.MERGE, origin='display')
			self.display_active = 'dash'
			self.display_init = True

		if 'stop' in self.command:
			data = {'updated': True, 'mode': 'Stop'}
			write_control(data, WriteKind.MERGE, origin='display')

			self._init_framework()
			self._zero_dash_data()
			self.display_active = 'dash'
			self.display_init = True
			self.display_timeout = time.time() + self.TIMEOUT

		if 'splus' in self.command:
			toggle = False if self.last_status_data.get('s_plus', False) else True
			data = {'s_plus': toggle}
			write_control(data, WriteKind.MERGE, origin='display')
			self.display_active = 'dash'
			self.display_init = True

		if 'primestartup' in self.command:
			data = {'updated': True, 'mode': 'Prime', 'prime_amount': self.command_data, 'next_mode': 'Startup'}
			write_control(data, WriteKind.MERGE, origin='display')
			self.display_active = 'dash'
			self.display_init = True

		if 'primeonly' in self.command:
			data = {'updated': True, 'mode': 'Prime', 'prime_amount': self.command_data, 'next_mode': 'Stop'}
			write_control(data, WriteKind.MERGE, origin='display')
			self.display_active = 'dash'
			self.display_init = True

		if 'pmode' in self.command:
			# TODO : Change to API Call
			settings = read_settings()
			settings['cycle_data']['PMode'] = self.command_data
			write_settings(settings)
			data = {'settings_update': True}
			write_control(data, WriteKind.MERGE, origin='display')

			self.display_active = 'dash'
			self.display_init = True

		if 'next_step' in self.command:
			data = read_control()
			# Check if currently in 'Paused' Status
			if 'triggered' in data['recipe']['step_data'] and 'pause' in data['recipe']['step_data']:
				if data['recipe']['step_data']['triggered'] and data['recipe']['step_data']['pause']:
					# 'Unpause' Recipe
					data['recipe']['step_data']['pause'] = False
					write_control(data, WriteKind.MERGE, origin='display')
				else:
					# User is forcing next step
					data['updated'] = True
					write_control(data, WriteKind.MERGE, origin='display')
			else:
				# User is forcing next step
				data['updated'] = True
				write_control(data, WriteKind.MERGE, origin='display')
			self.display_active = 'dash'
			self.display_init = True

		if 'reboot' in self.command:
			data = {'updated': True, 'mode': 'Stop'}
			write_control(data, WriteKind.MERGE, origin='display')
			if self.real_hardware:
				os.system('sleep 3 && sudo reboot &')
			else:
				pass
			self.display_active = 'dash'
			self.display_init = True
			self.display_loop_active = False

		if 'poweroff' in self.command:
			data = {'updated': True, 'mode': 'Stop'}
			write_control(data, WriteKind.MERGE, origin='display')
			if self.real_hardware:
				os.system('sleep 3 && sudo shutdown -h now &')
			else:
				pass
			self.display_active = 'dash'
			self.display_init = True
			self.display_loop_active = False

		if 'restart' in self.command:
			data = {'updated': True, 'mode': 'Stop'}
			write_control(data, WriteKind.MERGE, origin='display')
			if self.real_hardware:
				os.system('sleep 3 && sudo service supervisor restart &')
			else:
				pass
			self.display_active = 'dash'
			self.display_init = True
			self.display_loop_active = False

		if 'hopper' in self.command:
			data = {'hopper_check': True}
			write_control(data, WriteKind.MERGE, origin='display')
			self.display_active = 'dash'
			self.display_init = True

		if 'igniter_toggle' in self.command:
			try:
				requests.get('http://127.0.0.1:5000/api/set/manual/igniter/toggle')
			except:
				self.eventLogger.debug('Igniter Toggle Failed.')

		if 'auger_toggle' in self.command:
			try:
				requests.get('http://127.0.0.1:5000/api/set/manual/auger/toggle')
			except:
				self.eventLogger.debug('Auger Toggle Failed.')

		if 'fan_toggle' in self.command:
			try:
				requests.get('http://127.0.0.1:5000/api/set/manual/fan/toggle')
			except:
				self.eventLogger.debug('Fan Toggle Failed.')

		if 'lid_open' in self.command:
			try:
				requests.get('http://127.0.0.1:5000/api/set/lid_open/toggle')
			except:
				self.eventLogger.debug('Lid Open Failed.')
			# Go back to Dash
			self.display_active = 'dash'
			self.display_init = True

		if 'none' in self.command:
			pass

		self.command = None

	"""
    ================ Externally Available Methods ================
    """

	def display_status(self, in_data, status_data):
		"""
		Stub from legacy implementation
		"""
		pass

	def display_splash(self):
		"""
		- Calls Splash Screen
		This function is currently unused and is only provided to maintain compatibility.
		"""
		pass

	def clear_display(self):
		"""
		- Clear display and turn off backlight
		This function is currently unused and is only provided to maintain compatibility.
		"""
		# print('Clear Display Requested.')
		pass

	def display_text(self, text):
		"""
		- Display some text
		This function is currently unused and is only provided to maintain compatibility.
		"""
		# print(f'Display Text: {text}')
		pass

	def display_network(self):
		"""
		- Display Network IP QR Code
		This function is currently unused and is only provided to maintain compatibility.
		"""
		pass
