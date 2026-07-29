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


# The build references /static/js, /static/css, /static/font. These three rules
# are MORE specific than Flask's built-in "/static/<path:filename>", so Werkzeug
# matches them first and serves the React bundle -- while /static/img/** falls
# through to the default static handler (PiFire's static/img: api_files uploads +
# the wizard/controller vendor images React references). Do NOT repoint Flask's
# static_folder; that would 404 every /static/img.
@spa_bp.route("/static/js/<path:filename>")
def spa_js(filename):
    return send_from_directory(os.path.join(_STATIC, "js"), filename)


@spa_bp.route("/static/css/<path:filename>")
def spa_css(filename):
    return send_from_directory(os.path.join(_STATIC, "css"), filename)


@spa_bp.route("/static/font/<path:filename>")
def spa_font(filename):
    return send_from_directory(os.path.join(_STATIC, "font"), filename)


@spa_bp.route("/")
@spa_bp.route("/<path:path>")
def spa(path=""):
    # Unmatched API/socket paths stay JSON 404s -- never serve the SPA shell
    # there, or clients can't distinguish a missing endpoint from an app route.
    if path.startswith(("api/", "mobile/")):
        return jsonify({"error": "not found"}), 404
    if not os.path.isfile(os.path.join(_DIST, "index.html")):
        abort(404)
    return send_from_directory(_DIST, "index.html")
