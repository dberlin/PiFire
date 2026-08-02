"""
==============================================================================
 PiFire Web UI (Flask App) Process
==============================================================================

Description:
  This script will start at boot, and start up the web user
  interface.

  This script runs as a separate process from the control program
  implementation which handles interfacing the hardware directly.

==============================================================================
"""

"""
==============================================================================
 Imported Modules
==============================================================================
"""

from flask import Flask, render_template
from flask_mobility import Mobility
from flask_socketio import SocketIO
from flask_qrcode import QRcode
from werkzeug.exceptions import InternalServerError
from common import datastore
from common.common import ErrorKind, create_logger, log_path
from common.datastore_accessors import flush_errors, read_settings
from common.system import is_real_hardware
import logging

# First-boot migration: import existing settings.json / pelletdb.json into
# SQLite if it hasn't happened yet. Must run before the first settings read
# below (and before any blueprint route can be hit). This is the ONLY trigger
# of that import in production (control.py calls it too; it is idempotent,
# so running it from both independently-supervised processes, in either
# order, is safe).
datastore.init()

# The web tier clears its own banners at its own boot, exactly as control.py and
# display_process.py do for theirs. The sole ErrorKind.WEB producer is the
# detached extra_installer child, so an install failure's banner now stands
# until this process restarts. No other kind is ours to clear.
flush_errors(ErrorKind.WEB)

"""
==============================================================================
 Constants & Globals 
==============================================================================
"""
from config import ProductionConfig  # ProductionConfig or DevelopmentConfig
from common.server_status import set_server_status

app = Flask(__name__)
# async_mode='threading' pins Flask-SocketIO to the threaded (gthread) server
# model. gunicorn runs the webapp with `-k gthread`, which does NOT monkey-patch
# the stdlib; that is what lets in-process asyncio (e.g. ThermoWorks Cloud
# discovery) work. eventlet/gevent are intentionally not installed, so engineio
# already defaults to 'threading'; the explicit pin is a safety belt so a stray
# eventlet/gevent install can't silently switch modes and reintroduce the
# monkey-patch hang. WebSockets are still served natively via simple-websocket.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
QRcode(app)
Mobility(app)

""" Load Configuration Settings """
# Use ProductionConfig for production environment, DevelopmentConfig for development
# Uncomment the line below to switch to DevelopmentConfig
# app.config.from_object(DevelopmentConfig)
app.config.from_object(ProductionConfig)

""" Flask Blueprints """
from blueprints.api import api_bp
from blueprints.api_wizard import api_wizard_bp
from blueprints.api_history import api_history_bp
from blueprints.api_files import api_files_bp
from blueprints.api_admin import api_admin_bp
from blueprints.api_metrics import api_metrics_bp
from blueprints.api_tuner import api_tuner_bp
from blueprints.api_update import api_update_bp

""" Register Flask Blueprints """
app.register_blueprint(api_bp, url_prefix="/api")
app.register_blueprint(api_wizard_bp, url_prefix="/api/wizard")
app.register_blueprint(api_history_bp, url_prefix="/api/history")
app.register_blueprint(api_files_bp, url_prefix="/api/files")
app.register_blueprint(api_admin_bp, url_prefix="/api/admin")
app.register_blueprint(api_metrics_bp, url_prefix="/api/metrics")
app.register_blueprint(api_tuner_bp, url_prefix="/api/tuner")
app.register_blueprint(api_update_bp, url_prefix="/api/update")

"""
==============================================================================
 App Routes
==============================================================================
"""


@app.errorhandler(InternalServerError)
def handle_500(e):
    """Handle 500 Server Error"""
    return render_template("server_error.html"), 500


@app.context_processor
def inject_theme_and_grill_name():
    """Inject page_theme/grill_name into every template's context, replacing
    the identical render_template kwargs that used to be passed at each of
    the 39 call sites across the blueprints."""
    settings = read_settings()
    return {
        "page_theme": settings["globals"].get("bootstrap_page_theme", "light"),
        "grill_name": settings["globals"].get("grill_name", ""),
    }


"""
==============================================================================
 Register Mobile Blueprint
==============================================================================
"""
# Register mobile blueprint and provide it with socketio instance
# (socketio is created once, above, right after the Flask app.)
from blueprints.mobile import mobile_bp

mobile_bp.socketio = socketio
app.register_blueprint(mobile_bp, url_prefix="/mobile")

"""
==============================================================================
 Register SPA Blueprint (serves the React build; must be registered LAST so
 real backend routes win over the catch-all)
==============================================================================
"""
from blueprints.spa import spa_bp

app.register_blueprint(spa_bp)

"""
==============================================================================
 Main Program Start
==============================================================================
"""

# Setup logging
settings = read_settings()

log_level = logging.DEBUG if settings["globals"]["debug_mode"] else logging.ERROR
webappLogger = create_logger(
    "webapp", filename=log_path("webapp.log"), messageformat="%(asctime)s [%(levelname)s] %(message)s", level=log_level
)

log_level = logging.DEBUG if settings["globals"]["debug_mode"] else logging.INFO
eventLogger = create_logger(
    "events", filename=log_path("events.log"), messageformat="%(asctime)s [%(levelname)s] %(message)s", level=log_level
)

event_message = f"PiFire Web UI started. PiFire Version: {settings['versions']['server']} Build: {settings['versions']['build']}, Debug Mode: {settings['globals']['debug_mode']}"
webappLogger.info(event_message)
eventLogger.info(event_message)

# Initialize server status to 'available' when app starts
with app.app_context():
    set_server_status("available")

if __name__ == "__main__":
    if is_real_hardware():
        socketio.run(app, host="0.0.0.0")
    else:
        socketio.run(app, host="0.0.0.0", debug=True)
