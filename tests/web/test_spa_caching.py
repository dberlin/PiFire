"""How the SPA shell and its assets are cached, and how a tab learns to reload.

Picking up an update needed a shift-refresh. index.html is the one unhashed URL
in the build and it is what NAMES the content-hashed bundles, so once a browser
had it cached, an ordinary reload re-read the cached shell and re-requested the
same old asset URLs -- the new build was on disk and unreachable.
"""

import hashlib
import os

import pytest

from blueprints.spa import routes as spa_routes
from common.web_contracts.core import WebUiBuildResponse


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """A built bundle in a temp dir, pointed at by the blueprint."""
    root = tmp_path / "dist"
    (root / "static" / "js").mkdir(parents=True)
    (root / "index.html").write_text('<script src="/static/js/index.abc123.js"></script>')
    (root / "static" / "js" / "index.abc123.js").write_text("console.log(1)")
    monkeypatch.setattr(spa_routes, "_DIST", str(root))
    monkeypatch.setattr(spa_routes, "_STATIC", str(root / "static"))
    monkeypatch.setattr(spa_routes, "_INDEX", str(root / "index.html"))
    return root


def test_the_shell_is_revalidated_rather_than_reused(dist, client):
    """The bug in one assertion: a cached index.html pins the browser to the
    previous release's asset URLs however often it reloads."""
    resp = client.get("/")

    assert resp.status_code == 200
    cache = resp.headers["Cache-Control"]
    assert "no-cache" in cache
    assert "must-revalidate" in cache
    assert "immutable" not in cache


def test_hashed_assets_are_cached_hard(dist, client):
    """Safe precisely because the filename carries a content hash: these bytes
    can never change, a new build emits new names."""
    resp = client.get("/static/js/index.abc123.js")

    assert resp.status_code == 200
    assert "immutable" in resp.headers["Cache-Control"]
    assert "max-age=31536000" in resp.headers["Cache-Control"]


def test_build_id_identifies_the_bundle(dist, client):
    body = client.get("/api/webui").get_json()

    expected = hashlib.sha256((dist / "index.html").read_bytes()).hexdigest()[:16]
    assert body["build"] == expected
    validated = WebUiBuildResponse.model_validate(body, strict=True)
    assert validated.model_dump(mode="json", by_alias=True, exclude_none=False) == body


def test_the_build_id_endpoint_is_never_cached(dist, client):
    """A cached answer here is a tab that never notices an update."""
    assert client.get("/api/webui").headers["Cache-Control"] == "no-store"


def test_the_build_id_changes_only_when_the_build_does(dist, client):
    first = client.get("/api/webui").get_json()["build"]

    # A rebuild that produces identical output must NOT look like a new build:
    # rsbuild's asset hashes are content-derived, so unchanged sources give a
    # byte-identical shell. Only the mtime moves -- and an mtime-based id would
    # reload every open tab for nothing.
    os.utime(dist / "index.html", (99999, 99999))
    assert client.get("/api/webui").get_json()["build"] == first

    (dist / "index.html").write_text('<script src="/static/js/index.def456.js"></script>')
    assert client.get("/api/webui").get_json()["build"] != first


def test_build_id_is_null_when_nothing_is_built(dist, client):
    """A fresh clone has no bundle. The client must read that as "no answer",
    not as a new build to reload for."""
    os.remove(dist / "index.html")

    assert client.get("/api/webui").get_json()["build"] is None
