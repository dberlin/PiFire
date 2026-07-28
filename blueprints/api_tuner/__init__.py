from flask import Blueprint

api_tuner_bp = Blueprint("api_tuner_bp", __name__, url_prefix="/api/tuner")

from . import routes  # noqa: E402,F401
