from flask import Blueprint

api_wizard_bp = Blueprint("api_wizard_bp", __name__, url_prefix="/api/wizard")

from . import routes  # noqa: E402,F401
