"""End-to-end work-cycle tests against a REAL Valkey server.

These re-run a subset of the golden-master characterization scenarios
(tests/characterization/test_modes_golden.py) with the control-loop state
living in an actual `valkey-server` via `ValkeyStore`, instead of the hermetic
`InMemoryStore`. Everything else stays a fake: grill/probes/distance devices,
the notifier (`FakeNotifier`), and the clock (`ManualClock`). Only the STORE is
real -- this is the seam that exercises common.common's live Valkey funcs
(deferred deep-merge control writes, metrics rpop/rpush, the display queue).

WHY THESE PROVE PARITY: each scenario asserts the exact same outcomes the
InMemoryStore golden test asserts for the same inputs. If a scenario passes
under InMemoryStore but diverges here, the InMemoryStore fake has drifted from
real Valkey semantics -- fix the fake, not the test.

ONE UNAVOIDABLE SERIALIZATION DIFFERENCE: the display queue round-trips through
JSON in real Valkey (common/valkey_queue.py), so a command pushed as the tuple
('text', 'ERROR') reads back as the list ['text', 'ERROR']. That is real
production behavior (JSON has no tuple type), not a bug, so the display-command
assertions here use list form. All other observable state (grill calls,
notifications, final control mode/flags, metrics) is store-independent or
JSON-clean and matches the golden values exactly.

SETTINGS ARE NOT VALKEY-BACKED: in PiFire `read_settings()` reads settings.json
from disk, not Valkey (see common.common.read_settings). So the test injects
the scenario's settings by monkeypatching `common.common.read_settings`; this
does not weaken the "real store" guarantee for the control/status/current/
metrics/queue paths, which are the only Valkey-backed state.

RUN INSTRUCTIONS:
    valkey-server must be listening on localhost:6379.
    python -m pytest tests/e2e -v
Without a reachable server the whole module SKIPS (same gate as the parity
suite, tests/test_valkey_store_parity.py).

RESIDUE: each scenario snapshots and restores control:general and status so a
real instance is left as it was found. control:current and metrics are not
restored -- both are rewritten sub-second by the live control loop (metrics
flushing also matches the existing parity suite), so any residue is transient
and immediately clobbered when a real instance resumes.
"""

import pytest

valkey = pytest.importorskip('valkey')


def _valkey_available():
	try:
		valkey.StrictValkey('localhost', 6379, socket_connect_timeout=0.2).ping()
		return True
	except Exception:
		return False


pytestmark = pytest.mark.skipif(not _valkey_available(), reason='no local valkey-server')

import common.common as _ccommon
from common.common import WriteKind
from controller.runtime.store import ValkeyStore
from controller.runtime.runner import NormalizedOutput

from tests.characterization.harness import run_mode
from tests.characterization.fixtures import base_settings, base_control, base_pellet_db
from tests.fakes.probes import FakeProbes
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.runner import FakeControllerRunner


def run_valkey_scenario(
	monkeypatch, mode, *, settings, control_data, pellet_db, probes, grill=None, probe_cap=None, runner=None
):
	"""Seed a live ValkeyStore, run one work cycle against it, restore residue.

	Returns the same `CaptureResult` shape as the InMemoryStore harness, so
	scenario assertions are identical to the golden tests (modulo the display
	queue's JSON tuple->list round-trip -- see module docstring)."""
	store = ValkeyStore()

	# Snapshot the keys we can faithfully restore so a real instance is left as
	# found. (control:current is intentionally not snapshotted: read_current()
	# returns a transformed P/F/AUX/... payload that write_current() can't
	# round-trip, and the live loop rewrites it sub-second anyway.)
	saved_control = store.read_control()
	saved_status = store.read_status()

	# Settings are file-backed in PiFire, not Valkey-backed: inject the
	# scenario's settings so every ctx.store.read_settings() (-> common.read_settings)
	# returns them, without touching settings.json on disk.
	monkeypatch.setattr(_ccommon, 'read_settings', lambda *a, **k: settings)

	try:
		# Flush + seed the genuinely Valkey-backed state.
		store.system_commands().flush()
		store.system_output().flush()
		store.display_commands().flush()
		store.write_metrics(flush=True)
		store.write_control(control_data, WriteKind.OVERWRITE)
		store.write_pellet_db(pellet_db)

		return run_mode(
			mode,
			settings=settings,
			control_data=control_data,
			pellet_db=pellet_db,
			probes=probes,
			grill=grill,
			probe_cap=probe_cap,
			runner=runner,
			store=store,
		)
	finally:
		# Best-effort restore. write_status assumes a fully-formed payload, so
		# only write back a snapshot that was actually present -- on a fresh
		# Valkey status is empty and there is nothing to restore (the test's
		# final status simply remains, harmless on a dev/test instance).
		store.write_control(saved_control, WriteKind.OVERWRITE)
		if saved_status:
			store.write_status(saved_status)


