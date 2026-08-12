import json
import subprocess

import pytest
from common.common import WriteKind
from common.modes import Mode


def _stub_reads(monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(
        ur,
        "get_update_data",
        lambda settings: {
            "version": "v1.8.0 (v1.8.0)",
            "branch_target": "main",
            "branches": ["main", "dev", "prototype"],
            "remote_url": "https://github.com/nebhead/PiFire",
            "remote_version": "v1.8.1",
        },
    )
    return ur


def test_state_returns_the_update_data_shape(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    _stub_reads(monkeypatch)
    monkeypatch.setattr(ur, "web_ui_needs_rebuild", lambda root: False)
    monkeypatch.setattr(ur, "last_build_failed", lambda: False)
    monkeypatch.setattr(ur, "detached_head", lambda: None)
    body = client.get("/api/update/state").get_json()
    assert body["result"] == "OK"
    assert body["data"] == {
        "version": "v1.8.0 (v1.8.0)",
        "branch": "main",
        "detached": None,
        "branches": ["main", "dev", "prototype"],
        "remote_url": "https://github.com/nebhead/PiFire",
        "remote_version": "v1.8.1",
        "web_ui_stale": False,
        "web_ui_build_failed": False,
    }


def test_state_reports_a_bundle_older_than_its_sources(ds, client, monkeypatch):
    """Drives the updater page's Rebuild Web UI prompt: web-react/dist is a
    build artifact, so a pull can leave the served interface behind."""
    import blueprints.api_update.routes as ur

    _stub_reads(monkeypatch)
    monkeypatch.setattr(ur, "web_ui_needs_rebuild", lambda root: True)
    monkeypatch.setattr(ur, "last_build_failed", lambda: False)
    assert client.get("/api/update/state").get_json()["data"]["web_ui_stale"] is True


def test_state_reports_a_failed_rebuild_independently_of_staleness(ds, client, monkeypatch):
    """The two are not the same question. A forced rebuild of an already-current
    bundle can fail and leave nothing stale, and only the failure should put the
    build log on offer."""
    import blueprints.api_update.routes as ur

    _stub_reads(monkeypatch)
    monkeypatch.setattr(ur, "web_ui_needs_rebuild", lambda root: False)
    monkeypatch.setattr(ur, "last_build_failed", lambda: True)
    data = client.get("/api/update/state").get_json()["data"]
    assert data["web_ui_build_failed"] is True
    assert data["web_ui_stale"] is False


def test_check_reports_commits_behind(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_available_updates", lambda: {"success": True, "commits_behind": 3})
    body = client.get("/api/update/check").get_json()
    assert body["result"] == "OK"
    assert body["data"]["behind"] == 3
    assert isinstance(body["data"]["current"], str)


def test_check_maps_a_failed_fetch_to_an_error_envelope(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_available_updates", lambda: {"success": False, "message": "ERROR Getting Remote"})
    resp = client.get("/api/update/check")
    assert resp.status_code == 502
    assert resp.get_json()["result"] == "Error"
    assert "ERROR" in resp.get_json()["message"]


def test_log_defaults_to_ten_and_returns_output(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    seen = {}
    monkeypatch.setattr(ur, "get_log", lambda num_commits: (seen.setdefault("n", num_commits), ("abc123 msg", ""))[1])
    body = client.get("/api/update/log").get_json()
    assert seen["n"] == 10
    assert body["data"]["output"] == "abc123 msg"


def test_log_rejects_a_non_numeric_commit_count(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_log", lambda num_commits: ("", ""))
    resp = client.get("/api/update/log?commits=abc")
    assert resp.status_code == 400


def test_status_passes_through_the_install_status_triplet(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_updater_install_status", lambda: (42, "Working...", "line"))
    body = client.get("/api/update/status").get_json()
    assert body["data"] == {"percent": 42, "status": "Working...", "output": "line"}


def test_status_preserves_idle_null_triplet(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_updater_install_status", lambda: (None, None, None))

    resp = client.get("/api/update/status")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "data": {"percent": None, "status": None, "output": None},
        "result": "OK",
        "message": None,
    }


def _set_mode(mode):
    from common.datastore_accessors import read_control, write_control

    ctrl = read_control()
    ctrl["mode"] = mode
    write_control(ctrl, WriteKind.OVERWRITE, origin="test")


def _set_real_hw(value):
    from common.datastore_accessors import read_settings, write_settings_store

    s = read_settings()
    s["platform"]["real_hw"] = value
    write_settings_store(s)


def _neutralize(monkeypatch, exit_code=0):
    """Replace the one seam that shells out. The stub returns an exit status
    because the routes now read it: os.system answers 0 when the shell accepted
    the line, and the routes report a launch that never happened otherwise."""
    import blueprints.api_update.routes as ur

    fired = []

    def system(cmd):
        fired.append(cmd)
        return exit_code

    monkeypatch.setattr(ur.os, "system", system)
    return ur, fired


def test_refresh_fires_the_r_flag_on_real_hardware(ds, client, monkeypatch):
    _, fired = _neutralize(monkeypatch)
    _set_real_hw(True)
    resp = client.post("/api/update/branches/refresh")
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"started": True}
    assert len(fired) == 1 and fired[0].endswith("updater.py -r &")


def test_mutations_do_not_fire_off_real_hardware(ds, client, monkeypatch):
    _, fired = _neutralize(monkeypatch)
    _set_real_hw(False)
    resp = client.post("/api/update/branches/refresh")
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"started": False}  # gated: nothing fired off a Pi
    assert fired == []


def test_change_branch_validates_against_the_branch_list(ds, client, monkeypatch):
    ur, fired = _neutralize(monkeypatch)
    monkeypatch.setattr(ur, "get_update_data", lambda settings: {"branches": ["main", "dev"]})
    _set_real_hw(True)
    ok = client.post("/api/update/branch", data=json.dumps({"target": "dev"}), content_type="application/json")
    assert ok.status_code == 200
    assert len(fired) == 1 and fired[0].endswith("updater.py -b dev &")
    bad = client.post(
        "/api/update/branch", data=json.dumps({"target": "evil; rm -rf"}), content_type="application/json"
    )
    assert bad.status_code == 400
    assert len(fired) == 1  # rejected target never fired


@pytest.mark.parametrize(
    "body",
    ['{"target":"dev","extra":true}', "null", "[]", "{"],
)
def test_change_branch_rejects_invalid_json_before_update_discovery(
    ds,
    client,
    monkeypatch,
    body,
):
    ur, fired = _neutralize(monkeypatch)
    update_data_calls = []

    def discover(settings):
        update_data_calls.append(settings)
        return {"branches": ["main", "dev"]}

    monkeypatch.setattr(ur, "get_update_data", discover)
    _set_real_hw(True)

    resp = client.post(
        "/api/update/branch",
        data=body,
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"data": None, "result": "Error", "message": "bad_request"}
    assert update_data_calls == []
    assert fired == []


@pytest.mark.parametrize(
    "path",
    ["/branches/refresh", "/pull", "/rebuild-web-ui", "/rebuild-acados", "/upgrade"],
)
@pytest.mark.parametrize("body", ["null", "{"])
def test_mutations_reject_present_invalid_json_before_side_effects(
    ds,
    client,
    monkeypatch,
    path,
    body,
):
    ur, fired = _neutralize(monkeypatch)
    status_updates = []
    monkeypatch.setattr(ur, "set_updater_install_status", lambda *args: status_updates.append(args))
    _set_real_hw(True)
    _set_mode(Mode.STOP)

    resp = client.post(
        f"/api/update{path}",
        data=body,
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert status_updates == []
    assert fired == []


def test_pull_is_blocked_unless_stopped(ds, client, monkeypatch):
    ur, fired = _neutralize(monkeypatch)
    monkeypatch.setattr(ur, "get_branch", lambda: ("main", ""))
    _set_real_hw(True)
    _set_mode("Hold")
    blocked = client.post("/api/update/pull")
    assert blocked.status_code == 409
    assert fired == []
    _set_mode(Mode.STOP)
    ok = client.post("/api/update/pull")
    assert ok.status_code == 200
    assert len(fired) == 1 and fired[0].endswith("updater.py -u main -p &")


def test_upgrade_is_blocked_unless_stopped(ds, client, monkeypatch):
    _, fired = _neutralize(monkeypatch)
    _set_real_hw(True)
    _set_mode("Hold")
    assert client.post("/api/update/upgrade").status_code == 409
    assert fired == []
    _set_mode(Mode.STOP)
    assert client.post("/api/update/upgrade").status_code == 200
    assert len(fired) == 1 and fired[0].endswith("updater.py -i &")


def test_rebuild_web_ui_fires_the_w_flag_on_real_hardware(ds, client, monkeypatch):
    _, fired = _neutralize(monkeypatch)
    _set_real_hw(True)

    resp = client.post("/api/update/rebuild-web-ui")

    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"started": True}
    assert len(fired) == 1 and fired[0].endswith("updater.py -w &")


def test_rebuild_web_ui_does_not_shell_out_off_real_hardware(ds, client, monkeypatch):
    _, fired = _neutralize(monkeypatch)
    _set_real_hw(False)

    resp = client.post("/api/update/rebuild-web-ui")

    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"started": False}
    assert fired == []


def test_rebuild_web_ui_is_allowed_while_the_grill_runs(ds, client, monkeypatch):
    """Unlike /pull and /upgrade: this writes only web-react/dist and changes
    no code the control process is running, and refusing it would strand a
    grill mid-cook on a stale interface."""
    _, fired = _neutralize(monkeypatch)
    _set_real_hw(True)
    _set_mode(Mode.HOLD)

    resp = client.post("/api/update/rebuild-web-ui")

    assert resp.status_code == 200
    assert len(fired) == 1


def test_rebuild_acados_is_blocked_unless_stopped(ds, client, monkeypatch):
    _, fired = _neutralize(monkeypatch)
    _set_real_hw(True)
    _set_mode(Mode.HOLD)

    blocked = client.post("/api/update/rebuild-acados")

    assert blocked.status_code == 409
    assert fired == []

    _set_mode(Mode.STOP)
    started = client.post("/api/update/rebuild-acados")

    assert started.status_code == 200
    assert started.get_json()["data"] == {"started": True}
    assert len(fired) == 1 and fired[0].endswith("updater.py --rebuild-acados &")


def test_buildlog_serves_the_run_from_its_start_and_reports_the_next_offset(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "read_build_log", lambda offset: ("+ building\n", 11, True))

    body = client.get("/api/update/buildlog").get_json()

    assert body["result"] == "OK"
    assert body["data"] == {"text": "+ building\n", "offset": 11, "reset": True}


def test_buildlog_passes_the_offset_through(ds, client, monkeypatch):
    """The panel polls once a second for as long as it is open; re-sending the
    whole transcript each tick is what the offset exists to avoid."""
    import blueprints.api_update.routes as ur

    seen = []

    def read(offset):
        seen.append(offset)
        return ("", offset, False)

    monkeypatch.setattr(ur, "read_build_log", read)
    client.get("/api/update/buildlog?offset=512")

    assert seen == [512]


def test_buildlog_treats_a_junk_offset_as_the_start_of_the_run(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    seen = []

    def read(offset):
        seen.append(offset)
        return ("", 0, True)

    monkeypatch.setattr(ur, "read_build_log", read)
    client.get("/api/update/buildlog?offset=nonsense")

    assert seen == [0]


def test_buildlog_download_returns_the_transcript_as_an_attachment(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "read_build_log", lambda: ("+ building\n! failed\n", 20, True))

    resp = client.get("/api/update/buildlog/download")

    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    # Without this the browser renders it in a tab instead of saving it, and
    # the point of the control is producing a file to attach to a bug report.
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "web-ui-build.log" in resp.headers["Content-Disposition"]
    assert resp.get_data(as_text=True) == "+ building\n! failed\n"


# ---------------------------------------------------------------------------
# A checkout that is not on a branch. Everything the updater does resolves
# `origin/<branch>`, and git's `(HEAD detached at abc1234)` placeholder used to
# be handed out as that branch -- so the page fired a command whose parentheses
# the shell would not parse, and then polled a status nothing would ever write.


def test_state_reports_a_detached_checkout(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    _stub_reads(monkeypatch)
    monkeypatch.setattr(ur, "web_ui_needs_rebuild", lambda root: False)
    monkeypatch.setattr(ur, "last_build_failed", lambda: False)
    monkeypatch.setattr(ur, "detached_head", lambda: "30aaae0c")

    data = client.get("/api/update/state").get_json()["data"]

    assert data["detached"] == "30aaae0c"
    # Not get_branch()'s error string: the page binds this to the branch picker.
    assert data["branch"] == ""


def test_state_reports_null_when_on_a_branch(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    _stub_reads(monkeypatch)
    monkeypatch.setattr(ur, "web_ui_needs_rebuild", lambda root: False)
    monkeypatch.setattr(ur, "last_build_failed", lambda: False)
    monkeypatch.setattr(ur, "detached_head", lambda: None)

    data = client.get("/api/update/state").get_json()["data"]

    assert data["detached"] is None
    assert data["branch"] == "main"


def test_pull_refuses_a_detached_checkout_without_firing_anything(ds, client, monkeypatch):
    ur, fired = _neutralize(monkeypatch)
    monkeypatch.setattr(
        ur,
        "get_branch",
        lambda: ("ERROR Not On A Branch", "This checkout is not on a branch -- HEAD is detached at 30aaae0c."),
    )
    _set_real_hw(True)
    _set_mode(Mode.STOP)

    resp = client.post("/api/update/pull")

    assert resp.status_code == 502
    assert "detached" in resp.get_json()["message"]
    assert fired == [], "a doomed command must not be fired at all"


def test_a_branch_name_reaches_the_shell_quoted(ds, client, monkeypatch):
    """Defence in depth behind get_branch()'s refusal: whatever a branch name
    contains, it arrives as one argument rather than as shell syntax."""
    ur, fired = _neutralize(monkeypatch)
    monkeypatch.setattr(ur, "get_branch", lambda: ("weird (name)", ""))
    _set_real_hw(True)
    _set_mode(Mode.STOP)

    client.post("/api/update/pull")

    assert len(fired) == 1
    probe = subprocess.run(["sh", "-n", "-c", fired[0]], capture_output=True, text=True)
    assert probe.returncode == 0, f"the shell cannot parse it: {probe.stderr}"


def test_a_launch_the_shell_refused_is_reported_not_reported_as_started(ds, client, monkeypatch):
    """The command ends in `&`, so the shell backgrounds it and answers 0 at
    once; a non-zero status means nothing ran, and the page would otherwise poll
    an install status that nothing will ever write to again."""
    _neutralize(monkeypatch, exit_code=2)
    _set_real_hw(True)

    resp = client.post("/api/update/branches/refresh")

    assert resp.status_code == 500
    assert "could not be started" in resp.get_json()["message"]


def test_a_launch_that_worked_still_reports_started(ds, client, monkeypatch):
    _, fired = _neutralize(monkeypatch, exit_code=0)
    _set_real_hw(True)

    resp = client.post("/api/update/branches/refresh")

    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"started": True}
    assert len(fired) == 1
