"""JSON endpoints for PiFire's probe tuner.

The SESSION is separated from the READING, which is the one structural change
from Flask. `/tuner`'s read_tr command both enables tuning mode and returns a
value, so a page that merely polls mutates grill state on every tick and there
is no request that means "stop". Here exactly two calls write control -- open
and close -- and everything else is a pure read.

Opening moves a stopped grill to Monitor. Monitor lights nothing, but it is a
real mode change, so opening is refused from any mode that is neither Stop nor
Monitor: tuning during a cook would fight the controller for the probes.
"""

from flask import jsonify, request
from werkzeug.exceptions import BadRequest

from common.app import api_response
from common.common import WriteKind
from common.control_delta import control_delta
from common.datastore_accessors import read_control, read_tr, write_control
from common.modes import Mode

from . import api_tuner_bp

#: The only two modes a tuning session may be opened from. Stop is the normal
#: case; Monitor is allowed because the session itself puts the grill there,
#: which makes re-opening after a reload a no-op rather than a refusal.
TUNABLE_MODES = (Mode.STOP, Mode.MONITOR)


def error(message, status, **data):
    return jsonify(api_response("Error", message, data or None)), status


def json_body():
    try:
        return request.get_json(silent=True) or {}
    except BadRequest:
        return {}


def set_control(**values):
    write_control(control_delta(set_values=values), WriteKind.DELTA, origin="api-tuner")


def require_tunable():
    """None if a session may be opened, else a 409 refusing it.

    Re-reads control rather than taking it as an argument: the caller's copy
    may predate the request, and this is the guard between a web request and a
    mode change on a live grill.
    """
    control = read_control()
    if control.get("mode") not in TUNABLE_MODES:
        return error("not_tunable", 409, mode=control.get("mode"))
    return None


@api_tuner_bp.route("/session", methods=["POST"])
def tuner_session():
    """Open or close a tuning session.

    Closing is IDEMPOTENT and closing never stops a cook: it restores Stop only
    when the mode is currently Monitor, so a cook started while the session was
    open is left alone. `restored` reports whether the mode was actually moved,
    which is what makes "close twice" observable rather than silent.
    """
    body = json_body()
    if not isinstance(body.get("open"), bool):
        return error("bad_request", 400, field="open")

    if body["open"]:
        refusal = require_tunable()
        if refusal:
            return refusal
        control = read_control()
        moved = control.get("mode") == Mode.STOP
        values = {"tuning_mode": True}
        if moved:
            values.update({"mode": Mode.MONITOR, "updated": True})
        set_control(**values)
        return jsonify(api_response("OK", None, {"open": True, "mode": Mode.MONITOR, "restored": moved})), 200

    control = read_control()
    restored = control.get("mode") == Mode.MONITOR
    values = {"tuning_mode": False}
    if restored:
        values.update({"mode": Mode.STOP, "updated": True})
    set_control(**values)
    mode_after = Mode.STOP if restored else control.get("mode")
    return jsonify(api_response("OK", None, {"open": False, "mode": mode_after, "restored": restored})), 200


@api_tuner_bp.route("/tr", methods=["GET"])
def tuner_tr():
    """The current resistance reading for one probe, in ohms.

    Inert by design: the page polls this once a second, and a poll that moved
    the grill between modes is exactly the shape this blueprint exists to
    avoid.

    A probe that is not in the blob reads `null`, not 0. Flask returns 0, which
    a client cannot tell apart from a real zero-ohm reading -- and 0 ohms is
    what a shorted probe reports, so the two cases genuinely differ.
    """
    probe = request.args.get("probe", "")
    if not probe:
        return error("bad_request", 400, field="probe")

    readings = read_tr()
    control = read_control()
    return jsonify(
        api_response(
            "OK",
            None,
            {
                "probe": probe,
                "trohms": readings.get(probe),
                #  A reading taken outside a session is stale: control.py only
                #  refreshes this blob in tuning mode.
                "tuning": bool(control.get("tuning_mode")),
            },
        )
    ), 200
