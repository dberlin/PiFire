from flask import Blueprint

api_history_bp = Blueprint("api_history_bp", __name__, url_prefix="/api/history")

from . import routes  # noqa: E402,F401
