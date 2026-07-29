import re

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


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


def test_unknown_api_path_is_json_404(client):
    r = client.get("/api/does-not-exist-xyz")
    assert r.status_code == 404
    assert "text/html" not in r.content_type


def test_unknown_mobile_path_is_json_404(client):
    r = client.get("/mobile/does-not-exist-xyz")
    assert r.status_code == 404
    assert "text/html" not in r.content_type
