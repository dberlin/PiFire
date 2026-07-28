from flask import Blueprint

api_metrics_bp = Blueprint("api_metrics_bp", __name__, url_prefix="/api/metrics")

from . import routes  # noqa: E402,F401
