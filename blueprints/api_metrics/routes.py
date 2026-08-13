"""JSON endpoints for PiFire's metrics surface.

Read-only, and deliberately so: there is nothing on a metrics page to write.
The blueprint registers no POST at all rather than registering one that
refuses, so there is no door to leave unlocked later.

Why a new blueprint rather than adding JSON to blueprints/metrics: that one
answers `/metrics/<action>` for both POST and GET and dispatches on a path
segment, so `export` and any future action share a rule with the page itself.
New code gets one rule per thing it does.

The path also has to beat blueprints/api's `/api/<action>` catch-all, which
matches the literal `/api/metrics`. It does -- Werkzeug sorts static rules ahead
of converter rules -- but that is an accident of routing order rather than a
statement, so tests/web/test_api_metrics.py pins the resolved endpoint.
"""

import datetime

from flask import jsonify, send_file

from common.app import api_response, prepare_metrics_csv
from common.common import process_metrics
from common.persistence.runtime import read_settings
from common.datastore_accessors import read_all_metrics
from common.web_contracts.content import MetricsPayload, validated_content_json

from . import api_metrics_bp


def processed_metrics(settings):
    """Every metrics record, with process_metrics' derived columns applied.

    The derivation stays server-side rather than being re-implemented in the
    client: process_metrics is the only definition of what "60 s" or
    "30 grams" means, and a second one in TypeScript would be a second answer.
    """
    return process_metrics(read_all_metrics(), augerrate=settings["globals"]["augerrate"])


@api_metrics_bp.route("", methods=["GET"])
@api_metrics_bp.route("/", methods=["GET"])
def metrics_listing():
    """The whole metrics table, in insertion order.

    Not paginated. The table is capped server-side and a metrics record is a
    handful of scalars, so the whole thing is smaller than one history window.

    `units` and `augerrate` ride along because the client renders both: the
    temperature suffix on setpoint rows, and the auger rate as the stated
    assumption behind every pellet-usage estimate on the page.
    """
    settings = read_settings()
    data = validated_content_json(
        MetricsPayload,
        {
            "metrics": processed_metrics(settings),
            "units": settings["globals"]["units"],
            "augerrate": settings["globals"]["augerrate"],
        },
    )
    return jsonify(api_response("OK", None, data)), 200


@api_metrics_bp.route("/export", methods=["GET"])
def metrics_export():
    """The whole table as a CSV attachment.

    The filename is composed here from the clock and NOTHING else: no request
    value reaches prepare_metrics_csv, which joins its argument under /tmp.
    common/app.py's _export_temp_path basenames what it is given, but the
    request is not the place to find out whether that held.

    The stamp is passed bare. blueprints/metrics appends "-PiFire-Metrics-Export"
    to it before calling the same helper, which appends that suffix itself --
    so the legacy download arrives named -PiFire-Metrics-Export twice.

    Exports processed_metrics() rather than read_all_metrics() so the CSV
    carries the same derived columns the page shows -- an export that
    disagreed with the screen would be worse than no export.
    """
    settings = read_settings()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    path = prepare_metrics_csv(processed_metrics(settings), stamp)
    return send_file(path, mimetype="text/csv", as_attachment=True, max_age=0)
