"""Golden-master characterization tests for control._work_cycle.

METHOD: RUN-THEN-FREEZE. Each scenario seeds inputs, runs `run_mode(...)`
once, and asserts against captured behavior that was verified by actually
running the current code (not against what we expect it "should" do). These
are the equivalence oracle for the Phase 5-7 decomposition -- if a refactor
changes any of these captured behaviors, that's a regression to investigate,
not necessarily a bug in this test.

TERMINATION SAFETY: every scenario either (a) relies on a natural loop exit
already present in control.py (max-temp safety, flameout safety check,
Startup/Shutdown/Prime timers, Reignite/Error mode transitions) with those
timers shrunk to a few ManualClock ticks via fixtures.base_settings(), or (b)
passes `probe_cap=` to bound modes with no natural exit (Smoke steady-state,
Hold, Monitor, Manual) via the harness's capped-probe injection. No scenario
here can loop indefinitely.
"""
from tests.characterization.harness import run_mode
from tests.characterization.fixtures import base_settings, base_control, base_pellet_db
from tests.fakes.probes import FakeProbes
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.runner import FakeControllerRunner
from controller.runtime.runner import NormalizedOutput


def test_smoke_over_maxtemp_triggers_error_and_notifies():
	settings = base_settings()
	settings['safety']['maxtemp'] = 500
	probes = FakeProbes().script([550, 550, 550])
	control_data = base_control(mode='Smoke')
	result = run_mode('Smoke', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes)
	assert result.final_control['mode'] == 'Error'
	assert 'Grill_Error_01' in result.notifications
	assert ('text', 'ERROR') in result.display_commands


def test_smoke_flameout_with_retries_triggers_reignite():
	# Pre-loop safety check: control['safety']['afterstarttemp'] already below
	# startuptemp when the mode starts (simulates a flameout carried over from
	# a previous cycle) -- fires before the main loop even begins iterating.
	settings = base_settings()
	control_data = base_control(mode='Smoke')
	control_data['safety']['startuptemp'] = 150
	control_data['safety']['afterstarttemp'] = 100
	control_data['safety']['reigniteretries'] = 2
	probes = FakeProbes().script([100, 100, 100])
	result = run_mode('Smoke', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes)
	assert result.final_control['mode'] == 'Reignite'
	assert result.final_control['safety']['reigniteretries'] == 1  # decremented
	assert result.final_control['safety']['reignitelaststate'] == 'Smoke'
	assert 'Grill_Error_03' in result.notifications
	assert ('text', 'Re-Ignite') in result.display_commands


def test_smoke_flameout_without_retries_triggers_error():
	settings = base_settings()
	control_data = base_control(mode='Smoke')
	control_data['safety']['startuptemp'] = 150
	control_data['safety']['afterstarttemp'] = 100
	control_data['safety']['reigniteretries'] = 0
	probes = FakeProbes().script([100, 100, 100])
	result = run_mode('Smoke', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes)
	assert result.final_control['mode'] == 'Error'
	assert result.final_control['safety']['reigniteretries'] == 0
	assert 'Grill_Error_02' in result.notifications
	assert ('text', 'ERROR') in result.display_commands


def test_startup_exits_on_exit_temp():
	settings = base_settings()
	settings['startup']['startup_exit_temp'] = 100
	settings['startup']['duration'] = 100  # large so the timer can't fire first
	control_data = base_control(mode='Startup')
	probes = FakeProbes().script([50, 60, 105, 105, 105])
	result = run_mode('Startup', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes)
	# Natural timer/temp exits break the loop without setting control['updated'];
	# only the Error/Reignite/mode-change paths do that.
	assert result.final_control['mode'] == 'Startup'
	assert result.final_control['updated'] is False
	assert result.notifications == []
	# Igniter/auger cycle ran, then clean-up turned both off.
	assert ('igniter_on', ()) in result.grill_calls
	assert result.grill_calls[-2:] == [('auger_off', ()), ('igniter_off', ())]


def test_startup_exits_on_timer():
	settings = base_settings()
	settings['startup']['duration'] = 0.1
	settings['startup']['startup_exit_temp'] = 0  # disabled, timer must fire first
	control_data = base_control(mode='Startup')
	probes = FakeProbes().script([50] * 8)
	result = run_mode('Startup', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes)
	assert result.final_control['mode'] == 'Startup'
	assert result.final_control['updated'] is False
	assert result.notifications == []


