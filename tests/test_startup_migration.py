import json
import os
import sqlite3
import subprocess
import sys

import pytest

from common import datastore

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def test_export_import_roundtrip(fresh):
	datastore.init()
	datastore.set_blob('settings:general', json.dumps({'globals': {'units': 'C'}}))
	p = str(fresh / 'out.json')
	datastore.export_config('settings:general', p)
	assert json.load(open(p))['globals']['units'] == 'C'
	# edit the file, re-import
	d = json.load(open(p))
	d['globals']['units'] = 'F'
	json.dump(d, open(p, 'w'))
	datastore.import_config('settings:general', p)
	assert json.loads(datastore.get_blob('settings:general'))['globals']['units'] == 'F'


def test_import_rejects_malformed(fresh):
	datastore.init()
	p = str(fresh / 'bad.json')
	open(p, 'w').write('{not json')
	with pytest.raises(ValueError):
		datastore.import_config('settings:general', p)


def test_boot_path_import_reaches_public_readers(fresh, monkeypatch):
	"""FIX 1: the import must be visible through the same public accessors
	(read_settings()/read_pellet_db()) that every caller in the codebase
	actually uses -- not just the raw kv blob."""
	from common import common as c

	monkeypatch.setattr(c, 'read_settings_file', lambda *a, **k: {'globals': {'units': 'F', 'grill_name': 'sentinel'}})
	monkeypatch.setattr(c, 'read_pellet_db_file', lambda *a, **k: {'current': {'hopper_level': 100}})
	datastore.init()
	assert c.read_settings()['globals']['grill_name'] == 'sentinel'
	assert c.read_pellet_db()['current']['hopper_level'] == 100


def test_first_boot_import_calls_read_settings_file_with_init_true(fresh, monkeypatch):
	"""FIX 2: _first_boot_import must import settings through the init=True
	overlay/upgrade path, not the raw file reader -- otherwise imported
	settings never gain new default fields or get upgrade_settings() applied."""
	from common import common as c

	seen_kwargs = {}

	def fake_read_settings_file(*a, **k):
		seen_kwargs.update(k)
		return {'globals': {'units': 'F'}}

	monkeypatch.setattr(c, 'read_settings_file', fake_read_settings_file)
	monkeypatch.setattr(c, 'read_pellet_db_file', lambda *a, **k: {})
	datastore.init()
	assert seen_kwargs.get('init') is True


def test_read_settings_file_init_overlay_upgrades_stale_settings(tmp_path):
	"""FIX 2 (unit-level): read_settings_file(init=True) must overlay new
	default fields onto an on-disk settings dict and bump its stored version,
	while preserving the user's existing values -- this is exactly what
	_first_boot_import now relies on to migrate an old settings.json."""
	from common.common import default_settings, read_settings_file

	stale = default_settings()
	stale['globals']['grill_name'] = 'UNIT_TEST_SENTINEL'
	del stale['globals']['uv']  # simulate a field that didn't exist in an older settings.json
	stale['versions']['build'] = 1  # simulate an old install (lower build number)

	p = tmp_path / 'old_settings.json'
	p.write_text(json.dumps(stale))

	upgraded = read_settings_file(filename=str(p), init=True)

	assert upgraded['globals']['grill_name'] == 'UNIT_TEST_SENTINEL'  # user data preserved
	assert 'uv' in upgraded['globals']  # new default field backfilled by the overlay
	assert upgraded['versions']['build'] == default_settings()['versions']['build']  # version bumped


