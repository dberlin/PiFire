"""Playwright coverage for the probe tuner page
(blueprints/tuner/routes.py's `tuner_page`, a single route handling a
`command` dispatch over two content-types: `request.form` for template
fragment rendering, `request.json` for the tuning-tool's data/control
commands).

Follows the pattern established in test_page_settings.py; see
tests/web/conftest.py for the shared harness.

Actions covered here (all direct-POST style: this page is a heavily
JS-driven single-page tool -- see blueprints/tuner/static/tuner/js/tuner.js
-- where every one of these commands is normally fired by JS in response
to wizard-style button clicks, not plain HTML forms; POSTing the same
payload the JS would build and reading back through the datastore proves
the same round trip without reverse-engineering the click sequence, the
same rationale test_page_settings.py used for `pwm_duty_cycle`):

- (base GET, no command)   -- full-page render, key sections present.
- `command=render`         -- template-fragment endpoint (form content-type)
                              backing the auto/manual tool cards.
- `command=stop_tuning`    -- turns off tuning_mode and, if in Monitor,
                              stops the system. A MERGE control write (see
                              conftest.py's drain_control_writes -- this
                              harness has no control loop to drain it
                              automatically).
- `command=read_tr`        -- enables tuning_mode / Monitor mode as a
                              side effect, then returns the current probe's
                              Tr reading (0, since control:tuning is never
                              populated by real hardware here).
- `command=manual_finish`  -- computes Steinhart-Hart coefficients from
                              three manually-entered temp/Tr points (pure
                              math, no hardware) and stops tuning mode.
- `command=read_auto_status` -- flushes/queries the autotune queue and
                              reads current probe temps; needs
                              `control:current` populated with the probe
                              labels from probe_map (normally zeroed out by
                              history_page's `stream` action when the
                              system is stopped -- reused here as a
                              precondition-seed, not a real dependency
                              between the two blueprints).

NOT covered: `auto_finish` (functionally identical to `manual_finish` on
this route -- same tuning_mode/mode side effects, just a different label
on the same command branch) and driving the wizard-style click flow
through tuner.js itself (would require reverse-engineering its full
state machine for no additional server-side assertion power beyond what
direct-POSTing the same JSON already proves).
"""

import pytest

from tests.web.conftest import (
    apply_control,
    drain_control_writes,
    read_control_from_server,
    requires_chromium,
)

pytestmark = requires_chromium


@pytest.fixture(autouse=True)
def seed_probe_device_info():
    """See test_page_dashboard.py's identical fixture."""
    from common.datastore_accessors import write_generic_key

    write_generic_key("probe_device_info", [])


def test_tuner_page_renders_key_sections(live_server, page):
    resp = page.goto(f"{live_server}/tuner/")

    assert resp.status == 200
    assert page.title().startswith("Probe Tuning Tools")
    assert page.locator("#autotune_btn").count() == 1
    assert page.locator("#manual_tune_btn").count() == 1
    assert page.locator("#tunerResultsChart").count() == 1
    assert page.locator("form[name='tunerAddProfile']").count() == 1


def test_command_render_returns_template_fragment(live_server, page):
    resp = page.request.post(
        f"{live_server}/tuner/",
        form={"command": "render", "value": "manual_instruction_card"},
    )

    assert resp.status == 200
    assert "text/html" in resp.headers["content-type"]
    assert len(resp.text()) > 0


def test_command_stop_tuning_via_direct_post(live_server, page):
    apply_control(lambda c: (c.__setitem__("tuning_mode", True), c.__setitem__("mode", "Monitor"))[0])
    assert read_control_from_server()["tuning_mode"] is True

    resp = page.request.post(f"{live_server}/tuner/", data={"command": "stop_tuning"})
    assert resp.status == 200

    drain_control_writes()
    control = read_control_from_server()
    assert control["tuning_mode"] is False
    assert control["mode"] == "Stop"


def test_command_read_tr_enables_tuning_and_returns_trohms(live_server, page):
    apply_control(lambda c: (c.__setitem__("tuning_mode", False), c.__setitem__("mode", "Stop"))[0])

    resp = page.request.post(f"{live_server}/tuner/", data={"command": "read_tr", "probe_selected": "Grill"})
    assert resp.status == 200
    assert resp.json() == {"trohms": 0}  # control:tuning is never populated without real hardware

    drain_control_writes()
    control = read_control_from_server()
    assert control["tuning_mode"] is True
    assert control["mode"] == "Monitor"


def test_command_manual_finish_computes_coefficients(live_server, page):
    apply_control(lambda c: (c.__setitem__("tuning_mode", True), c.__setitem__("mode", "Monitor"))[0])

    resp = page.request.post(
        f"{live_server}/tuner/",
        data={
            "command": "manual_finish",
            "tunerManualLowTemp": "100",
            "tunerManualLowTr": "100000",
            "tunerManualMediumTemp": "200",
            "tunerManualMediumTr": "20000",
            "tunerManualHighTemp": "300",
            "tunerManualHighTr": "5000",
        },
    )

    assert resp.status == 200
    body = resp.json()
    assert set(body.keys()) == {"labels", "chart_data", "coefficients"}
    a, b, c = body["coefficients"]["a"], body["coefficients"]["b"], body["coefficients"]["c"]
    assert all(isinstance(v, float) for v in (a, b, c))
    assert len(body["labels"]) > 0
    assert len(body["chart_data"]) > 0

    drain_control_writes()
    assert read_control_from_server()["tuning_mode"] is False


def test_command_read_auto_status_first_run(live_server, page):
    # Precondition: zero out control:current with the default probe map's
    # labels (normally done by history_page's `stream` action once the
    # control loop reports Stop/Error; reused here purely as a data-shape
    # seed, not a real cross-blueprint dependency).
    stream_resp = page.request.get(f"{live_server}/history/stream")
    assert stream_resp.status == 200

    apply_control(lambda c: c.__setitem__("tuning_mode", False))

    resp = page.request.post(
        f"{live_server}/tuner/",
        data={"command": "read_auto_status", "probe_selected": "Grill", "probe_reference": "Grill"},
    )

    assert resp.status == 200
    body = resp.json()
    assert body["ready"] is False  # fewer than 10 datapoints recorded
    assert body["current_temp"] == 0  # zeroed-out probe temp from the stream seed above

    drain_control_writes()
    control = read_control_from_server()
    # First call after tuning_mode was False flips it on and switches to
    # Monitor mode, per the route's "first_run" handling.
    assert control["tuning_mode"] is True
    assert control["mode"] == "Monitor"
