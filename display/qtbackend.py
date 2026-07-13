"""
*****************************************
PiFire Qt Quick Display — Backend Bridge
*****************************************

 Description: QObject bridge between PiFire's Redis-backed data/command layer
 and the Qt Quick (QML) UI. Polls live data via an injected fetch function and
 exposes it as Qt properties; forwards UI actions via an injected command
 function. Framework-agnostic and unit-testable without a running QML engine.

*****************************************
"""

import time

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Qt, Signal, Slot


class FoodProbeModel(QAbstractListModel):
	NameRole = Qt.UserRole + 1
	TempRole = Qt.UserRole + 2
	TargetRole = Qt.UserRole + 3
	MaxRole = Qt.UserRole + 4

	def __init__(self, food_info, parent=None):
		super().__init__(parent)
		# Live data (F/NT) is keyed by probe *label*; the display name and the
		# notify origin use probe *name* (matching the pygame flex display).
		self._rows = [
			{
				'name': f.get('name', f'Probe {i + 1}'),
				'label': f.get('label', f.get('name', f'Probe {i + 1}')),
				'temp': 0,
				'target': 0,
				'maxTemp': f.get('max_temp', 300),
			}
			for i, f in enumerate(food_info)
		]

	def rowCount(self, parent=QModelIndex()):
		return 0 if parent.isValid() else len(self._rows)

	def roleNames(self):
		return {self.NameRole: b'name', self.TempRole: b'temp', self.TargetRole: b'target', self.MaxRole: b'maxTemp'}

	def data(self, index, role):
		if not index.isValid():
			return None
		row = self._rows[index.row()]
		return {
			self.NameRole: row['name'],
			self.TempRole: row['temp'],
			self.TargetRole: row['target'],
			self.MaxRole: row['maxTemp'],
		}.get(role)

	def update(self, in_data):
		f = in_data.get('F', {})
		nt = in_data.get('NT', {})
		changed = False
		for row in self._rows:
			temp = f.get(row['label'], 0)
			target = nt.get(row['label'], 0)
			if row['temp'] != temp or row['target'] != target:
				row['temp'], row['target'] = temp, target
				changed = True
		if changed and self._rows:
			self.dataChanged.emit(
				self.index(0, 0), self.index(len(self._rows) - 1, 0), [self.TempRole, self.TargetRole]
			)


