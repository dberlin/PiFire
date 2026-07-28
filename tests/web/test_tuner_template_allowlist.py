"""`/tuner`'s fragment endpoint renders a macro NAMED by the client.

Before 2026-07-28 that name was concatenated straight into Jinja source and
handed to render_template_string, so the value was parsed as template code
rather than escaped as data -- server-side template injection. The six names
the client actually sends are now an allowlist.

The double interpolation (`render_<value>` in the import AND
`render_<value>(...)` in the call) makes a clean value-emitting payload
awkward to construct, which is an accidental mitigation, not a fix: an
attacker string still reaches the Jinja compiler. Two observables prove it,
each with a clean 200-vs-400 distinction against the vulnerable code:

  * `manual_tool_card` -- a macro that EXISTS but the client never requests --
    renders a full fragment under the old code (200) and is refused under the
    new one (400);
  * a value containing Jinja syntax reaches the compiler under the old code
    (it raises from Jinja internals) and is refused before compilation under
    the new one (400).

See docs/superpowers/plans/2026-07-28-react-tuner-manual.md.
"""

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


ALLOWED = [
    "manual_instruction_card",
    "manual_tool",
    "manual_finish_btn",
    "auto_instruction_card",
    "auto_tool",
    "auto_finish_btn",
]


@pytest.mark.parametrize("name", ALLOWED)
def test_every_name_the_client_sends_still_renders(ds, client, name):
    """The six literals in static/tuner/js/tuner.js. If one of these 404s, the
    legacy page has a blank panel and no error anywhere."""
    resp = client.post("/tuner/", data={"command": "render", "value": name})
    assert resp.status_code == 200
    assert resp.get_data(as_text=True).strip() != ""


def test_a_macro_defined_but_never_requested_is_refused(ds, client):
    """render_manual_tool_card is defined in _macro_tuner.html but is only
    called from inside render_manual_tool -- the client never asks for it by
    name. The clean positive proof of the flaw: under the old code this
    rendered a full ~1.5 KB fragment (200); the allowlist refuses it (400).
    Reachable is not the same as offered.
    """
    resp = client.post("/tuner/", data={"command": "render", "value": "manual_tool_card"})
    assert resp.status_code == 400


def test_a_bogus_name_is_refused_not_compiled(ds, client):
    """A name matching no macro used to reach render_template_string and raise
    an UndefinedError from deep in Jinja -- proof the string was compiled. The
    allowlist turns it into a flat 400."""
    resp = client.post("/tuner/", data={"command": "render", "value": "totally_bogus"})
    assert resp.status_code == 400


def test_template_syntax_in_the_name_is_refused(ds, client):
    """The injection itself: `value` used to be concatenated into template
    SOURCE. Under the old code this reached the compiler; under the new one it
    is refused before any rendering, so no evaluated result (7*6) can appear.
    """
    payload = 'manual_tool %}{{ 7*6 }}{{ "'
    resp = client.post("/tuner/", data={"command": "render", "value": payload})
    assert resp.status_code == 400
    assert b"42" not in resp.get_data()


def test_a_name_naming_another_template_is_refused(ds, client):
    resp = client.post(
        "/tuner/",
        data={"command": "render", "value": "../../settings/_macro_settings.html"},
    )
    assert resp.status_code == 400
