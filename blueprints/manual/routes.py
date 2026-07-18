from flask import render_template
from common.datastore_accessors import read_settings, read_control

from . import manual_bp


@manual_bp.route("/", methods=["POST", "GET"])
def manual_page(action=None):
    settings = read_settings()
    control = read_control()
    return render_template(
        "manual/index.html",
        settings=settings,
        control=control,
    )
