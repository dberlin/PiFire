# Web App Latent Bugs — surfaced by the new Playwright suite (2026-07-17)

Adding thorough Playwright coverage of all 17 blueprint pages (branch
`test/web-playwright-coverage`) surfaced **14 pre-existing bugs** in the web app.
None were fixed (the work was test-only); each is characterized by a passing
test that pins current behavior. Triage below, most severe first.

## Serious — correctness / data-loss / crash

1. **probeconfig: editing a virtual/aggregate probe 500s on every call.**
   `blueprints/probeconfig/routes.py` `add_probe`/`edit_probe`: when the edited
   probe is itself virtual (`"VIRT" in port`), the reorder logic walks
   `range(len(probe_info), 0, -1)` and indexes `probe_info[len]` on the first
   iteration → `IndexError` → HTTP 500. Confirmed against the live server.
   Any user editing a virtual probe hits this.

2. **cookfile upload writes OUTSIDE the configured folder.**
   `ulcookfilereq` saves to the literal string `"HISTORY_FOLDER"` instead of the
   `HISTORY_FOLDER` variable → files land in a directory literally named
   `HISTORY_FOLDER` in cwd, not the configured history path. (Test deliberately
   skips exercising this to avoid polluting the repo cwd.)

3. **`create_logger()` guard silently suppresses event logging.**
   `common/common.py` `create_logger()` uses `if not logger.hasHandlers():` —
   but `hasHandlers()` walks ANCESTOR loggers too, so when the root logger is
   already configured (e.g. under pytest, or any embedding context), the
   "events" `FileHandler` never attaches and `write_log`/`write_event` silently
   never reach `./logs/events.log`. Use `if not logger.handlers:` instead.

4. **history CSV export crashes on empty history.**
   `prepare_csv()` unconditionally indexes the history list → `IndexError` when
   history is empty (fresh install, or after a clear). The export route 500s.

5. **update actions run `os.system` with no hardware gate.**
   `change_branch`/`do_update`/`do_upgrade` (`blueprints/update/routes.py`) call
   `os.system("... updater.py ... &")` unconditionally. `is_real_hardware()`
   does NOT protect `update_remote_branches` (and `default_settings()['platform']
   ['real_hw']` is `True`), so these fire on any platform. Also, the update page's
   GET render runs real `git fetch`/`git tag` every load.

6. **`create_recipefile()` silently overwrites on title collision.**
   Unlike `create_cookfile()` (which handles same-title collisions), a recipe
   created in the same clock-minute with the same title silently overwrites the
   prior one with blank defaults → data loss.

7. **Asset upload 500s on a fresh environment.**
   `uploadassets`/`ulmediafn`/`ulthumbfn` assume the `/tmp/pifire` PARENT dir
   already exists; on a truly fresh env the first asset upload 500s.

## Medium / Minor

8. **`dashboard_config` KeyErrors on empty `selected`** (settings route).
9. **`common/backups.py` writes `./backups/manifest.json` via a hardcoded literal
   path**, bypassing the configured `BACKUP_PATH` (backup CONTENT honors the path;
   only the manifest leaks to cwd).
10. **`cookfile_update` silently no-ops on a bare filename** (non-full-path)
    rather than returning an error.
11. **`cookfile_page` has no bare-GET render** — falls through to a JSON error;
    the real cook-detail render actually lives in `blueprints/history/routes.py`.
12. **`manifest` serves `Content-Type: text/cache-manifest`** (deprecated AppCache
    mimetype) instead of a proper web-manifest/JSON type.
13. **settings `auger_rate` input `step="0.05"`** silently blocks form submission
    for off-step values (browser-side validation quirk; a user entering e.g.
    0.03 gets no feedback and no save).
14. **`default_control()` per-pin `manual` dict keys are vestigial** — the live
    code only reads `change`/`output`/`pwm`; the per-pin sub-keys are dead.

## Notes
- All 14 are characterized by passing tests (behavior pinned, not fixed).
- Items 1, 2, 3, 5 are the highest priority (a guaranteed 500, silent
  out-of-folder writes, silently-lost event logs, and unguarded system commands).
- The suite also serves as the characterization net for the planned Phase D
  blueprints-service refactor (see `docs/superpowers/plans/`).
