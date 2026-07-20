from flask import Blueprint

dash_bp = Blueprint("dash_bp", __name__, template_folder="templates", static_folder="static", url_prefix="/dash")

from . import routes  # noqa: F401  # side-effect import: registers blueprint routes
