import pytest


@pytest.fixture
def store(tmp_path):
	from common import datastore

	datastore._reset_for_tests(str(tmp_path / 't.db'))
	datastore.init()
	from controller.runtime.store import SqliteStore

	yield SqliteStore()
	datastore._reset_for_tests(None)


def test_sqlite_store_smoke(store):
	# Read-only smoke: exercises the pass-through against a hermetic SQLite DB.
	assert isinstance(store.read_control(), dict)
	assert isinstance(store.read_settings(), dict)


def test_sqlite_display_queue_roundtrip(store):
	store.display_commands().flush()
	store.display_commands().push(['text', 'ERROR'])
	assert store.display_commands().drain() == [['text', 'ERROR']]


def test_sqlite_write_metrics_new_metric_without_metrics_does_not_crash(store):
	# Regression: write_metrics(new_metric=True) with no metrics must defer to
	# common's default_metrics() (passing None crashed on metrics['starttime']).
	# The control loop calls this at the start of every work cycle.
	store.write_metrics(flush=True)  # reset metrics list
	store.write_metrics(new_metric=True)  # must NOT raise
	current = store.read_metrics()
	assert isinstance(current, dict)
	assert 'starttime' in current  # populated from default_metrics() + starttime


def test_sqlite_control_write_semantics_parity(store):
	# Proves SqliteStore's OVERWRITE / MERGE / execute_control_writes match the
	# deferred deep-merge semantics that InMemoryStore replicates, against a
	# real (temp-DB) SQLite backend.
	from common.common import WriteKind

	store.write_control({'mode': 'Stop', 'nested': {'x': 1, 'y': 2}}, WriteKind.OVERWRITE)
	assert store.read_control() == {'mode': 'Stop', 'nested': {'x': 1, 'y': 2}}
	# MERGE is deferred until execute_control_writes
	store.write_control({'nested': {'x': 9}}, WriteKind.MERGE, origin='test')
	assert store.read_control()['nested'] == {'x': 1, 'y': 2}
	store.execute_control_writes()
	# deep-merged: x replaced, y preserved, mode untouched, origin stripped
	assert store.read_control()['nested'] == {'x': 9, 'y': 2}
	assert store.read_control()['mode'] == 'Stop'
	assert 'origin' not in store.read_control()


def test_control_merge_null_handling_parity(store):
	# SqliteStore (real json_patch) and InMemoryStore (deep_update) must agree on
	# null handling: dict-nested nulls are ignored (key kept), list-nested nulls
	# are preserved. This is the contract that keeps the two backends swappable.
	from common.common import WriteKind
	from controller.runtime.store import InMemoryStore

	seed = {'mode': 'Stop', 'manual': {'change': 'pwm'}, 'notify_data': [{'eta': 0}]}
	partial = {'mode': None, 'primary_setpoint': 275, 'manual': {'change': None}, 'notify_data': [{'eta': None}]}
	expected = {
		'mode': 'Stop',  # client null ignored
		'primary_setpoint': 275,  # non-null applied
		'manual': {'change': 'pwm'},  # dict-nested null ignored, key kept
		'notify_data': [{'eta': None}],  # list replaced atomically, null preserved
	}

	mem = InMemoryStore()
	for st in (store, mem):
		st.write_control(dict(seed), WriteKind.OVERWRITE)
		st.write_control(dict(partial), WriteKind.MERGE, origin='app')
		st.execute_control_writes()
		assert st.read_control() == expected


def test_sqlite_write_metrics_replace_last_parity(store):
	# write_metrics(metrics) with new_metric=False replaces the last record,
	# matching InMemoryStore's replace-last behavior.
	store.write_metrics(flush=True)
	store.write_metrics(new_metric=True)
	metrics = store.read_metrics()
	metrics['mode'] = 'Hold'
	store.write_metrics(metrics)
	assert store.read_metrics()['mode'] == 'Hold'
	assert len(store.read_metrics(all=True)) == 1  # replaced, not appended
