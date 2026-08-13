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

from pydantic import ValidationError

from common.app import api_response
from common.common import generate_uuid
from common.control_delta import control_delta
from common.persistence.control import (
    read_control,
    enqueue_control_delta,
)
from common.persistence.runtime import (
    read_current_snapshot,
    read_settings,
    write_settings,
)
from common.persistence.history import (
    autotune_length,
    flush_autotune,
    read_autotune,
    read_tr,
    write_autotune,
)
from common.modes import Mode

from blueprints.tuner.tuner import calc_auto_tune_status, calc_shh_chart, calc_shh_coefficients
from common.web_contracts.operations import (
    AutoStatus,
    AutoStatusRequest,
    Coefficients,
    CoefficientsRequest,
    ProfileInput,
    SavedProfile,
    TrReading,
    TunerSession,
    TunerSessionRequest,
    dump_error_data,
    dump_wire,
)

from . import api_tuner_bp

SEGMENTS = ("High", "Medium", "Low")

#: The only two modes a tuning session may be opened from. Stop is the normal
#: case; Monitor is allowed because the session itself puts the grill there,
#: which makes re-opening after a reload a no-op rather than a refusal.
TUNABLE_MODES = (Mode.STOP, Mode.MONITOR)


def error(message, status, **data):
    details = dump_error_data(data) if data else None
    return jsonify(api_response("Error", message, details)), status


def json_body():
    return request.get_json(silent=True)


def validate_json(model, *, fallback_field=None):
    try:
        return model.model_validate(json_body(), strict=True), None
    except ValidationError as exc:
        location = exc.errors()[0]["loc"]
        field = next((part for part in location if isinstance(part, str)), fallback_field)
        return None, error("bad_request", 400, field=field or fallback_field)


def set_control(**values):
    enqueue_control_delta(control_delta(set_values=values), origin="api-tuner")


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
    payload, invalid = validate_json(TunerSessionRequest, fallback_field="open")
    if payload is None:
        assert invalid is not None
        return invalid

    if payload.open:
        refusal = require_tunable()
        if refusal:
            return refusal
        control = read_control()
        moved = control.get("mode") == Mode.STOP
        values: dict[str, object] = {"tuning_mode": True}
        if moved:
            values.update({"mode": Mode.MONITOR, "updated": True})
        set_control(**values)
        #  Start every tuning session from an empty autotune store. Flask
        #  flushed on the first auto-status poll -- the moment it enabled tuning
        #  mode -- which is this call now. Manual tuning never reads this queue,
        #  so the flush is a no-op there.
        flush_autotune()
        data = dump_wire(TunerSession, {"open": True, "mode": Mode.MONITOR, "restored": moved})
        return jsonify(api_response("OK", None, data)), 200

    control = read_control()
    restored = control.get("mode") == Mode.MONITOR
    values: dict[str, object] = {"tuning_mode": False}
    if restored:
        values.update({"mode": Mode.STOP, "updated": True})
    set_control(**values)
    mode_after = Mode.STOP if restored else control.get("mode")
    data = dump_wire(TunerSession, {"open": False, "mode": mode_after, "restored": restored})
    return jsonify(api_response("OK", None, data)), 200


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
    data = dump_wire(
        TrReading,
        {
            "probe": probe,
            "trohms": readings.get(probe),
            # A reading taken outside a session is stale: control.py only
            # refreshes this blob in tuning mode.
            "tuning": bool(control.get("tuning_mode")),
        },
    )
    return jsonify(api_response("OK", None, data)), 200


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
    payload, invalid = validate_json(CoefficientsRequest, fallback_field="points")
    if payload is None:
        assert invalid is not None
        return invalid
    by_segment = {point.segment: (float(point.temp), float(point.trohms)) for point in payload.points}

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
    data = dump_wire(
        Coefficients,
        {"a": a, "b": b, "c": c, "chart": chart, "chart_ok": bool(chart)},
    )
    return jsonify(api_response("OK", None, data)), 200


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
    payload, invalid = validate_json(ProfileInput, fallback_field="name")
    if payload is None:
        assert invalid is not None
        return invalid
    coefficients = {"a": float(payload.a), "b": float(payload.b), "c": float(payload.c)}
    apply_to = payload.apply_to

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
        "name": payload.name,
        "id": profile_id,
    }
    settings["probe_settings"]["probe_profiles"][profile_id] = profile
    if target is not None:
        probe_info[target]["profile"] = profile
    write_settings(settings)

    data = dump_wire(
        SavedProfile,
        {"id": profile_id, "applied": apply_to if target is not None else None},
    )
    return jsonify(api_response("OK", None, data)), 200


def _reference_temp(current, reference):
    """The reference probe's temperature, or None if it is not reporting.

    Probes are checked primary, then food, then aux, matching Flask. None (not
    -1): a probe absent from every group is not reporting, which the client
    renders as "waiting" -- distinct from a real reading that happens to be
    zero.
    """
    for values in (current.primary, current.food, current.aux):
        if reference in values:
            return values[reference]
    return None


@api_tuner_bp.route("/auto-status", methods=["POST"])
def tuner_auto_status():
    """Record one auto-tuning sample and report the derived selection.

    Unlike /tr this is a POST: each poll captures a datapoint. But it writes
    only the autotune QUEUE -- never control -- so the mode-change safety stays
    entirely in the session endpoint. The session's flush-on-open is what makes
    each run start from zero.

    A sample is recorded only when both readings are present and past the
    DS18B20 warm-up guard (Flask's `autotune_length() > 4 or current_temp > 0`),
    so a cold probe's leading zeros do not poison the solve. Once more than ten
    samples span a wide enough temperature range, calc_auto_tune_status
    (unchanged) fills in the high/medium/low points and flips `ready`.
    """
    payload, invalid = validate_json(AutoStatusRequest, fallback_field="probe")
    if payload is None:
        assert invalid is not None
        return invalid
    probe = payload.probe
    reference = payload.reference

    current_tr = read_tr().get(probe)
    current_temp = _reference_temp(read_current_snapshot(), reference)

    #  Record only a complete, warmed-up reading. `current_temp > 0` lets an
    #  early sample through once the probe is live; `autotune_length() > 4`
    #  lets later samples through even at exactly zero, matching Flask.
    if (
        current_tr is not None
        and current_temp is not None
        and current_tr >= 0
        and current_temp >= 0
        and (autotune_length() > 4 or current_temp > 0)
    ):
        write_autotune({"ref_T": current_temp, "probe_Tr": current_tr})

    status = {
        "current_tr": current_tr,
        "current_temp": current_temp,
        "high_tr": 0,
        "high_temp": 0,
        "medium_tr": 0,
        "medium_temp": 0,
        "low_tr": 0,
        "low_temp": 0,
        "ready": False,
    }
    samples = read_autotune()
    if len(samples) > 10:
        settings = read_settings()
        calc_auto_tune_status(samples, settings["globals"]["units"], status)

    status["samples"] = len(samples)
    return jsonify(api_response("OK", None, dump_wire(AutoStatus, status))), 200
