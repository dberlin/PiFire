import os

import common.backups as backups

# The real repo's ./backups/ directory is gitignored but NOT test-isolated --
# it is a real path relative to the process cwd (repo root under normal test
# runs), and other concurrent work in this repo may have already populated it
# (that pollution is itself live evidence of this bug). So rather than assert
# "does not exist" (it may already exist for unrelated reasons), snapshot its
# content/mtime before the call and assert the fix leaves it untouched.
REPO_MANIFEST = os.path.join(os.getcwd(), "backups", "manifest.json")


def _snapshot(path):
    if not os.path.exists(path):
        return None
    return os.path.getmtime(path), open(path).read()


def test_backup_settings_manifest_honors_backup_path(ds, tmp_path, monkeypatch):
    """backup_settings() must write manifest.json under BACKUP_PATH, not a
    hardcoded "./backups/manifest.json" relative to cwd.
    """
    before = _snapshot(REPO_MANIFEST)
    backup_dir = tmp_path / "custom_backups"
    backup_dir.mkdir()
    monkeypatch.setattr(backups, "BACKUP_PATH", str(backup_dir) + os.sep)

    backups.backup_settings()

    assert (backup_dir / "manifest.json").exists()
    assert _snapshot(REPO_MANIFEST) == before


def test_backup_pellet_db_manifest_honors_backup_path(ds, tmp_path, monkeypatch):
    before = _snapshot(REPO_MANIFEST)
    backup_dir = tmp_path / "custom_backups"
    backup_dir.mkdir()
    monkeypatch.setattr(backups, "BACKUP_PATH", str(backup_dir) + os.sep)

    backups.backup_pellet_db(action="backup")

    assert (backup_dir / "manifest.json").exists()
    assert _snapshot(REPO_MANIFEST) == before
