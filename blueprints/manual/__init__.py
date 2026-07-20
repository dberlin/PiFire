from flask import Blueprint

manual_bp = Blueprint("manual_bp", __name__, template_folder="templates", static_folder="static", url_prefix="/manual")

from . import routes  # noqa: F401  # side-effect import: registers blueprint routes