def test_prime_elapses_after_prime_duration():
	settings = base_settings()
	settings['globals']['augerrate'] = 10  # prime_duration = int(prime_amount / augerrate)
	control_data = base_control(mode='Prime')
	control_data['prime_amount'] = 10  # -> prime_duration = 1 (tiny)
	control_data['next_mode'] = 'Startup'
	probes = FakeProbes().script([70] * 5)
	result = run_mode('Prime', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes)
	assert result.final_control['mode'] == 'Prime'
	assert result.final_control['updated'] is False
	# Prime mode: fan off, power on for the cycle; clean-up turns fan/power off.
	assert ('power_on', ()) in result.grill_calls
	assert result.grill_calls[-2:] == [('fan_off', ()), ('power_off', ())]
	# augerontime metric was accumulated during the cycle.
	assert result.final_metrics['augerontime'] > 0


def test_shutdown_elapses_after_shutdown_duration():
	settings = base_settings()
	settings['shutdown']['shutdown_duration'] = 0.1
	control_data = base_control(mode='Shutdown')
	probes = FakeProbes().script([150] * 5)
	result = run_mode('Shutdown', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes)
	assert result.final_control['mode'] == 'Shutdown'
	assert result.final_control['updated'] is False
	assert result.grill_calls[-2:] == [('fan_off', ()), ('power_off', ())]


def test_smoke_auger_cycles_on_and_off():
	# Shrink the smoke auger on/off cycle times so several full cycles happen
	# within a small, bounded number of probe reads (steady-state Smoke has no
	# natural exit, so probe_cap bounds it).
	settings = base_settings()
	settings['cycle_data']['SmokeOnCycleTime'] = 0.1
	settings['cycle_data']['SmokeOffCycleTime'] = 0.1
	settings['cycle_data']['PMode'] = 0
	control_data = base_control(mode='Smoke')
	probes = FakeProbes().script([200])  # steady temp; never trips safety/maxtemp
	result = run_mode('Smoke', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=30)
	auger_calls = [c for c in result.grill_calls if c[0] in ('auger_on', 'auger_off')]
	names = [c[0] for c in auger_calls]
	on_count = names.count('auger_on')
	assert on_count >= 3  # multiple on/off cycles observed
	# Sequence: setup 'auger_off', then strictly-alternating on/off cycling,
	# then a final cleanup 'auger_off' (control.py always calls auger_off on
	# exit regardless of current state, so the last two entries repeat).
	assert names[0] == 'auger_off'
	assert names[-1] == 'auger_off'
	middle = names[1:-1]
	for a, b in zip(middle, middle[1:]):
		assert a != b
	assert middle[0] == 'auger_on'
	assert result.final_control['mode'] == 'Smoke'
	assert result.final_control['updated'] is True  # ended via harness's probe-cap injection


def test_monitor_idles_powered_off_and_bounded_by_probe_cap():
	settings = base_settings()
	control_data = base_control(mode='Monitor')
	probes = FakeProbes().script([120])
	result = run_mode('Monitor', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=30)
	# Monitor starts powered off and stays that way.
	assert result.grill_calls[:4] == [
		('igniter_off', ()), ('auger_off', ()), ('fan_off', ()), ('power_off', ())
	]
	assert result.grill_calls[-2:] == [('fan_off', ()), ('power_off', ())]
	assert ('power_on', ()) not in result.grill_calls
	assert result.final_control['mode'] == 'Monitor'
	assert result.final_control['updated'] is True  # ended via harness's probe-cap injection


def test_manual_override_fan_on_applies_and_records_grill_call():
	settings = base_settings()
	control_data = base_control(mode='Manual')
	control_data['manual']['change'] = 'fan'
	control_data['manual']['output'] = True
	probes = FakeProbes().script([120])
	result = run_mode('Manual', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=5)
	assert ('fan_on', (None,)) in result.grill_calls
	# The manual request is consumed (cleared) after being applied.
	assert result.final_control['manual']['change'] is None
	assert result.final_control['manual']['output'] is None


def test_hold_lid_open_stops_auger_and_fan():
	settings = base_settings()
	settings['cycle_data']['HoldCycleTime'] = 0.2
	settings['cycle_data']['LidOpenDetectEnabled'] = True
	settings['cycle_data']['LidOpenThreshold'] = 15
	control_data = base_control(mode='Hold')
	control_data['primary_setpoint'] = 225
	# Reach setpoint first (arms target_temp_achieved), then a sharp temp drop
	# triggers the lid-open detector.
	probes = FakeProbes().script([230, 230, 230] + [150] * 10)
	result = run_mode('Hold', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=13)
	# Lid-open response: auger_off immediately followed by fan_off.
	names = [c[0] for c in result.grill_calls]
	fan_off_idx = names.index('fan_off')
	assert names[fan_off_idx - 1] == 'auger_off'
	assert result.final_control['mode'] == 'Hold'


