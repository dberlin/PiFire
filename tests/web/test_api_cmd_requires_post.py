"""`GET /api/cmd/reboot` rebooted the machine.

`api_page` handles `action in ["get","set","cmd","sys"]` before it branches on
method, so every cmd was reachable by GET -- no body, no confirmation, no CSRF
token. Any link, prefetch, crawler or `<img src>` pointing at that URL was enough
to power the box off.

NEUTRALIZATION, and why it is shaped this way (see also the module docstring of
tests/web/test_page_admin.py, which documents three real unintended reboots):

  * `common/api_commands.py:31` does `from common.system import reboot_system,
    restart_scripts, shutdown_system`, binding those names into ITS OWN globals.
    Patching `common.system.reboot_system` would therefore miss the call site
    entirely. These tests patch `common.api_commands.<name>`.
  * `reboot_system` dispatches PRIMARILY through
    `subprocess.run(["sudo","systemctl","reboot"])` and only falls back to
    `os.system`, so patching `os.system` alone is not sufficient either.
  * `is_real_hardware()` reads `settings["platform"]["real_hw"]`, which
    `default_settings()` ships as True and tests/conftest.py seeds as False.
    That closes the branch for a test that leaves settings alone -- and reopens
    it for one that does not, since it is a settings value and nothing more.

So: patch the bound names, patch `os.system`, AND assert nothing
reboot/poweroff/shutdown-shaped ever reached `subprocess.run`.
"""

from unittest import mock

import pytest

from common import api_commands

_HAZARD_TOKENS = ("reboot", "poweroff", "shutdown", "supervisor", "systemctl")


@pytest.fixture
def hazard_stubs():
    """Neutralizes every lifecycle call reachable from /api/cmd/*, and records."""
    calls = []

    def _record(name):
        def _inner(*args, **kwargs):
            calls.append((name, args, kwargs))

        return _inner

    def _record_os_system(cmd):
        calls.append(("os.system", cmd))
        return 0

    with (
        mock.patch("os.system", side_effect=_record_os_system),
        mock.patch.object(api_commands, "reboot_system", side_effect=_record("reboot_system")),
        mock.patch.object(api_commands, "shutdown_system", side_effect=_record("shutdown_system")),
        mock.patch.object(api_commands, "restart_scripts", side_effect=_record("restart_scripts")),
        mock.patch("subprocess.run") as m_run,
    ):
        yield {"calls": calls, "subprocess_run": m_run}


def _assert_nothing_hazardous_ran(stubs):
    """Proof the real dispatch body never executed -- not merely that a stub was
    reached first."""
    for call in stubs["subprocess_run"].call_args_list:
        argv = " ".join(str(a) for a in call.args[0]) if call.args else ""
        assert not any(token in argv for token in _HAZARD_TOKENS), argv
    for name, payload in ((c[0], c[1]) for c in stubs["calls"] if c[0] == "os.system"):
        assert not any(token in str(payload) for token in _HAZARD_TOKENS), payload


@pytest.mark.parametrize("cmd", ["reboot", "shutdown", "restart"])
def test_get_cannot_reach_a_lifecycle_command(client, hazard_stubs, cmd):
    """The regression this file exists for. Against the pre-fix code this
    returns 201 and the stub records a call."""
    resp = client.get(f"/api/cmd/{cmd}")
    assert resp.status_code == 405
    assert hazard_stubs["calls"] == []
    _assert_nothing_hazardous_ran(hazard_stubs)


@pytest.mark.parametrize(
    "cmd,expected",
    [("reboot", "reboot_system"), ("shutdown", "shutdown_system"), ("restart", "restart_scripts")],
)
def test_post_still_works(client, hazard_stubs, cmd, expected):
    """The fix must not break the supported form."""
    resp = client.post(f"/api/cmd/{cmd}")
    assert resp.status_code == 201
    assert [c[0] for c in hazard_stubs["calls"]] == [expected]
    _assert_nothing_hazardous_ran(hazard_stubs)


def test_get_still_works_for_read_actions(client, hazard_stubs):
    """Only `cmd` is narrowed. Narrowing `get`/`set`/`sys` is a separate
    decision and deliberately out of scope -- `get` in particular is read-only
    and used by the mobile app over GET."""
    resp = client.get("/api/get/versions")
    assert resp.status_code == 201
    assert hazard_stubs["calls"] == []
