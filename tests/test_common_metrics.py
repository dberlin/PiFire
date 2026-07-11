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


def test_metrics_columns_queryable(ds):
	m = c.default_metrics()
	m['mode'] = 'Startup'
	m['primary_setpoint'] = 225
	c.write_metrics(m, new_metric=True)

	conn = datastore.connection()
	row = conn.execute('SELECT mode, primary_setpoint FROM metrics').fetchone()
	assert row == ('Startup', 225)


def test_metrics_roundtrip_all_fields(ds):
	m = c.default_metrics()
	m['id'] = 'distinct-id'
	m['starttime'] = 111.0
	m['starttime_c'] = '00:01:00'
	m['endtime'] = 222.0
	m['endtime_c'] = '00:02:00'
	m['timeinmode'] = 'Active'
	m['mode'] = 'Hold'
	m['augerontime'] = 12.5
	m['augerontime_c'] = '12 s'
	m['estusage_m'] = '5 grams'
	m['estusage_i'] = '0.01 pounds'
	m['fanontime'] = 33.0
	m['fanontime_c'] = '33 s'
	m['smokeplus'] = False
	m['primary_setpoint'] = 250
	m['smart_start_profile'] = 2
	m['startup_temp'] = 75
	m['p_mode'] = 3
	m['auger_cycle_time'] = 8
	m['pellet_level_start'] = 90
	m['pellet_level_end'] = 80
	m['pellet_brand_type'] = 'Generic-Alder'

	c.write_metrics(m, new_metric=True)
	result = c.read_metrics()

	for key, _ in c.metrics_items:
		if key in ('starttime', 'id'):
			continue  # stamped by new_metric=True
		assert result[key] == m[key], key
	assert isinstance(result['smokeplus'], bool)
	assert result['smokeplus'] is False