class PiFireBackend(QObject):
	modeChanged = Signal()
	modeTextChanged = Signal()
	unitsChanged = Signal()
	primaryChanged = Signal()
	hopperChanged = Signal()
	statusChanged = Signal()
	timerChanged = Signal()
	asleepChanged = Signal()
	navEvent = Signal(str)
	accentThemeChanged = Signal()

	def __init__(self, fetch_fn, command_fn, probe_info, accent_fn=None, timeout_fn=None, parent=None):
		super().__init__(parent)
		self._fetch_fn = fetch_fn
		self._command_fn = command_fn
		self._probe_info = probe_info or {}
		self._now = time.time
		self._accent_fn = accent_fn
		self._timeout_fn = timeout_fn
		self._accent_theme = 'Ember'
		self._last_settings_check = 0.0
		primary = self._probe_info.get('primary', {})
		self._primary_name = primary.get('name', 'Primary')
		self._primary_label = primary.get('label', self._primary_name)
		self._primary_max = primary.get('max_temp', 600)
		self._primary_notify = 0
		self._ip_address = self._probe_info.get('ip_address', '') or ''
		self._food_model = FoodProbeModel(self._probe_info.get('food', []))
		self._mode = 'Stop'
		self._units = 'F'
		self._primary_temp = 0
		self._primary_sp = 0
		self._hopper_level = 0
		self._hopper_enabled = False
		self._p_mode = 0
		self._s_plus = False
		self._fan = False
		self._auger = False
		self._igniter = False
		self._lid_open = False
		self._recipe = False
		self._recipe_paused = False
		self._timer_text = ''
		self._timer_label = ''
		self._mode_text = 'Stop'
		self._p_mode_active = False
		self._auger_duty = 0
		self._fan_duty = 0
		self._food_count = len(self._probe_info.get('food', []))
		self._cook_elapsed_text = '00:00'
		# Idle / sleep state
		self.TIMEOUT = self._timeout_fn() if self._timeout_fn is not None else 300
		self._last_interaction = self._now()
		self._asleep = False

	def _set(self, attr, value, signal):
		if getattr(self, attr) != value:
			setattr(self, attr, value)
			signal.emit()

	@Slot()
	def poll(self):
		in_data, status = self._fetch_fn()
		if status is None or in_data is None:
			return
		self._set('_mode', status.get('mode', 'Stop'), self.modeChanged)
		self._set('_units', status.get('units', 'F'), self.unitsChanged)
		p = in_data.get('P', {})
		primary_key = next(iter(p), None)
		primary_temp = p.get(primary_key, 0) if primary_key is not None else 0
		self._set('_primary_temp', primary_temp, self.primaryChanged)
		self._set('_primary_sp', in_data.get('PSP', 0) or 0, self.primaryChanged)
		nt = in_data.get('NT', {})
		self._set('_primary_notify', nt.get(primary_key, 0) or 0, self.primaryChanged)
		outpins = status.get('outpins', {})
		self._set('_fan', bool(outpins.get('fan', False)), self.statusChanged)
		self._set('_auger', bool(outpins.get('auger', False)), self.statusChanged)
		self._set('_igniter', bool(outpins.get('igniter', False)), self.statusChanged)
		self._set('_p_mode', status.get('p_mode', 0), self.statusChanged)
		self._set('_s_plus', bool(status.get('s_plus', False)), self.statusChanged)
		self._set('_auger_duty', int(round((status.get('cycle_ratio', 0) or 0) * 100)), self.statusChanged)
		self._set('_fan_duty', int(status.get('fan_duty', 0) or 0), self.statusChanged)
		self._set('_lid_open', bool(status.get('lid_open_detected', False)), self.statusChanged)
		self._set('_recipe', bool(status.get('recipe', False)), self.statusChanged)
		self._set('_recipe_paused', bool(status.get('recipe_paused', False)), self.statusChanged)
		self._set('_hopper_enabled', bool(status.get('hopper_level_enabled', False)), self.hopperChanged)
		self._set('_hopper_level', max(status.get('hopper_level', 0) or 0, 0), self.hopperChanged)
		self._food_model.update(in_data)
		now = self._now()
		self._update_timer_text(status, now)
		self._update_cook_elapsed(status, now)
		mode = status.get('mode', 'Stop')
		recipe = bool(status.get('recipe', False))
		mode_text = f'Recipe: {mode}' if recipe and mode != 'Shutdown' else mode
		self._set('_mode_text', mode_text, self.modeTextChanged)
		self._set('_p_mode_active', mode in ('Startup', 'Reignite', 'Smoke'), self.statusChanged)
		self._update_idle(mode, now)
		if (now - self._last_settings_check) >= 1.0:
			self._last_settings_check = now
			if self._accent_fn is not None:
				self._set('_accent_theme', self._accent_fn() or 'Ember', self.accentThemeChanged)
			if self._timeout_fn is not None:
				self.TIMEOUT = self._timeout_fn()

	def _update_timer_text(self, status, now):
		mode = status.get('mode', 'Stop')
		duration_key = {
			'Startup': 'start_duration',
			'Reignite': 'start_duration',
			'Prime': 'prime_duration',
			'Shutdown': 'shutdown_duration',
		}.get(mode)
		text = ''
		label = ''
		if duration_key and status.get('start_time'):
			remaining = int(status.get(duration_key, 0) - (now - status['start_time']))
			remaining = max(remaining, 0)
			text = f'{remaining // 60:02d}:{remaining % 60:02d}'
			label = 'Timer'
		elif mode == 'Hold' and status.get('lid_open_detected') and status.get('lid_open_endtime'):
			remaining = max(int(status['lid_open_endtime'] - now), 0)
			text = f'{remaining // 60:02d}:{remaining % 60:02d}'
			label = 'Lid Pause'
		self._set('_timer_text', text, self.timerChanged)
		self._set('_timer_label', label, self.timerChanged)

	def _update_cook_elapsed(self, status, now):
		ts = status.get('startup_timestamp', 0) or 0
		if ts and status.get('mode', 'Stop') not in ('Stop', 'Monitor'):
			secs = max(int(now - ts), 0)
			h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
			text = (f'{h}:' if h else '') + f'{m:02d}:{s:02d}'
		else:
			text = '00:00'
		self._set('_cook_elapsed_text', text, self.timerChanged)

	def _update_idle(self, mode, now):
		# The screen never sleeps during an active cook; in Stop it sleeps after
		# TIMEOUT seconds of no interaction (TIMEOUT <= 0 disables sleeping).
		# Leaving Stop auto-wakes.
		if mode != 'Stop':
			self._set('_asleep', False, self.asleepChanged)
		elif self.TIMEOUT > 0 and now - self._last_interaction > self.TIMEOUT:
			self._set('_asleep', True, self.asleepChanged)

	@Slot()
	def registerInteraction(self):
		self._last_interaction = self._now()
		self._set('_asleep', False, self.asleepChanged)

	# ---------------- Action slots ----------------
	@Slot(str, int)
	@Slot(str)
	def action(self, command, value=0):
		self._command_fn(command, value)

	@Slot()
	def startup(self):
		self.action('cmd_startup')

	@Slot()
	def stop(self):
		self.action('cmd_stop')

	@Slot()
	def monitor(self):
		self.action('cmd_monitor')

	@Slot()
	def shutdown(self):
		self.action('cmd_shutdown')

	@Slot()
	def smoke(self):
		self.action('cmd_smoke')

	@Slot()
	def toggleSmokePlus(self):
		self.action('cmd_splus')

	@Slot()
	def nextStep(self):
		self.action('cmd_next_step')

	@Slot(int)
	def setHold(self, temp):
		self.action('cmd_hold', int(temp))

	@Slot(str, int)
	def setNotify(self, origin, target):
		self._command_fn('cmd_notify', {'origin': origin, 'target': int(target)})

	@Slot(int)
	def setPMode(self, n):
		self.action('cmd_pmode', int(n))

	@Slot(int)
	def primeStartup(self, grams):
		self.action('cmd_primestartup', int(grams))

	@Slot(int)
	def primeOnly(self, grams):
		self.action('cmd_primeonly', int(grams))

	@Slot()
	def reboot(self):
		self.action('cmd_reboot')

	@Slot()
	def powerOff(self):
		self.action('cmd_poweroff')

	@Slot()
	def restart(self):
		self.action('cmd_restart')

	@Slot()
	def hopperCheck(self):
		self.action('cmd_hopper_level')

	@Slot()
	def toggleFan(self):
		self.action('cmd_fan_toggle')

	@Slot()
	def toggleAuger(self):
		self.action('cmd_auger_toggle')

	@Slot()
	def toggleIgniter(self):
		self.action('cmd_igniter_toggle')

	@Slot()
	def toggleLidOpen(self):
		self.action('cmd_lid_open')

	@Slot()
	def navUp(self):
		self.navEvent.emit('UP')

	@Slot()
	def navDown(self):
		self.navEvent.emit('DOWN')

	@Slot()
	def navEnter(self):
		self.navEvent.emit('ENTER')

	# ---------------- Properties ----------------
	@Property(str, notify=modeChanged)
	def mode(self):
		return self._mode

	@Property(str, notify=modeTextChanged)
	def modeText(self):
		return self._mode_text

	@Property(bool, notify=statusChanged)
	def pModeActive(self):
		return self._p_mode_active

	@Property(bool, notify=asleepChanged)
	def asleep(self):
		return self._asleep

	@Property(str, notify=unitsChanged)
	def units(self):
		return self._units

	@Property(float, notify=primaryChanged)
	def primaryTemp(self):
		return float(self._primary_temp)

	@Property(float, notify=primaryChanged)
	def primarySetpoint(self):
		return float(self._primary_sp)

	@Property(str, constant=True)
	def primaryName(self):
		return self._primary_name

	@Property(float, notify=primaryChanged)
	def primaryNotifyTarget(self):
		return float(self._primary_notify)

	@Property(float, constant=True)
	def primaryMax(self):
		return float(self._primary_max)

	@Property(str, constant=True)
	def ipAddress(self):
		return self._ip_address

	@Property(QObject, constant=True)
	def foodProbes(self):
		return self._food_model

	@Property(int, notify=hopperChanged)
	def hopperLevel(self):
		return int(self._hopper_level)

	@Property(bool, notify=hopperChanged)
	def hopperEnabled(self):
		return self._hopper_enabled

	@Property(int, notify=statusChanged)
	def pMode(self):
		return self._p_mode

	@Property(int, notify=statusChanged)
	def augerDuty(self):
		return self._auger_duty

	@Property(int, notify=statusChanged)
	def fanDuty(self):
		return self._fan_duty

	@Property(int, constant=True)
	def foodProbeCount(self):
		return self._food_count

	@Property(str, notify=timerChanged)
	def cookElapsedText(self):
		return self._cook_elapsed_text

	@Property(bool, notify=statusChanged)
	def smokePlus(self):
		return self._s_plus

	@Property(bool, notify=statusChanged)
	def fanOn(self):
		return self._fan

	@Property(bool, notify=statusChanged)
	def augerOn(self):
		return self._auger

	@Property(bool, notify=statusChanged)
	def igniterOn(self):
		return self._igniter

	@Property(bool, notify=statusChanged)
	def lidOpen(self):
		return self._lid_open

	@Property(bool, notify=statusChanged)
	def recipe(self):
		return self._recipe

	@Property(bool, notify=statusChanged)
	def recipePaused(self):
		return self._recipe_paused

	@Property(str, notify=timerChanged)
	def timerText(self):
		return self._timer_text

	@Property(str, notify=timerChanged)
	def timerLabel(self):
		return self._timer_label

	@Property(str, notify=accentThemeChanged)
	def accentTheme(self):
		return self._accent_theme
