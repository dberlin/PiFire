from flask import Blueprint

admin_bp = Blueprint("admin_bp", __name__, template_folder="templates", static_folder="static", url_prefix="/admin")

from . import routes  # noqa: F401  # side-effect import: registers blueprint routes
