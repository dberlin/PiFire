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

from flask import jsonify

from common.app import api_response
from common.common import process_metrics
from common.datastore_accessors import read_all_metrics, read_settings

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
    return jsonify(
        api_response(
            "OK",
            None,
            {
                "metrics": processed_metrics(settings),
                "units": settings["globals"]["units"],
                "augerrate": settings["globals"]["augerrate"],
            },
        )
    ), 200
