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
