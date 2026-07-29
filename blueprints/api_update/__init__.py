from flask import Blueprint

api_update_bp = Blueprint("api_update_bp", __name__, url_prefix="/api/update")

from . import routes  # noqa: E402,F401
