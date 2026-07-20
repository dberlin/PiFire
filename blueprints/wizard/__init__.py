from flask import Blueprint

wizard_bp = Blueprint("wizard_bp", __name__, template_folder="templates", static_folder="static", url_prefix="/wizard")

from . import routes  # noqa: F401  # side-effect import: registers blueprint routes
