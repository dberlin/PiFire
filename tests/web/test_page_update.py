"""Playwright coverage for the system-updater page
(blueprints/update/routes.py's `update_page`).

Follows the pattern established in test_page_settings.py; see
tests/web/conftest.py for the shared harness.

*** SAFETY-CRITICAL MODULE -- READ BEFORE EDITING ***

This route (and the `updater.py` functions it calls) shells out to real
`git`/`os.system` for nearly everything, including the **plain GET
render**:

- `update_page` GET (no action) calls `get_update_data()`, which calls
  `updater.get_remote_version()`, which unconditionally runs
  `git fetch --tags` and `git tag ...` via `subprocess.run` against
  *this actual checkout* -- not gated by any hardware/test flag. Every
  other `get_update_data()` sub-call (`get_current_tag`, `get_branch`,
  `get_available_branches`, `get_remote_url`) is also a real `git`
  `subprocess.run` call.
- `action=check` (GET) calls `get_available_updates()`, another real
  `git fetch` + `git rev-list`.
- `update_remote_branches` (POST) calls `os.system(f"{python_exec}
  updater.py -r &")` -- but ONLY gated behind `is_real_hardware()`.
  **`is_real_hardware()` reads `settings['platform']['real_hw']`, which
  `default_settings()` ships as `True`** (see common/defaults.py) --
  i.e. in this harness's seeded settings, that gate does NOT protect
  this action; it would fire for real if `os.system` weren't patched.
- `change_branch`, `do_update`, `do_upgrade` (all POST) call
  `os.system(...)` to kick off `updater.py -b/-u/-i ...` **completely
  unconditionally** (no `is_real_hardware()` check at all) whenever the
  branch differs / control mode is "Stop". These would perform a REAL
  branch checkout / code update / package upgrade against this actual
  repo checkout if not intercepted.
- `show_log` (POST) calls `get_log()`, a real `git log` `subprocess.run`.

Given all of the above, EVERY test in this module runs under the
`no_real_subprocess` autouse fixture below, which monkeypatches
`subprocess.run` (used by updater.py) and `os.system` (used directly by
this route) to recording fakes -- for the GET-render path too, not just
the POST actions the task brief called out. Nothing in this module ever
spawns a real subprocess or hits the network. Each fake git subprocess.run
call returns a small deterministic stdout so the route's parsing logic
(which branch is current, how many commits behind, etc.) has something
sane to work with, instead of leaving it to interpret empty output.

The patches are applied via `monkeypatch.setattr` on the `subprocess` and
`os` *modules themselves* (not on `blueprints.update.routes`'s or
`updater.py`'s already-bound names) -- so the same patched callable is
observed regardless of which module's `subprocess.run(...)` / `os.system(
...)` call site executes it, including inside `live_server`'s background
thread (see conftest.py's "thread-shared datastore" docs for why that
sharing works: same process, same imported module objects). Verified
working below: `test_update_page_renders_key_sections` asserts
`subprocess.run` was actually invoked (proving interception, not just
absence of a crash), and every POST-action test asserts the exact
recorded argv, then confirms nothing escaped by checking
`subprocess_calls`/`os_system_calls` after each request.

Actions covered: base GET render, `updatestatus`, `post-message`, `check`,
`update_remote_branches`, `change_branch` (same-branch no-op and
different-branch), `do_update` (stopped and active-system-blocked),
`do_upgrade` (stopped), `show_log`.
"""

from types import SimpleNamespace

import pytest

from tests.web.conftest import apply_control, requires_chromium

pytestmark = requires_chromium


def _fake_git_stdout(command):
    """Deterministic canned stdout per git subcommand, keyed on argv, so
    updater.py's parsing logic (branch name, commit counts, tags) has
    something sane to chew on instead of empty strings."""
    if command[:2] == ["git", "branch"]:
        return "* main\n  remotes/origin/main\n  remotes/origin/other-branch\n"
    if command[:2] == ["git", "config"]:
        return "https://github.com/example/pifire.git\n"
    if command[:3] == ["git", "rev-list", "--left-only"]:
        return "0\n"
    if command[:2] == ["git", "tag"]:
        return "v1.0.0\nv1.1.0\n"
    if command[:2] == ["git", "describe"]:
        return "v1.1.0-0-gabcdef0\n"
    if command[:2] == ["git", "log"]:
        return 'abc123 - 2 days ago : "Test commit message"\n'
    return ""  # e.g. "git fetch" / "git fetch --tags" -- no output needed


@pytest.fixture(autouse=True)
def no_real_subprocess(monkeypatch):
    """See module docstring. Intercepts every `subprocess.run` (updater.py)
    and `os.system` (this route) call for the whole module, recording what
    would have run without ever actually running it."""
    import os
    import subprocess

    subprocess_calls = []
    os_system_calls = []

    def fake_run(command, *args, **kwargs):
        subprocess_calls.append(command)
        return SimpleNamespace(returncode=0, stdout=_fake_git_stdout(command), stderr="")

    def fake_system(command):
        os_system_calls.append(command)
        return 0

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os, "system", fake_system)

    return SimpleNamespace(subprocess_calls=subprocess_calls, os_system_calls=os_system_calls)


