import pytest

valkey = pytest.importorskip("valkey")


def _valkey_available():
	try:
		valkey.StrictValkey('localhost', 6379, socket_connect_timeout=0.2).ping()
		return True
	except Exception:
		return False


pytestmark = pytest.mark.skipif(not _valkey_available(), reason="no local valkey-server")


def test_valkey_store_smoke():
	# Read-only smoke: exercises the pass-through against a live server
	# without writing (leaves no residue on a real instance's Valkey).
	from controller.runtime.store import ValkeyStore
	s = ValkeyStore()
	assert isinstance(s.read_control(), dict)
	assert isinstance(s.read_settings(), dict)


def test_valkey_display_queue_roundtrip():
	from controller.runtime.store import ValkeyStore
	s = ValkeyStore()
	s.display_commands().flush()
	s.display_commands().push(['text', 'ERROR'])
	assert s.display_commands().drain() == [['text', 'ERROR']]


def test_valkey_write_metrics_new_metric_without_metrics_does_not_crash():
	# Regression: write_metrics(new_metric=True) with no metrics must defer to
	# common's default_metrics() (passing None crashed on metrics['starttime']).
	# The control loop calls this at the start of every work cycle.
	from controller.runtime.store import ValkeyStore
	s = ValkeyStore()
	s.write_metrics(flush=True)          # reset metrics list
	s.write_metrics(new_metric=True)     # must NOT raise
	current = s.read_metrics()
	assert isinstance(current, dict)
	assert 'starttime' in current        # populated from default_metrics() + starttime


def test_valkey_control_write_semantics_parity():
	# Proves ValkeyStore's OVERWRITE / MERGE / execute_control_writes match the
	# deferred deep-merge semantics that InMemoryStore replicates, against a live
	# server. Saves and restores control:general so it leaves no residue.
	from common.common import WriteKind
	from controller.runtime.store import ValkeyStore
	s = ValkeyStore()
	saved = s.read_control()
	try:
		s.write_control({'mode': 'Stop', 'nested': {'x': 1, 'y': 2}}, WriteKind.OVERWRITE)
		assert s.read_control() == {'mode': 'Stop', 'nested': {'x': 1, 'y': 2}}
		# MERGE is deferred until execute_control_writes
		s.write_control({'nested': {'x': 9}}, WriteKind.MERGE, origin='test')
		assert s.read_control()['nested'] == {'x': 1, 'y': 2}
		s.execute_control_writes()
		# deep-merged: x replaced, y preserved, mode untouched, origin stripped
		assert s.read_control()['nested'] == {'x': 9, 'y': 2}
		assert s.read_control()['mode'] == 'Stop'
		assert 'origin' not in s.read_control()
	finally:
		s.write_control(saved, WriteKind.OVERWRITE)


def test_valkey_write_metrics_replace_last_parity():
	# write_metrics(metrics) with new_metric=False replaces the last record
	# (rpop+rpush), matching InMemoryStore's replace-last behavior.
	from controller.runtime.store import ValkeyStore
	s = ValkeyStore()
	s.write_metrics(flush=True)
	s.write_metrics(new_metric=True)
	metrics = s.read_metrics()
	metrics['mode'] = 'Hold'
	s.write_metrics(metrics)
	assert s.read_metrics()['mode'] == 'Hold'
	assert len(s.read_metrics(all=True)) == 1  # replaced, not appended