def test_e2e_smoke_over_maxtemp_triggers_error_and_notifies(monkeypatch):
	settings = base_settings()
	settings['safety']['maxtemp'] = 500
	probes = FakeProbes().script([550, 550, 550])
	control_data = base_control(mode='Smoke')
	result = run_valkey_scenario(
		monkeypatch, 'Smoke', settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes
	)
	assert result.final_control['mode'] == 'Error'
	assert 'Grill_Error_01' in result.notifications
	# display queue round-trips through JSON in real Valkey -> list, not tuple.
	assert ['text', 'ERROR'] in result.display_commands


def test_e2e_smoke_flameout_with_retries_triggers_reignite(monkeypatch):
	settings = base_settings()
	control_data = base_control(mode='Smoke')
	control_data['safety']['startuptemp'] = 150
	control_data['safety']['afterstarttemp'] = 100
	control_data['safety']['reigniteretries'] = 2
	probes = FakeProbes().script([100, 100, 100])
	result = run_valkey_scenario(
		monkeypatch, 'Smoke', settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes
	)
	assert result.final_control['mode'] == 'Reignite'
	assert result.final_control['safety']['reigniteretries'] == 1  # decremented
	assert result.final_control['safety']['reignitelaststate'] == 'Smoke'
	assert 'Grill_Error_03' in result.notifications
	assert ['text', 'Re-Ignite'] in result.display_commands


def test_e2e_smoke_flameout_without_retries_triggers_error(monkeypatch):
	settings = base_settings()
	control_data = base_control(mode='Smoke')
	control_data['safety']['startuptemp'] = 150
	control_data['safety']['afterstarttemp'] = 100
	control_data['safety']['reigniteretries'] = 0
	probes = FakeProbes().script([100, 100, 100])
	result = run_valkey_scenario(
		monkeypatch, 'Smoke', settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes
	)
	assert result.final_control['mode'] == 'Error'
	assert result.final_control['safety']['reigniteretries'] == 0
	assert 'Grill_Error_02' in result.notifications
	assert ['text', 'ERROR'] in result.display_commands


def test_e2e_hold_pwm_duty_from_temp_profile(monkeypatch):
	# Hold cycle: pwm_control + dc_fan -> duty_cycle set from the temp-profile
	# table and pushed to the grill. Exercises the deferred control MERGE
	# (duty_cycle) landing in real Valkey via execute_control_writes.
	settings = base_settings()
	settings['platform']['dc_fan'] = True
	settings['pwm']['update_time'] = 0
	control_data = base_control(mode='Hold')
	control_data['pwm_control'] = True
	control_data['primary_setpoint'] = 225
	probes = FakeProbes().script([210] * 8)
	grill = FakeGrillPlatform(dc_fan=True)
	result = run_valkey_scenario(
		monkeypatch,
		'Hold',
		settings=settings,
		control_data=control_data,
		pellet_db=base_pellet_db(),
		probes=probes,
		probe_cap=6,
		grill=grill,
	)
	assert result.final_control['duty_cycle'] == 75
	assert ('set_duty_cycle', (75,)) in result.grill_calls


def test_e2e_prime_elapses_after_prime_duration(monkeypatch):
	settings = base_settings()
	settings['globals']['augerrate'] = 10
	control_data = base_control(mode='Prime')
	control_data['prime_amount'] = 10  # -> prime_duration = 1 (tiny)
	control_data['next_mode'] = 'Startup'
	probes = FakeProbes().script([70] * 5)
	result = run_valkey_scenario(
		monkeypatch, 'Prime', settings=settings, control_data=control_data, pellet_db=base_pellet_db(), probes=probes
	)
	assert result.final_control['mode'] == 'Prime'
	assert result.final_control['updated'] is False
	assert ('power_on', ()) in result.grill_calls
	assert result.grill_calls[-2:] == [('fan_off', ()), ('power_off', ())]
	assert result.final_metrics['augerontime'] > 0
