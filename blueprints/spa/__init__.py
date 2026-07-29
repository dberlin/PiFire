from flask import Blueprint

spa_bp = Blueprint("spa_bp", __name__)

from . import routes  # noqa: E402,F401  # side-effect import: registers routes
