import json
import os

import pytest

from common import common as c
from common import datastore


@pytest.fixture
def ds(tmp_path):
	datastore._reset_for_tests(str(tmp_path / 't.db'))
	datastore.init()
	yield datastore
	datastore._reset_for_tests(None)


def _oracle(name):
	p = os.path.join(os.path.dirname(__file__), 'oracle', 'fixtures', f'{name}.json')
	return json.load(open(p))


def test_control_overwrite_and_read(ds):
	c.write_control({'mode': 'Stop', 'n': {'a': 1}}, c.WriteKind.OVERWRITE, origin='t')
	assert c.read_control() == {'mode': 'Stop', 'n': {'a': 1}}


def test_control_merge_matches_oracle(ds):
	exp = _oracle('control_merge')
	c.write_control({'mode': 'Stop', 'nested': {'a': 1, 'b': 2}}, c.WriteKind.OVERWRITE, origin='test')
	c.write_control({'nested': {'b': 9, 'c': 3}}, c.WriteKind.MERGE, origin='webapp')
	assert c.read_control() == exp['before_execute']  # MERGE deferred
	c.execute_control_writes()
	assert c.read_control() == exp['after_execute']  # deep-merge, origin stripped


def test_errors_and_current_status_roundtrip(ds):
	c.write_errors(['e1'])
	assert c.read_errors() == ['e1']
	c.write_status({'mode': 'Hold'})
	assert c.read_status() == {'mode': 'Hold'}


def test_autotune_uses_queue(ds):
	c.read_autotune(flush=True)
	c.write_autotune({'tr': 1})
	c.write_autotune({'tr': 2})
	assert c.read_autotune() == [{'tr': 1}, {'tr': 2}]
	assert c.read_autotune(size_only=True) == 2
	c.read_autotune(flush=True)
	assert c.read_autotune() == []


def test_warnings_read_and_clear_matches_oracle(ds):
	exp = _oracle('warnings')
	c.write_warning('first')
	c.write_warning('second')
	assert c.read_warnings() == exp['read1']
	assert c.read_warnings() == exp['read2_after_clear']


def test_connected_users_add_remove(ds):
	assert c.read_connected_users() == []
	c.write_connected_user('sidA')
	c.write_connected_user('sidB')
	assert sorted(c.read_connected_users()) == ['sidA', 'sidB']
	c.remove_connected_user('sidA')
	assert c.read_connected_users() == ['sidB']
	c.read_connected_users(flush=True)
	assert c.read_connected_users() == []
