import re

import pytest

from app import app as flask_app


def test_root_serves_spa(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"/static/" in r.data  # SPA shell references hashed assets


def test_deep_link_serves_same_spa_shell(client):
    # A React-router path with no Flask route boots the SPA (same index.html).
    # Avoid top-level segments that collide with a still-registered legacy
    # page blueprint (e.g. "/admin" 308-redirects to "/admin/" via that
    # blueprint's own index route, pre-empting this catch-all) -- those
    # collisions disappear once the page blueprints are retired.
    assert client.get("/some-react-only-route").get_data() == client.get("/").get_data()


def test_hashed_bundle_asset_is_served(client):
    # A /static/js|css|font asset from the build serves via the spa rules.
    index = client.get("/").get_data(as_text=True)
    m = re.search(r'/static/(?:js|css|font)/[^"\']+', index)
    assert m, "index.html referenced no /static/{js,css,font} asset"
    assert client.get(m.group(0)).status_code == 200


def test_static_img_still_served_by_flask_default(client):
    # REGRESSION: the spa /static/{js,css,font} rules must NOT shadow /static/img.
    # api_files serves uploads there, and React references it directly.
    assert client.get("/static/img/pifire-cf-thumb.png").status_code == 200


def test_favicon_is_declared_and_the_declared_path_is_served(client):
    """Both ends of the favicon path, in one test.

    The href lives in web-react/index.html and the file is served by Flask's
    default static handler -- two repos' worth of distance between the two, and
    nothing else looks at both. Asserting the tag exists would pass against a
    dead link; asserting the file is served would pass with no tag at all. So
    this reads the href OUT of the shipped shell and fetches exactly that.
    """
    index = client.get("/").get_data(as_text=True)
    m = re.search(r'<link[^>]+rel="icon"[^>]+href="([^"]+)"', index)
    assert m, "index.html declared no rel=icon"

    href = m.group(1)
    assert client.get(href).status_code == 200, f"favicon href {href} is not served"


def test_unknown_api_path_is_json_404(client):
    r = client.get("/api/does-not-exist-xyz")
    assert r.status_code == 404
    assert "text/html" not in r.content_type


def test_unknown_mobile_path_is_json_404(client):
    r = client.get("/mobile/does-not-exist-xyz")
    assert r.status_code == 404
    assert "text/html" not in r.content_type


def test_server_error_template_renders():
    # render_template must still find server_error.html after the base.html
    # purge -- it no longer extends base.html, so this must keep passing.
    from flask import render_template

    with flask_app.test_request_context():
        html = render_template("server_error.html")
    assert html


def test_retired_page_routes_are_gone():
    rules = {r.rule for r in flask_app.url_map.iter_rules()}
    for prefix in (
        "/dash",
        "/admin/",
        "/settings/",
        "/tuner/",
        "/history/",
        "/metrics/",
        "/pellets/",
        "/recipes/",
        "/cookfile/",
        "/probeconfig/",
        "/manual/",
        "/events/",
        "/logs/",
        "/manifest/",
        "/wizard/",
        "/update/",
    ):
        stale = [r for r in rules if r.startswith(prefix)]
        assert not stale, f"{prefix} still routed: {stale}"
    assert any(r.startswith("/api/") for r in rules)  # kept
    # mobile has no HTTP routes of its own (socketio-only namespace) -- the spa
    # catch-all special-cases "mobile/" paths to a JSON 404 (see
    # blueprints/spa/routes.py), so assert the blueprint itself is kept.
    assert "mobile" in flask_app.blueprints
    assert "/<path:path>" in rules  # SPA catch-all
