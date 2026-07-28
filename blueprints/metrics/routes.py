import datetime
from flask import render_template, request, send_file
from common.common import process_metrics
from common.datastore_accessors import read_settings, read_control, read_all_metrics
from common.app import prepare_metrics_csv

from . import metrics_bp


@metrics_bp.route("/<action>", methods=["POST", "GET"])
@metrics_bp.route("/", methods=["POST", "GET"])
def metrics_page(action=None):
    settings = read_settings()
    control = read_control()

    #  The grill's own auger rate, not process_metrics' 0.3 g/s default. The
    #  setting has been writable since blueprints/settings/routes.py:679 and is
    #  what the dashboard's estimate already uses (common/app.py:175); this page
    #  was the one consumer still reading a stranger's auger.
    metrics_data = process_metrics(read_all_metrics(), augerrate=settings["globals"]["augerrate"])

    if (request.method == "GET") and (action == "export"):
        filename = datetime.datetime.now().strftime("%Y%m%d-%H%M") + "-PiFire-Metrics-Export"
        csvfilename = prepare_metrics_csv(metrics_data, filename)
        return send_file(csvfilename, as_attachment=True, max_age=0)

    return render_template(
        "metrics/index.html",
        settings=settings,
        control=control,
        metrics_data=metrics_data,
    )