def test_boot_path_import_via_app_import(tmp_path):
	"""End-to-end proof that the actual production boot path (importing the
	real app.py) performs the first-boot SQLite import -- without any test
	scaffolding calling datastore.init() itself. This is exactly the gap
	that shipped uncaught: every prior init() call anywhere in the codebase
	was in a test, never product code, so the real settings.json/pelletdb.json
	of an upgrading user was never imported and read_settings() silently
	returned defaults (data loss).

	Runs `import app` in a subprocess (a fresh interpreter, cwd = repo root
	so relative lookups like updater/updater_manifest.json still resolve)
	against a fresh, isolated SQLite DB. settings.json/pelletdb.json in the
	repo root are temporarily swapped for sentinel content and restored
	afterward (they're gitignored, locally-generated files, never committed).
	"""
	settings_path = os.path.join(_REPO_ROOT, 'settings.json')
	pelletdb_path = os.path.join(_REPO_ROOT, 'pelletdb.json')
	orig_settings = open(settings_path).read() if os.path.exists(settings_path) else None
	orig_pelletdb = open(pelletdb_path).read() if os.path.exists(pelletdb_path) else None

	from common.common import default_pellets, default_settings

	sentinel_settings = default_settings()
	sentinel_settings['globals']['grill_name'] = 'BOOT_PATH_SENTINEL_GRILL'
	del sentinel_settings['globals']['uv']  # a "new" field the import must backfill
	sentinel_pelletdb = default_pellets()
	sentinel_pelletdb['current']['hopper_level'] = 37

	db_path = str(tmp_path / 'boot_app.db')

	try:
		with open(settings_path, 'w') as fh:
			json.dump(sentinel_settings, fh)
		with open(pelletdb_path, 'w') as fh:
			json.dump(sentinel_pelletdb, fh)

		env = dict(os.environ)
		env['PIFIRE_DB_PATH'] = db_path
		# Intentional real-process integration test: a fresh interpreter is required to
		# exercise app.py's actual module-level boot wiring (see docstring above).
		proc = subprocess.run(
			[sys.executable, '-c', 'import app'], cwd=_REPO_ROOT, env=env, capture_output=True, text=True, timeout=60
		)
		assert proc.returncode == 0, f'importing app.py failed:\nstdout={proc.stdout}\nstderr={proc.stderr}'
	finally:
		if orig_settings is not None:
			with open(settings_path, 'w') as fh:
				fh.write(orig_settings)
		if orig_pelletdb is not None:
			with open(pelletdb_path, 'w') as fh:
				fh.write(orig_pelletdb)

	# Read the subprocess's isolated DB directly (it is not the DB this test
	# process's `datastore` module is bound to).
	conn = sqlite3.connect(db_path)
	try:
		row = conn.execute("SELECT value FROM kv WHERE key='settings:general'").fetchone()
		assert row is not None, 'app.py import did not populate settings:general -- datastore.init() not wired at boot'
		imported_settings = json.loads(row[0])
		assert imported_settings['globals']['grill_name'] == 'BOOT_PATH_SENTINEL_GRILL'  # FIX 1: real file data
		assert 'uv' in imported_settings['globals']  # FIX 2: init=True overlay backfilled it

		row = conn.execute("SELECT value FROM kv WHERE key='pellets:general'").fetchone()
		assert row is not None, 'app.py import did not populate pellets:general'
		imported_pelletdb = json.loads(row[0])
		assert imported_pelletdb['current']['hopper_level'] == 37
	finally:
		conn.close()


def test_control_main_calls_datastore_init_before_first_settings_read():
	"""control.py's __main__ block cannot be exercised directly in a unit
	test (it goes on to initialize real hardware and enter the control
	loop's infinite run()), so this is a source-order regression guard: the
	first-boot import must be wired in and must run before the first
	settings/control read, exactly like app.py's module-level call."""
	with open(os.path.join(_REPO_ROOT, 'control.py')) as fh:
		src = fh.read()
	main_block = src[src.index("if __name__ == '__main__':") :]
	# Search for the actual call (`datastore.init()` as a statement), not just
	# any mention -- e.g. an explanatory comment referencing "datastore.init()"
	# or "read_settings(" would otherwise produce a false pass/fail.
	init_pos = main_block.index('\tdatastore.init()')
	first_read_pos = main_block.index('settings = read_settings(')
	assert init_pos < first_read_pos, 'datastore.init() must run before the first settings read in control.py'


@pytest.fixture
def backups_dir():
	"""backup_settings()/restore_settings() use hardcoded relative paths
	('./backups/...') rather than an injectable path, so this fixture makes
	the real repo-root './backups/' directory usable for a test and restores
	it to its prior state afterward (it's gitignored -- a local scratch dir
	-- but other concurrent processes may be using it, so don't clobber it)."""
	backups_path = os.path.join(_REPO_ROOT, 'backups')
	manifest_path = os.path.join(backups_path, 'manifest.json')
	dir_existed = os.path.isdir(backups_path)
	if not dir_existed:
		os.mkdir(backups_path)
	manifest_existed = os.path.exists(manifest_path)
	manifest_orig = open(manifest_path).read() if manifest_existed else None
	pre_existing = set(os.listdir(backups_path))

	yield backups_path

	for name in set(os.listdir(backups_path)) - pre_existing:
		os.remove(os.path.join(backups_path, name))
	if manifest_existed:
		with open(manifest_path, 'w') as fh:
			fh.write(manifest_orig)
	if not dir_existed:
		try:
			os.rmdir(backups_path)
		except OSError:
			pass


