from flask import Blueprint

tuner_bp = Blueprint("tuner_bp", __name__, template_folder="templates", static_folder="static", url_prefix="/tuner")

from . import routes  # noqa: F401  # side-effect import: registers blueprint routes
