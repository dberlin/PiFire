from flask import Blueprint

api_files_bp = Blueprint("api_files_bp", __name__, url_prefix="/api/files")

from . import routes  # noqa: E402,F401
