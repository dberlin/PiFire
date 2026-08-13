import time

from flask import jsonify, request

from common.app import prepare_annotations
from common.persistence.runtime import read_settings
from file_mgmt.cookfile import prepare_chartdata
from common.web_contracts.content import HistoryChartData, validated_content_json

from . import api_history_bp

# The history store records ~20 samples per minute (see the legacy
# blueprints/history/routes.py, which computes num_items as minutes * 20).
SAMPLES_PER_MINUTE = 20


@api_history_bp.route("/chart", methods=["GET"])
def history_chart():
    """Read-only chart data for the React history page.

    Deliberately NOT the legacy POST /history/refresh: that route persists
    settings["history_page"]["minutes"] as a side effect of being asked for a
    window, which would let a client's transient zoom overwrite the user's
    saved preference.
    """
    settings = read_settings()
    history_page = settings["history_page"]

    raw = request.args.get("minutes")
    if raw is None:
        minutes = int(history_page["minutes"])
    else:
        try:
            minutes = int(raw)
        except TypeError, ValueError:
            return jsonify({"result": "error", "message": "invalid_minutes"}), 400
        if minutes < 1:
            return jsonify({"result": "error", "message": "invalid_minutes"}), 400

    payload = prepare_chartdata(
        history_page["probe_config"],
        num_items=minutes * SAMPLES_PER_MINUTE,
        reduce=True,
        data_points=history_page.get("datapoints", 10000),
        tolerance=history_page.get("fidelity_degrees", 2.0),
    )
    payload["annotations"] = prepare_annotations(time.time() - minutes * 60)
    payload["minutes"] = minutes
    return jsonify(validated_content_json(HistoryChartData, payload)), 200
