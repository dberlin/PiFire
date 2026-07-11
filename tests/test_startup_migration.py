import json

import pytest

from common import datastore


@pytest.fixture
def fresh(tmp_path, monkeypatch):
	monkeypatch.setenv('PIFIRE_DB_PATH', str(tmp_path / 't.db'))
	datastore._reset_for_tests(str(tmp_path / 't.db'))
	yield tmp_path
	datastore._reset_for_tests(None)


def test_first_boot_imports_settings(fresh, monkeypatch):
	from common import common as c

	monkeypatch.setattr(c, 'read_settings_file', lambda *a, **k: {'globals': {'units': 'F'}})
	monkeypatch.setattr(c, 'read_pellet_db_file', lambda *a, **k: {'current': {'hopper_level': 100}})
	datastore.init()
	assert json.loads(datastore.get_blob('settings:general'))['globals']['units'] == 'F'
	assert json.loads(datastore.get_blob('pellets:general'))['current']['hopper_level'] == 100


def test_first_boot_idempotent(fresh, monkeypatch):
	from common import common as c

	monkeypatch.setattr(c, 'read_settings_file', lambda *a, **k: {'v': 1})
	monkeypatch.setattr(c, 'read_pellet_db_file', lambda *a, **k: {'v': 1})
	datastore.init()
	datastore.set_blob('settings:general', json.dumps({'v': 999}))  # simulate runtime edit
	datastore.init()  # must NOT re-import
	assert json.loads(datastore.get_blob('settings:general'))['v'] == 999
