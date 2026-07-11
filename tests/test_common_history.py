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
	return json.load(open(os.path.join(os.path.dirname(__file__), 'oracle', 'fixtures', f'{name}.json')))


SAMPLE = {
	'probe_history': {'primary': {'Grill': 225}, 'food': {'P1': 145}, 'aux': {}},
	'primary_setpoint': 225,
	'notify_targets': {'Grill': 0},
}


def test_history_cap_matches_oracle(ds):
	exp = _oracle('history_cap')
	for _ in range(5):
		c.write_history(SAMPLE, maxsizelines=3)
	items = c.read_history()
	assert len(items) == exp['len'] == 3  # capped
	# each reconstructed row carries the expected dict keys
	assert set(items[0]) == {'T', 'P', 'F', 'PSP', 'NT', 'AUX'}
	assert items[0]['P'] == {'Grill': 225}
	assert items[0]['PSP'] == 225


def test_history_ext_data_roundtrip(ds):
	d = dict(SAMPLE, ext_data={'k': 1})
	c.write_history(d, ext_data=True)
	row = c.read_history()[0]
	assert row['EXD'] == {'k': 1}
