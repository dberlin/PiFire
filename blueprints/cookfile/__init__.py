from flask import Blueprint

cookfile_bp = Blueprint(
    "cookfile_bp", __name__, template_folder="templates", static_folder="static", url_prefix="/cookfile"
)

from . import routes  # noqa: F401  # side-effect import: registers blueprint routes
