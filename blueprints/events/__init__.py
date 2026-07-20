from flask import Blueprint

events_bp = Blueprint("events_bp", __name__, template_folder="templates", static_folder="static", url_prefix="/events")

from . import routes  # noqa: F401  # side-effect import: registers blueprint routes
