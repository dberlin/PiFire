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