def test_startup_smartstart_selects_profile_by_temp():
	# Pins the currently-UNGUARDED smart-start wiring (control.py lines ~301-320):
	# select_profile() picks an index from temp_range_list based on the initial
	# probe read, and profile_cycle() applies that profile's augerontime/p_mode/
	# startuptime -- reflected into both control['smartstart'] and metrics.
	settings = base_settings()
	settings['startup']['smartstart']['enabled'] = True
	settings['startup']['smartstart']['temp_range_list'] = [60, 80, 90]
	settings['startup']['duration'] = 1000  # large: must not be what ends the run
	settings['startup']['startup_exit_temp'] = 0
	control_data = base_control(mode='Startup')
	# 70 falls under temp_range_list[1]=80 (not under [0]=60) -> profile index 1.
	probes = FakeProbes().script([70] * 10)
	result = run_mode('Startup', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=8)
	profile = settings['startup']['smartstart']['profiles'][1]
	assert result.final_control['smartstart']['profile_selected'] == 1
	assert result.final_control['smartstart']['startuptemp'] == 70
	assert result.final_metrics['smart_start_profile'] == 1
	assert result.final_metrics['startup_temp'] == 70
	assert result.final_metrics['p_mode'] == profile['p_mode']
	assert result.final_metrics['auger_cycle_time'] == profile['augerontime']
	# Run was bounded by the harness's probe-cap injection, not the (huge)
	# startup duration/timer, confirming smartstart's own startup_timer (360s
	# for this profile) is what's in play, not the settings default.
	assert result.final_control['updated'] is True


def test_manual_override_auger_igniter_power_record_grill_calls():
	# Existing coverage (test_manual_override_fan_on_applies_and_records_grill_call)
	# only exercises the fan branch of the manual-override block (control.py
	# ~447-506); this pins the auger/igniter/power branches too.
	settings = base_settings()
	for change in ('auger', 'igniter', 'power'):
		control_data = base_control(mode='Manual')
		control_data['manual']['change'] = change
		control_data['manual']['output'] = True
		probes = FakeProbes().script([120])
		result = run_mode('Manual', settings=settings, control_data=control_data,
		                   pellet_db=base_pellet_db(), probes=probes, probe_cap=5)
		assert (f'{change}_on', ()) in result.grill_calls
		assert result.final_control['manual']['change'] is None
		assert result.final_control['manual']['output'] is None


def test_manual_override_pwm_sets_duty_cycle():
	# PWM manual-override branch (control.py ~492-502) is gated on
	# current_output_status['fan'] already being True, which Manual mode's own
	# setup always clears (fan_off/power_off) -- so this scenario uses Hold
	# (whose setup turns the fan on) with allow_manual_changes=True, the same
	# gate real non-Manual-mode manual overrides use in production.
	settings = base_settings()
	settings['platform']['dc_fan'] = True
	settings['safety']['allow_manual_changes'] = True
	settings['cycle_data']['HoldCycleTime'] = 100  # avoid auger-cycle noise
	control_data = base_control(mode='Hold')
	control_data['manual']['change'] = 'pwm'
	control_data['manual']['pwm'] = 55
	probes = FakeProbes().script([120] * 8)
	grill = FakeGrillPlatform(dc_fan=True)
	result = run_mode('Hold', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=5, grill=grill)
	assert ('set_duty_cycle', (55,)) in result.grill_calls
	# Manual PWM request is consumed: reset to 100 per control.py line 502.
	assert result.final_control['manual']['pwm'] == 100


def test_hold_pwm_duty_from_temp_profile():
	# Hold + pwm_control=True + dc_fan: control['duty_cycle'] is set from the
	# temperature-profile table (hold_duty_cycle), not from the controller.
	settings = base_settings()
	settings['platform']['dc_fan'] = True
	settings['pwm']['update_time'] = 0  # fire the temp-profile branch every tick
	control_data = base_control(mode='Hold')
	control_data['pwm_control'] = True
	control_data['primary_setpoint'] = 225
	# setpoint - ptemp = 15 -> temp_range_list [3,7,10,15] index 3 -> duty 75.
	probes = FakeProbes().script([210] * 8)
	grill = FakeGrillPlatform(dc_fan=True)
	result = run_mode('Hold', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=6, grill=grill)
	assert result.final_control['duty_cycle'] == 75
	assert ('set_duty_cycle', (75,)) in result.grill_calls


