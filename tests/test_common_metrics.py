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


def test_replace_last_matches_oracle(ds):
	exp = _oracle('metrics_replace_last')
	m = c.default_metrics()
	m['mode'] = 'Startup'
	c.write_metrics(m, new_metric=True)
	m2 = c.default_metrics()
	m2['mode'] = 'Hold'
	c.write_metrics(m2, new_metric=False)
	assert c.read_metrics()['mode'] == exp['last']['mode'] == 'Hold'
	assert len(c.read_metrics(all=True)) == exp['all_len'] == 1


def test_new_metric_without_existing_does_not_crash(ds):
	c.write_metrics(new_metric=True)  # regression: no metrics yet
	assert 'starttime' in c.read_metrics()
