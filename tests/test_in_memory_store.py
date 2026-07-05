from common.common import WriteKind
from controller.runtime.store import InMemoryStore


def test_overwrite_replaces_whole_control():
	s = InMemoryStore(control={'mode': 'Stop', 'a': 1})
	s.write_control({'mode': 'Hold'}, WriteKind.OVERWRITE)
	assert s.read_control() == {'mode': 'Hold'}


def test_merge_is_deferred_until_execute():
	s = InMemoryStore(control={'mode': 'Stop', 'nested': {'x': 1, 'y': 2}})
	s.write_control({'nested': {'x': 9}}, WriteKind.MERGE, origin='display')
	# nothing changes until execute
	assert s.read_control()['nested'] == {'x': 1, 'y': 2}
	s.execute_control_writes()
	# deep_update: x replaced, y preserved
	assert s.read_control()['nested'] == {'x': 9, 'y': 2}
	assert s.read_control()['mode'] == 'Stop'


def test_merges_apply_in_fifo_order():
	s = InMemoryStore(control={'v': 0})
	s.write_control({'v': 1}, WriteKind.MERGE)
	s.write_control({'v': 2}, WriteKind.MERGE)
	s.execute_control_writes()
	assert s.read_control()['v'] == 2


def test_display_queue_drain_is_fifo_and_empties():
	s = InMemoryStore()
	s.display_commands().push(('text', 'ERROR'))
	s.display_commands().push(('clear', None))
	assert s.display_commands().drain() == [('text', 'ERROR'), ('clear', None)]
	assert s.display_commands().drain() == []


def test_read_control_returns_a_copy():
	s = InMemoryStore(control={'mode': 'Stop'})
	c = s.read_control()
	c['mode'] = 'Hold'
	assert s.read_control()['mode'] == 'Stop'


def test_write_history_accepts_maxsizelines():
	s = InMemoryStore()
	s.write_history({'x': 1}, maxsizelines=100, ext_data=True)
	assert s.read_history() == [{'x': 1}]


def test_write_metrics_positional_flush_matches_common_order():
	s = InMemoryStore(metrics={'a': 1})
	# positional 2nd arg is flush (matching common.common order)
	s.write_metrics(None, True)
	assert s.read_metrics(all=True) == []
