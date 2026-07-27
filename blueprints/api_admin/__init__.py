from flask import Blueprint

api_admin_bp = Blueprint("api_admin_bp", __name__, url_prefix="/api/admin")

from . import routes  # noqa: E402,F401
