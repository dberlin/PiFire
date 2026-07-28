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
from common.common import WriteKind, generate_uuid
from common.control_delta import control_delta
from common.datastore_accessors import (
    read_control,
    read_settings,
    read_tr,
    write_control,
    write_settings,
)
from common.modes import Mode

from blueprints.tuner.tuner import calc_shh_chart, calc_shh_coefficients

from . import api_tuner_bp

SEGMENTS = ("High", "Medium", "Low")

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


@api_tuner_bp.route("/coefficients", methods=["POST"])
def tuner_coefficients():
    """Solve Steinhart-Hart for three temperature/resistance pairs.

    The maths itself is blueprints/tuner/tuner.py's, unchanged -- one
    definition, and tests/web/test_page_tuner.py pins its return shape. What is
    new is that both of its silent failures get a signal:

      * calc_shh_coefficients wraps everything in a bare `except:` and returns
        (0, 0, 0). Flask handed that tuple to the save form, so a failed tune
        produced a saveable profile of zeros. Here it is a 422.
      * calc_shh_chart abandons the whole series the moment temp_to_tr throws,
        which its own docstring calls common. An empty chart is reported as
        chart_ok: false rather than drawn as an empty chart.
    """
    body = json_body()
    raw = body.get("points")
    if not isinstance(raw, list) or len(raw) != 3:
        return error("bad_request", 400, field="points")

    by_segment = {}
    for entry in raw:
        if not isinstance(entry, dict):
            return error("bad_request", 400, field="points")
        segment = entry.get("segment")
        if segment not in SEGMENTS:
            return error("bad_request", 400, field="segment")
        try:
            #  bool is a subclass of int, so `True` would otherwise sail
            #  through float() and become a 1-ohm reading.
            for key in ("temp", "trohms"):
                if isinstance(entry.get(key), bool):
                    raise TypeError(key)
            by_segment[segment] = (float(entry["temp"]), float(entry["trohms"]))
        except TypeError, ValueError, KeyError:
            return error("bad_request", 400, field="points")

    if set(by_segment) != set(SEGMENTS):
        return error("bad_request", 400, field="points")

    units = read_settings()["globals"]["units"]
    (high_t, high_r) = by_segment["High"]
    (medium_t, medium_r) = by_segment["Medium"]
    (low_t, low_r) = by_segment["Low"]

    a, b, c = calc_shh_coefficients(low_t, medium_t, high_t, low_r, medium_r, high_r, units=units)
    if (a, b, c) == (0, 0, 0):
        return error("uncomputable", 422)

    _labels, chart = calc_shh_chart(
        a, b, c, units=units, temp_range=220, tr_points=[int(high_r), int(medium_r), int(low_r)]
    )
    return jsonify(api_response("OK", None, {"a": a, "b": b, "c": c, "chart": chart, "chart_ok": bool(chart)})), 200


@api_tuner_bp.route("/profile", methods=["POST"])
def tuner_profile():
    """Save a probe profile, optionally attaching it to a probe.

    The same two writes _settings_addprofile makes, with three differences that
    are all deliberate:

      * numbers are validated instead of being float()-ed inside a bare
        `except:` that reports "something bad happened";
      * an apply_to that matches no probe is a 404, not a silent success --
        Flask loops looking for the label and simply does not find it, so the
        operator is told the profile was applied when it was not;
      * nothing is written at all when apply_to does not match, so a failed
        apply does not leave an orphan profile behind.

    Not routed through _settings_addprofile: that handler reads request.form
    off the global, so it is not callable without faking a request context.
    """
    body = json_body()

    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        return error("bad_request", 400, field="name")

    coefficients = {}
    for key in ("a", "b", "c"):
        value = body.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return error("bad_request", 400, field=key)
        coefficients[key] = float(value)

    apply_to = body.get("apply_to")
    if apply_to is not None and not isinstance(apply_to, str):
        return error("bad_request", 400, field="apply_to")

    settings = read_settings()
    probe_info = settings["probe_settings"]["probe_map"]["probe_info"]
    target = None
    if apply_to:
        target = next((i for i, p in enumerate(probe_info) if p["label"] == apply_to), None)
        if target is None:
            #  Refused BEFORE the profile is stored, so a bad label cannot
            #  leave an orphan behind.
            return error("not_found", 404, field="apply_to")

    profile_id = generate_uuid()
    profile = {
        "A": coefficients["a"],
        "B": coefficients["b"],
        "C": coefficients["c"],
        "name": name.strip(),
        "id": profile_id,
    }
    settings["probe_settings"]["probe_profiles"][profile_id] = profile
    if target is not None:
        probe_info[target]["profile"] = profile
    write_settings(settings)

    return jsonify(
        api_response("OK", None, {"id": profile_id, "applied": apply_to if target is not None else None})
    ), 200