def test_hold_fan_assist_cycles_fan_via_pid_path():
	# FanPidEnabled + pwm_control=False + a controller ratio below u_min: the
	# auger floors to u_min and the Fan-PID-assist block (control.py ~749-783)
	# takes over, cycling the fan on/off by fan_assist_times() rather than
	# holding it continuously on. A scripted FakeControllerRunner keeps this
	# deterministic rather than depending on the real PID's transient math.
	settings = base_settings()
	settings['platform']['dc_fan'] = True
	settings['cycle_data']['FanPidEnabled'] = True
	settings['cycle_data']['HoldCycleTime'] = 0.3  # small -> small fan on/off times
	settings['cycle_data']['u_min'] = 0.1
	control_data = base_control(mode='Hold')
	control_data['pwm_control'] = False
	control_data['primary_setpoint'] = 225
	probes = FakeProbes().script([230] * 60)  # >= setpoint: arms target_temp_achieved
	grill = FakeGrillPlatform(dc_fan=True)
	runner = FakeControllerRunner(period=0.01).script(
		[NormalizedOutput(cycle_ratio=0.02, fan=None)] * 60
	)
	result = run_mode('Hold', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=50,
	                   grill=grill, runner=runner)
	fan_calls = [c[0] for c in result.grill_calls if c[0] in ('fan_on', 'fan_off')]
	# More than the single setup fan_on: the assist path is actively cycling.
	assert fan_calls.count('fan_on') >= 2
	assert fan_calls.count('fan_off') >= 2
	assert fan_calls[0] == 'fan_on'  # mode setup turns fan on first
	assert 'fan_off' in fan_calls[1:]  # then the PID-fan-assist path cycles it


def test_hold_controller_fan_duty_sticky_latch_suppresses_temp_profile():
	# Pins CURRENT behavior of the `controller_fan_duty` sticky latch
	# (control.py ~510-526 sets it from an MPC fan command; ~733-744's
	# temp-profile PWM path is gated `controller_fan_duty is None`, so once an
	# MPC command has been seen even once, later plain-float controller
	# outputs (no 'fan' key) do NOT re-enable the temp-profile path for the
	# rest of this _work_cycle invocation -- it stays latched at the last MPC
	# duty rather than reverting to (or ever computing) a temp-profile value.
	settings = base_settings()
	settings['platform']['dc_fan'] = True
	settings['pwm']['update_time'] = 0  # temp-profile branch would fire every tick if unlatched
	control_data = base_control(mode='Hold')
	control_data['pwm_control'] = True
	control_data['primary_setpoint'] = 225
	# ptemp=210 -> setpoint-ptemp=15 -> temp-profile duty would be 75 if the
	# latch didn't suppress it.
	probes = FakeProbes().script([210] * 30)
	grill = FakeGrillPlatform(dc_fan=True)
	runner = FakeControllerRunner(period=0.01).script([
		NormalizedOutput(cycle_ratio=0.5, fan={'duty': 42}),
		NormalizedOutput(cycle_ratio=0.5, fan=None),
		NormalizedOutput(cycle_ratio=0.5, fan=None),
		NormalizedOutput(cycle_ratio=0.5, fan=None),
	])
	result = run_mode('Hold', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=20,
	                   grill=grill, runner=runner)
	assert result.final_control['duty_cycle'] == 42
	set_duty_calls = [c for c in result.grill_calls if c[0] == 'set_duty_cycle']
	assert set_duty_calls == [('set_duty_cycle', (42,))]  # never overwritten to 75


def test_smoke_plus_cycles_fan_on_and_off():
	# Smoke mode + s_plus=True, steady mid-band temperature: the fan cycles
	# purely on the smoke_plus on_time/off_time timer (control.py ~786-821),
	# since ptemp stays inside [min_temp, max_temp] the whole run.
	settings = base_settings()
	settings['platform']['dc_fan'] = True
	settings['smoke_plus']['on_time'] = 0.1
	settings['smoke_plus']['off_time'] = 0.1
	settings['smoke_plus']['min_temp'] = 160
	settings['smoke_plus']['max_temp'] = 220
	settings['cycle_data']['SmokeOnCycleTime'] = 100  # keep auger toggling out of the way
	settings['cycle_data']['SmokeOffCycleTime'] = 100
	control_data = base_control(mode='Smoke')
	control_data['s_plus'] = True
	probes = FakeProbes().script([190] * 40)
	grill = FakeGrillPlatform(dc_fan=True)
	result = run_mode('Smoke', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=30, grill=grill)
	fan_calls = [c[0] for c in result.grill_calls if c[0] in ('fan_on', 'fan_off')]
	assert fan_calls.count('fan_on') >= 3
	assert fan_calls.count('fan_off') >= 3
	# Strictly alternating (no double-on / double-off in a row).
	for a, b in zip(fan_calls, fan_calls[1:]):
		assert a != b


