"""Per-field settings errors have to survive the trip to the browser.

The dotted paths exist at the source -- pydantic's `loc` -- and were flattened
into one string before anything could route them to a widget. `message` is
pinned byte-identical here because other clients read it.
"""

import pytest


@pytest.fixture
def client(ds):
    from app import app as flask_app

    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


def _post(client, body):
    return client.post("/api/settings_update", json=body)


def test_a_bad_field_reports_its_own_path(client, ds):
    res = _post(client, {"settings": {"startup": {"duration": "not a number"}}, "flags": []})
    body = res.get_json()

    assert body["result"] == "error"
    assert body["errors"] == [{"path": "startup.duration", "message": body["errors"][0]["message"]}]
    assert body["errors"][0]["path"] == "startup.duration"
    assert body["errors"][0]["message"]


def test_the_message_is_unchanged_by_the_new_field(client, ds):
    # Other consumers read `message`; adding `errors` must not reword it.
    res = _post(client, {"settings": {"startup": {"duration": "not a number"}}, "flags": []})
    body = res.get_json()

    joined = "; ".join(f"{e['path']}: {e['message']}" for e in body["errors"])
    assert body["message"] == f"Settings update failed: {joined}"


def test_two_bad_fields_report_two_entries(client, ds):
    res = _post(
        client,
        {
            "settings": {"startup": {"duration": "x"}, "pwm": {"frequency": "y"}},
            "flags": [],
        },
    )
    paths = {e["path"] for e in res.get_json()["errors"]}

    assert paths == {"startup.duration", "pwm.frequency"}


def test_a_rejection_with_no_field_sends_an_empty_list(client, ds):
    # An unknown flag is not about any field. An invented path would send the
    # UI to highlight a widget that is not at fault.
    res = _post(client, {"settings": {}, "flags": ["not_a_flag"]})
    body = res.get_json()

    assert body["result"] == "error"
    assert body["errors"] == []


def test_a_successful_write_carries_no_errors_key_content(client, ds):
    res = _post(client, {"settings": {"startup": {"duration": 240}}, "flags": []})
    body = res.get_json()

    assert body["result"] == "success"
    assert body.get("errors", []) == []
