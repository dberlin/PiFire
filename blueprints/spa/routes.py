import hashlib
import os

from flask import abort, jsonify, send_from_directory

from blueprints.spa import spa_bp

# Absolute paths into the built React app (repo-root/web-react/dist), resolved
# from this file's location so serving never depends on the process CWD.
_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "web-react",
    "dist",
)
_STATIC = os.path.join(_DIST, "static")
_INDEX = os.path.join(_DIST, "index.html")

# A year. Everything under /static/js, /static/css and /static/font is emitted
# by rsbuild with a CONTENT HASH in its filename, so a given URL's bytes can
# never change -- a new build produces new filenames. `immutable` additionally
# tells the browser not to revalidate even on an explicit reload, which is what
# keeps a refresh cheap once index.html stops being cached.
_ASSET_CACHE = "public, max-age=31536000, immutable"

# index.html is the opposite case: one unhashed URL whose contents change with
# every build, and the file that names the hashed bundles. It must be
# revalidated on every load, or a cached copy pins the browser to the previous
# release's asset URLs however often the page is reloaded. no-cache does not
# mean "do not store": the browser keeps it and revalidates, so an unchanged
# shell costs a 304 rather than a re-download.
_SHELL_CACHE = "no-cache, must-revalidate"


def _cached(response, policy):
    response.headers["Cache-Control"] = policy
    return response


# The build references /static/js, /static/css, /static/font. These three rules
# are MORE specific than Flask's built-in "/static/<path:filename>", so Werkzeug
# matches them first and serves the React bundle -- while /static/img/** falls
# through to the default static handler (PiFire's static/img: api_files uploads +
# the wizard/controller vendor images React references). Do NOT repoint Flask's
# static_folder; that would 404 every /static/img.
@spa_bp.route("/static/js/<path:filename>")
def spa_js(filename):
    return _cached(send_from_directory(os.path.join(_STATIC, "js"), filename), _ASSET_CACHE)


@spa_bp.route("/static/css/<path:filename>")
def spa_css(filename):
    return _cached(send_from_directory(os.path.join(_STATIC, "css"), filename), _ASSET_CACHE)


@spa_bp.route("/static/font/<path:filename>")
def spa_font(filename):
    return _cached(send_from_directory(os.path.join(_STATIC, "font"), filename), _ASSET_CACHE)


@spa_bp.route("/manifest.webmanifest")
def spa_manifest():
    return _cached(send_from_directory(_DIST, "manifest.webmanifest"), _SHELL_CACHE)


def build_id():
    """Identity of the bundle currently on disk, or None when none is built.

    A digest of index.html, the file that names the hashed asset URLs, so the
    id changes when and only when the built app changes. Deliberately not an
    mtime: rsbuild's asset hashes are content-derived, so a rebuild from
    unchanged sources produces a byte-identical shell, and an mtime would make
    every open tab reload for nothing.
    """
    try:
        with open(_INDEX, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()[:16]
    except OSError:
        return None


@spa_bp.route("/api/webui")
def spa_build():
    """What build the browser should be running.

    Polled by the running app so a tab held open across an update reloads
    itself. Never cached -- a cached answer is a tab that never notices.
    """
    return _cached(jsonify({"build": build_id()}), "no-store")


@spa_bp.route("/")
@spa_bp.route("/<path:path>")
def spa(path=""):
    # Unmatched API/socket paths stay JSON 404s -- never serve the SPA shell
    # there, or clients can't distinguish a missing endpoint from an app route.
    if path.startswith(("api/", "mobile/")):
        return jsonify({"error": "not found"}), 404
    if not os.path.isfile(_INDEX):
        abort(404)
    return _cached(send_from_directory(_DIST, "index.html"), _SHELL_CACHE)