def test_backup_restore_settings_round_trip(fresh, backups_dir):
	"""FIX 3: backup_settings() must write the CURRENT SQLite settings out to
	the backup file (not a stale settings.json copy); restore_settings()
	must read that backup FILE back and make it the new current SQLite
	state (not silently re-read whatever is already current)."""
	from common import common as c

	datastore.init()
	settings = c.default_settings()
	settings['globals']['grill_name'] = 'BACKUP_ROUND_TRIP_SENTINEL'
	c.write_settings_store(settings)

	backup_file = c.backup_settings()
	assert os.path.exists(backup_file)
	with open(backup_file) as fh:
		backed_up = json.load(fh)
	# Backup direction: captured the CURRENT SQLite state.
	assert backed_up['globals']['grill_name'] == 'BACKUP_ROUND_TRIP_SENTINEL'

	# Blow away the current SQLite state to prove restore reads the file,
	# not whatever happens to already be current.
	c.write_settings_store(c.default_settings())
	assert c.read_settings()['globals']['grill_name'] == ''

	restored = c.restore_settings(c.default_settings())
	assert restored['globals']['grill_name'] == 'BACKUP_ROUND_TRIP_SENTINEL'
	# Restore direction: made the recovered settings the new current state.
	assert c.read_settings()['globals']['grill_name'] == 'BACKUP_ROUND_TRIP_SENTINEL'


def test_read_pellet_db_file_corrupt_backup_does_not_recurse_infinitely(fresh, backups_dir):
	"""FIX: read_pellet_db_file's self-repair path (corrupt/missing
	pelletdb.json -> backup_pellet_db(action='restore') -> read_pellet_db_file
	against the backup file) must not recurse without bound if the recorded
	backup is ALSO corrupt -- unlike read_settings_file (which guards its own
	analogous retry with retry_count<5), read_pellet_db_file previously had no
	guard at all, so a corrupt primary file + a corrupt backup file would
	recurse forever (RecursionError) instead of falling back to defaults."""
	from common import common as c

	# Record a corrupt file as the current pellet DB backup.
	backup_file = os.path.join(backups_dir, 'PelletDB_corrupt.json')
	with open(backup_file, 'w') as fh:
		fh.write('{not valid json')
	manifest_path = os.path.join(backups_dir, 'manifest.json')
	with open(manifest_path, 'w') as fh:
		json.dump({'server_settings': {}, 'pelletdb': {'current': backup_file}}, fh)

	# Corrupt primary pelletdb.json triggers the self-repair path.
	primary_file = str(fresh / 'pelletdb.json')
	with open(primary_file, 'w') as fh:
		fh.write('{also not valid json')

	# The call must return (not hang/RecursionError) and fall back to
	# defaults. default_pellets() embeds a timestamp-based pelletid, so
	# compare structurally on the stable default fields rather than exact
	# equality against a freshly generated default_pellets().
	result = c.read_pellet_db_file(filename=primary_file)
	assert result['current']['hopper_level'] == 100
	assert result['woods'] == c.default_pellets()['woods']
	assert result['current']['est_usage'] == 0


def test_backup_restore_pellet_db_round_trip(fresh, backups_dir):
	"""FIX 3: backup_pellet_db('backup') must write the CURRENT SQLite pellet
	DB out to the backup file (not a stale pelletdb.json copy);
	backup_pellet_db('restore') must read that backup FILE back."""
	from common import common as c

	datastore.init()
	pelletdb = c.default_pellets()
	pelletdb['current']['hopper_level'] = 13
	c.write_pellets_store(pelletdb)

	backup_file = c.backup_pellet_db(action='backup')
	assert os.path.exists(backup_file)
	with open(backup_file) as fh:
		backed_up = json.load(fh)
	assert backed_up['current']['hopper_level'] == 13

	c.write_pellets_store(c.default_pellets())
	assert c.read_pellet_db()['current']['hopper_level'] != 13

	restored = c.backup_pellet_db(action='restore')
	assert restored['current']['hopper_level'] == 13
	assert c.read_pellet_db()['current']['hopper_level'] == 13
