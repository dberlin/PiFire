from flask import Blueprint

api_probe_profiles_bp = Blueprint("api_probe_profiles_bp", __name__, url_prefix="/api/probe_profiles")

from . import routes  # noqa: E402,F401