def test_hold_lid_open_clears_and_restores_fan_after_pause_time():
	# Complements test_hold_lid_open_stops_auger_and_fan: once
	# LidOpenPauseTime elapses, LidOpenDetect clears and _start_fan() is
	# called again to resume fan control (control.py ~716-719).
	settings = base_settings()
	settings['cycle_data']['HoldCycleTime'] = 0.2
	settings['cycle_data']['LidOpenDetectEnabled'] = True
	settings['cycle_data']['LidOpenThreshold'] = 15
	settings['cycle_data']['LidOpenPauseTime'] = 0.15
	control_data = base_control(mode='Hold')
	control_data['primary_setpoint'] = 225
	probes = FakeProbes().script([230, 230, 230] + [150] * 5 + [230] * 20)
	grill = FakeGrillPlatform()
	result = run_mode('Hold', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=28, grill=grill)
	names = [c[0] for c in result.grill_calls]
	fan_off_idx = names.index('fan_off')
	assert 'fan_on' in names[fan_off_idx + 1:]  # fan restored after the pause elapses
	assert result.final_control['mode'] == 'Hold'


def test_hold_lid_open_manual_toggle_stops_auger_and_fan():
	# The manual control['lid_open_toggle'] flag (control.py ~720-730) is a
	# distinct trigger from the automatic threshold detector: when
	# LidOpenDetect is currently False, toggling it flips LidOpenDetect True
	# and immediately stops auger+fan, exactly like the automatic path -- and
	# the toggle flag itself is always cleared after being read.
	settings = base_settings()
	settings['cycle_data']['HoldCycleTime'] = 0.2
	control_data = base_control(mode='Hold')
	control_data['primary_setpoint'] = 225
	control_data['lid_open_toggle'] = True
	probes = FakeProbes().script([220] * 15)
	grill = FakeGrillPlatform()
	result = run_mode('Hold', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=10, grill=grill)
	names = [c[0] for c in result.grill_calls]
	fan_off_idx = names.index('fan_off')
	assert names[fan_off_idx - 1] == 'auger_off'
	assert result.final_control['lid_open_toggle'] is False


def test_recipe_overlay_triggered_without_pause_breaks_and_notifies():
	# Recipe overlay (control['mode']=='Recipe', orthogonal to the `mode`
	# parameter) end-of-loop check (control.py ~918-925): a triggered step
	# with pause=False sends Recipe_Step_Message and breaks the loop
	# immediately, without otherwise touching control['mode'].
	settings = base_settings()
	control_data = base_control(mode='Smoke')
	control_data['mode'] = 'Recipe'
	control_data['recipe']['step_data'] = {
		'timer': 0,
		'trigger_temps': {},
		'triggered': True,
		'pause': False,
		'notify': True,
	}
	probes = FakeProbes().script([190] * 10)
	result = run_mode('Smoke', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=8)
	assert result.final_control['mode'] == 'Recipe'
	assert 'Recipe_Step_Message' in result.notifications
	# Broke via the Recipe check itself, not the harness's probe-cap injection.
	assert result.final_control['updated'] is False


def test_recipe_overlay_triggered_with_pause_notifies_once_and_continues():
	# Same trigger but pause=True: notify fires once (notify flag is cleared
	# immediately after) and the loop keeps running until something else ends
	# it -- here, the harness's probe-cap injection, confirming the Recipe
	# pause branch does NOT break on its own.
	settings = base_settings()
	control_data = base_control(mode='Smoke')
	control_data['mode'] = 'Recipe'
	control_data['recipe']['step_data'] = {
		'timer': 0,
		'trigger_temps': {},
		'triggered': True,
		'pause': True,
		'notify': True,
	}
	probes = FakeProbes().script([190] * 10)
	result = run_mode('Smoke', settings=settings, control_data=control_data,
	                   pellet_db=base_pellet_db(), probes=probes, probe_cap=8)
	assert result.final_control['mode'] == 'Recipe'
	assert result.notifications == ['Recipe_Step_Message']  # sent exactly once
	assert result.final_control['recipe']['step_data']['notify'] is False
	# This time the harness's probe-cap is what ended the run, not the Recipe check.
	assert result.final_control['updated'] is True