def test_update_page_renders_key_sections(live_server, page, no_real_subprocess):
    resp = page.goto(f"{live_server}/update/")

    assert resp.status == 200
    assert page.title().startswith("Updater")
    assert page.locator("#branch_target").count() == 1
    # Two do_update buttons ship on this page: the "Update available"
    # modal's confirm button, and the Advanced card's "Force Update".
    assert page.locator("button[name='do_update']").count() == 2
    assert page.locator("button[name='do_upgrade']").count() == 1
    assert page.locator("button[name='update_remote_branches']").count() == 1

    # Proves interception, not just an absent crash: get_update_data()'s
    # chain (get_current_tag/get_branch/get_available_branches/
    # get_remote_url/get_remote_version) makes several real subprocess.run
    # calls in the unpatched code -- all recorded here instead of executed.
    assert len(no_real_subprocess.subprocess_calls) >= 5
    assert ["git", "branch", "-a"] in no_real_subprocess.subprocess_calls
    assert not no_real_subprocess.os_system_calls

    # Branch info from our canned `git branch -a` output renders through.
    assert "main" in page.content()


def test_updatestatus_action(live_server, page, no_real_subprocess):
    from common.datastore_accessors import set_updater_install_status

    set_updater_install_status(42, "Updating...", "some output")

    resp = page.request.get(f"{live_server}/update/updatestatus")

    assert resp.status == 200
    assert resp.json() == {"percent": 42, "status": "Updating...", "output": "some output"}
    assert not no_real_subprocess.os_system_calls


def test_post_message_action(live_server, page, no_real_subprocess):
    resp = page.request.get(f"{live_server}/update/post-message")

    assert resp.status == 200
    assert len(resp.text()) > 0
    assert not no_real_subprocess.os_system_calls


def test_check_action_reports_commits_behind(live_server, page, no_real_subprocess):
    resp = page.request.get(f"{live_server}/update/check")

    assert resp.status == 200
    body = resp.json()
    assert body["result"] == "success"
    assert body["behind"] == 0  # from our canned "git rev-list" stdout of "0"
    assert not no_real_subprocess.os_system_calls


def test_update_remote_branches_via_direct_post(live_server, page, no_real_subprocess):
    """default_settings() ships platform.real_hw = True (see module
    docstring), so is_real_hardware() is True here -- this action WOULD
    call a real os.system(...) without the no_real_subprocess patch."""
    resp = page.request.post(f"{live_server}/update/", form={"update_remote_branches": "true"}, max_redirects=0)

    assert resp.status == 302
    assert resp.headers["location"] == "/update"
    assert len(no_real_subprocess.os_system_calls) == 1
    assert "updater.py -r &" in no_real_subprocess.os_system_calls[0]


def test_change_branch_same_branch_is_a_noop(live_server, page, no_real_subprocess):
    # Canned `git branch -a` marks "main" current, so posting "main" back
    # should hit the "already set to" alert branch, no os.system call.
    resp = page.request.post(f"{live_server}/update/", form={"change_branch": "true", "branch_target": "main"})

    assert resp.status == 200
    assert "already set to" in resp.text()
    assert not no_real_subprocess.os_system_calls


def test_change_branch_different_branch_via_direct_post(live_server, page, no_real_subprocess):
    resp = page.request.post(f"{live_server}/update/", form={"change_branch": "true", "branch_target": "other-branch"})

    assert resp.status == 200
    assert len(no_real_subprocess.os_system_calls) == 1
    assert "updater.py -b other-branch &" in no_real_subprocess.os_system_calls[0]


def test_do_update_when_stopped_via_direct_post(live_server, page, no_real_subprocess):
    apply_control(lambda c: c.__setitem__("mode", "Stop"))

    resp = page.request.post(f"{live_server}/update/", form={"do_update": "true"})

    assert resp.status == 200
    assert len(no_real_subprocess.os_system_calls) == 1
    assert "updater.py -u main -p &" in no_real_subprocess.os_system_calls[0]


def test_do_update_blocked_when_system_active(live_server, page, no_real_subprocess):
    apply_control(lambda c: c.__setitem__("mode", "Startup"))

    resp = page.request.post(f"{live_server}/update/", form={"do_update": "true"})

    assert resp.status == 200
    assert "cannot be completed when the system is active" in resp.text()
    assert not no_real_subprocess.os_system_calls


def test_do_upgrade_when_stopped_via_direct_post(live_server, page, no_real_subprocess):
    apply_control(lambda c: c.__setitem__("mode", "Stop"))

    resp = page.request.post(f"{live_server}/update/", form={"do_upgrade": "true"})

    assert resp.status == 200
    assert len(no_real_subprocess.os_system_calls) == 1
    assert "updater.py -i &" in no_real_subprocess.os_system_calls[0]


def test_show_log_action_via_direct_post(live_server, page, no_real_subprocess):
    resp = page.request.post(f"{live_server}/update/", form={"show_log": "5"})

    assert resp.status == 200
    assert "Test commit message" in resp.text()
    assert not no_real_subprocess.os_system_calls
    assert ["git", "log", "origin/main", "-5", '--pretty="%h - %cr : %s"'] in no_real_subprocess.subprocess_calls
