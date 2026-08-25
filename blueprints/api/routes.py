import json
import math
import time
from typing import get_args

from flask import Response, abort, jsonify, request
from pydantic import ValidationError

from blueprints.api.probe_map_actions import (
    module_requires_install,
    unsupported_new_modules,
)
from common.api_commands import mpc_calibration_command_revision, process_command
from common.app import api_response, create_ui_hash, get_system_command_output, save_settings_and_flag_update
from common.common import read_generic_json, read_wizard, write_log
from common.control_delta import ControlDeltaError, control_delta, notify_ops_from_post
from common.controller_deps import guard_controller_selection
from common.defaults import set_probe_map
from common.i2c_bus import (
    I2CBusConfigError,
    configured_bus_kinds,
    validate_bus_kinds,
)
from common.modes import Mode
from common.pellets_actions import dispatch_pellet_action
from common.persistence.control import (
    enqueue_control_delta,
    read_control,
)
from common.persistence.runtime import (
    clear_warnings_through,
    read_current,
    read_pellet_db,
    read_probe_status,
    read_settings,
    read_status,
    write_settings,
)
from common.server_status import get_server_status
from common.settings_schema import (
    SettingsValidationError,
    apply_settings_delta,
    format_validation_pairs,
    validate_partial_settings_pairs,
)
from common.web_contracts.control import (
    ControlPatchRequest,
    ControlPatchResponse,
    NotifyListResponse,
    PelletRestResponse,
    WledActionResponse,
    WledDiscoverResponse,
    WledPushProfilesRequest,
    WledTestProfileRequest,
)
from common.web_contracts.core import (
    CommandResponse,
    ControlHealthResponse,
    DismissWarningsRequest,
    DismissWarningsResponse,
)
from common.web_contracts.learning import (
    ModelActionRejected,
    ModelActivationAccepted,
    ModelActivationRequest,
    ModelEvidenceReport,
    ModelRollbackAccepted,
    ModelRollbackRequest,
    MpcCalibrationCommand,
    MpcCalibrationCommandResponse,
    PidSpLearningReport,
)
from common.web_contracts.settings import (
    ControllerCatalog,
    ModeResponse,
    SettingsFlag,
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
)
from common.web_contracts.wizard import (
    ProbeMapApplyData,
    ProbeMapApplyResponse,
    ProbeMapErrorResponse,
    ProbeMapRequest,
    ProbeModuleCatalog,
)
from controller.model_learning.activation_service import (
    ActivationAccepted,
    ActivationRejected,
    ActivationRejectionCategory,
    ModelActivationService,
    RollbackAccepted,
    RollbackRejected,
    RollbackRejectionCategory,
)
from controller.model_learning.report import backend_learning_report, build_learning_artifact
from controller.pid_sp_learning import backend_pid_sp_learning_report

from . import api_bp


def _build_current_status(settings, control, display, probe_status):
    status = {}
    status["mode"] = control["mode"]
    status["display_mode"] = display["mode"]
    status["status"] = control["status"]
    status["s_plus"] = control["s_plus"]
    status["units"] = settings["globals"]["units"]
    status["name"] = settings["globals"]["grill_name"]
    status["start_time"] = display["start_time"]
    status["start_duration"] = display["start_duration"]
    status["shutdown_duration"] = display["shutdown_duration"]
    status["prime_duration"] = display["prime_duration"]
    status["prime_amount"] = display["prime_amount"]
    status["lid_open_detected"] = display["lid_open_detected"]
    status["lid_open_endtime"] = display["lid_open_endtime"]
    status["p_mode"] = display["p_mode"]
    status["outpins"] = display["outpins"]
    status["startup_timestamp"] = display["startup_timestamp"]
    status["ui_hash"] = create_ui_hash()
    status["probe_status"] = probe_status
    status["critical_error"] = control.get("critical_error", False)
    return status


def _api_get_settings(settings, server_status):
    payload = SettingsResponse.model_validate({"settings": settings}, strict=True)
    return jsonify(payload.model_dump(mode="json", by_alias=True)), 201


def _api_get_server(settings, server_status):
    return jsonify({"server_status": server_status}), 201


def _api_get_control(settings, server_status):
    control = read_control()
    return jsonify({"control": control}), 201


def _api_get_current(settings, server_status):
    """Only fetch data from the datastore or locally available, to improve performance"""
    current_temps = read_current()  # Get current temperatures
    control = read_control()  # Get status of control
    display = read_status()  # Get status of display items
    probe_status = read_probe_status(settings["probe_settings"]["probe_map"]["probe_info"])

    """ Create string of probes that can be hashed to ensure UI integrity """
    probe_string = ""
    for group in current_temps:
        if group in ["P", "F"]:
            for probe in current_temps[group]:
                probe_string += probe
    probe_string += settings["globals"]["units"]

    notify_data = control["notify_data"]

    status = _build_current_status(settings, control, display, probe_status)
    return jsonify({"current": current_temps, "notify_data": notify_data, "status": status}), 201


def _api_get_hopper(settings, server_status):
    pelletdb = read_pellet_db()
    pelletlevel = pelletdb["current"]["hopper_level"]
    pelletid = pelletdb["current"]["pelletid"]
    pellets = f"{pelletdb['archive'][pelletid]['brand']} {pelletdb['archive'][pelletid]['wood']}"
    return jsonify({"hopper_level": pelletlevel, "hopper_pellets": pellets})


def _api_get_pellets(settings, server_status):
    """Whole pellet database over REST.

    The live UI reads this over socket_pellet_data (socket_io.py:174, :224);
    this route exists so a test can assert store state without going through
    the UI it is testing, and so a client with no socket can cold-start.
    """
    payload = PelletRestResponse.model_validate(
        {
            "result": "OK",
            "message": None,
            "data": {"uuid": settings["server_info"]["uuid"], "pellets": read_pellet_db()},
        },
        strict=True,
    )
    return jsonify(payload.model_dump(mode="json", by_alias=True, exclude_none=False)), 200


def _api_get_wled_discover(settings, server_status):
    """Discover WLED devices on the network (mDNS/zeroconf, in-process)"""
    try:
        # Imported lazily so zeroconf is only loaded when discovery runs.
        # Runs in-process now that the webapp uses the gthread worker (no
        # eventlet/gevent monkey-patching), so no subprocess is needed.
        from notify.wled_discovery import discover_wled_devices

        # Get timeout from query parameter, default to 10 seconds
        timeout = request.args.get("timeout", 10, type=int)
        timeout = max(5, min(30, timeout))  # Clamp between 5-30 seconds

        devices = discover_wled_devices(timeout)
        payload = WledDiscoverResponse.model_validate(
            {
                "result": "success",
                "message": f"Found {len(devices)} WLED devices",
                "devices": devices,
            },
            strict=True,
        )
        return jsonify(payload.model_dump(mode="json", by_alias=True, exclude_unset=True)), 200

    except Exception as e:
        payload = WledDiscoverResponse(
            result="error",
            message=f"WLED discovery failed: {e!s}",
            devices=[],
        )
        return jsonify(payload.model_dump(mode="json", by_alias=True, exclude_unset=True)), 500


def _api_get_controller_metadata(settings, server_status):
    payload = ControllerCatalog.model_validate(
        read_generic_json("./controller/controllers.json"),
        strict=True,
    )
    return jsonify(payload.model_dump(mode="json", by_alias=True)), 201


def _api_get_probe_modules(settings, server_status):
    """The probes section of wizard/wizard_manifest.json, plus a per-module
    "would this need the wizard's installer?" flag.

    GET /api/wizard/state ships the same manifest slice (api_wizard/routes.py:133)
    but also computes draft resumption, board reseed maps and first_time_setup,
    and its probe_map is the DRAFT map -- the wrong source for an editor that
    edits LIVE settings. This route is the manifest and nothing else.
    """
    modules = read_wizard().get("modules", {}).get("probes", {})
    catalog = ProbeModuleCatalog.model_validate(
        {
            "modules": modules,
            "requires_install": {key: module_requires_install(mod) for key, mod in modules.items()},
        },
        strict=True,
    )
    return jsonify(
        api_response(
            result="OK",
            data=catalog.model_dump(mode="json", by_alias=True, exclude_unset=True),
        )
    ), 200


def _model_evidence_projection():
    return backend_learning_report()


@api_bp.get("/model-evidence/report")
def api_model_evidence_report():
    """Return the current read-only confidence projection, including empty state."""
    try:
        report, _records = _model_evidence_projection()
        payload = ModelEvidenceReport.model_validate_json(
            json.dumps(report.as_dict(), allow_nan=False),
            strict=True,
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"error": "model-evidence-report-invalid", "detail": str(exc)}), 422
    return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 200


@api_bp.get("/pid-sp-learning/report")
def api_pid_sp_learning_report():
    """Return the current read-only PID-SP learning projection."""

    try:
        report = backend_pid_sp_learning_report()
        payload = PidSpLearningReport.model_validate_json(
            json.dumps(report.as_dict(), allow_nan=False),
            strict=True,
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"error": "pid-sp-learning-report-invalid", "detail": str(exc)}), 422
    return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 200


@api_bp.get("/model-evidence/artifact")
def api_model_evidence_artifact():
    """Return canonical evidence bytes without granting model-state authority."""
    try:
        report, records = _model_evidence_projection()
        artifact = build_learning_artifact(report, records)
    except (TypeError, ValueError) as exc:
        return jsonify({"error": "model-evidence-artifact-invalid", "detail": str(exc)}), 422
    return Response(artifact, status=200, content_type="application/json; charset=utf-8")


def _model_action_rejection(reason: str, status: int):
    payload = ModelActionRejected(
        accepted=False,
        active_kind="grey-box",
        error="model-activation-rejected",
        detail=reason,
    )
    return jsonify(payload.model_dump(mode="json")), status


_ACTIVATION_REJECTION_STATUS = {
    ActivationRejectionCategory.CONFLICT: 409,
    ActivationRejectionCategory.INVALID_DATA: 422,
    ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE: 503,
    ActivationRejectionCategory.CLEANUP_FAILED: 503,
}

_ROLLBACK_REJECTION_STATUS = {
    RollbackRejectionCategory.CONFLICT: 409,
    RollbackRejectionCategory.PERSISTENCE_UNAVAILABLE: 503,
}


@api_bp.post("/model-evidence/activate")
def api_model_evidence_activate():
    """Durably prepare the exact reviewed grey pair; runtime alone may activate it."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"candidate_digest", "decision_id"}:
        return _model_action_rejection(
            "request must contain exactly candidate_digest and decision_id",
            422,
        )
    try:
        activation_request = ModelActivationRequest.model_validate(body, strict=True)
    except ValidationError as error:
        return _model_action_rejection(str(error), 422)

    outcome = ModelActivationService().activate(
        activation_request,
        now_ms=int(time.time() * 1_000),
    )
    if isinstance(outcome, ActivationRejected):
        return _model_action_rejection(
            outcome.reason,
            _ACTIVATION_REJECTION_STATUS[outcome.category],
        )
    assert isinstance(outcome, ActivationAccepted)
    payload = ModelActivationAccepted(
        accepted=True,
        phase="prepared",
        transaction_id=outcome.transaction_id,
        decision_id=outcome.decision_id,
        candidate_digest=outcome.candidate_digest,
        role_generation=outcome.role_generation,
    )
    return jsonify(payload.model_dump(mode="json")), 200


@api_bp.post("/model-evidence/rollback")
def api_model_evidence_rollback():
    """Record an explicit operator rollback reason for immediate runtime fallback."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"reason"}:
        return _model_action_rejection("request must contain exactly reason", 422)
    try:
        rollback_request = ModelRollbackRequest.model_validate(body, strict=True)
    except ValidationError as error:
        return _model_action_rejection(str(error), 422)

    outcome = ModelActivationService().rollback(
        rollback_request,
        now_ms=int(time.time() * 1_000),
    )
    if isinstance(outcome, RollbackRejected):
        return _model_action_rejection(
            outcome.reason,
            _ROLLBACK_REJECTION_STATUS[outcome.category],
        )
    assert isinstance(outcome, RollbackAccepted)
    payload = ModelRollbackAccepted(
        accepted=True,
        active_kind="grey-box",
        decision_id=outcome.decision_id,
        reason=outcome.reason,
        role_generation=outcome.role_generation,
        rollback_digest=outcome.rollback_digest,
    )
    return jsonify(payload.model_dump(mode="json")), 200


_API_GET_ACTIONS = {
    "settings": _api_get_settings,
    "server": _api_get_server,
    "control": _api_get_control,
    "current": _api_get_current,
    "hopper": _api_get_hopper,
    "pellets": _api_get_pellets,
    "wled_discover": _api_get_wled_discover,
    "controller_metadata": _api_get_controller_metadata,
    "probe_modules": _api_get_probe_modules,
}


def _api_post_settings(settings, request_json):
    try:
        settings = apply_settings_delta(settings, request_json)
        write_settings(settings)
        return jsonify(
            {
                "settings": "success",  # Keeping for compatibility
                "result": "success",
                "message": "Settings updated successfully.",
            }
        ), 201
    except Exception:
        return jsonify(
            {
                "settings": "error",  # Keeping for compatibility
                "result": "error",
                "message": "Settings update failed.",
            }
        ), 201


_SETTINGS_UPDATE_ALLOWED_FLAGS = frozenset(get_args(SettingsFlag.__value__))


def _settings_update_response(payload: dict):
    response = SettingsUpdateResponse.model_validate(payload, strict=True)
    return jsonify(response.model_dump(mode="json", by_alias=True)), 200


def _api_post_settings_update(settings, request_json):
    """
    JSON settings write that ALSO sets control-update flags so the running
    control loop re-reads. Mirrors save_settings_and_flag_update.
    body: { "settings": <partial settings dict>, "flags": ["settings_update", ...] }

    `flags` is validated by SettingsUpdateRequest against the Pydantic-owned
    SettingsFlag literal. An unknown flag (for example a typo'd "mode") would
    otherwise be set as control[flag] = True and clobber unrelated control
    keys, so the request is rejected before writing settings or control.

    Two-layer rejection:
      1. The DELTA itself is FIELD-level strict-validated against
         PartialSettingsSchema before anything is touched -- catches a
         structurally-bad delta (e.g. a section replaced with a scalar, or a
         field of the wrong type) with a precise dotted-path message, same
         format as SettingsValidationError.errors. This layer deliberately
         does NOT enforce cross-field/cross-section model_validator rules
         (e.g. startup.pwm_duty_cycle vs. pwm.min/max_duty_cycle) -- on a
         sparse delta those would run against sections' STATIC DEFAULTS
         rather than the store's real values and could falsely reject a
         valid delta (see validate_partial_settings()'s docstring in
         common/settings_schema.py for the full rationale + discriminator).
      2. The delta is merged onto the current tree and handed to
         save_settings_and_flag_update() -> write_settings(), which
         strict-validates the FULL merged tree and raises
         SettingsValidationError on any violation -- including the
         cross-field constraints Layer 1 skips, now checked against real
         values everywhere. Caught here and turned into the same error
         envelope; the store is left untouched (write_settings() validates
         before persisting). This layer is authoritative.

    The envelope's `errors` key carries `{"path", "message"}` pairs alongside
    the joined `message` string, so a client can route each failure to the
    field that caused it. A rejection that is not about any field (unknown
    flag, blocked controller selection, an unexpected exception) sends an
    empty list rather than an invented path.
    """
    if isinstance(request_json, dict):
        raw_flags = request_json.get("flags", []) or []
        request_json = {**request_json, "flags": raw_flags}
        if isinstance(raw_flags, list):
            for flag in raw_flags:
                if isinstance(flag, str) and flag not in _SETTINGS_UPDATE_ALLOWED_FLAGS:
                    return _settings_update_response(
                        {
                            "result": "error",
                            "message": f"Unknown flag: {flag}",
                            "errors": [],
                            "data": {},
                        }
                    )
    try:
        update = SettingsUpdateRequest.model_validate(request_json, strict=True)
    except ValidationError as exc:
        pairs = format_validation_pairs(exc)
        message = "; ".join(f"{pair['path']}: {pair['message']}" for pair in pairs)
        return _settings_update_response(
            {
                "result": "error",
                "message": f"Settings update failed: {message}",
                "errors": pairs,
                "data": {},
            }
        )
    delta = update.settings
    flags = update.flags
    layer1_pairs = validate_partial_settings_pairs(delta)
    if layer1_pairs:
        message = "; ".join(f"{p['path']}: {p['message']}" for p in layer1_pairs)
        return _settings_update_response(
            {
                "result": "error",
                "message": f"Settings update failed: {message}",
                "errors": layer1_pairs,
                "data": {},
            }
        )

    try:
        settings = apply_settings_delta(settings, delta)
        # Layer 3: the selected controller must be constructible on THIS install.
        # Evaluated on the MERGED tree (so it sees the selection the save would
        # actually produce) and before any write, so a refusal leaves the store
        # untouched exactly like the two layers above -- the controller currently
        # running the user's cook is not disturbed. Kicks off the missing extra's
        # background install; see common/controller_deps.py.
        blocked = guard_controller_selection(settings)
        if blocked:
            return _settings_update_response({"result": "error", "message": blocked, "errors": [], "data": {}})
        control = read_control()
        save_settings_and_flag_update(settings, control, *flags, origin="api")
        return _settings_update_response(
            {
                "result": "success",
                "message": "Settings updated.",
                "errors": [],
                "data": settings,
            }
        )
    except SettingsValidationError as exc:
        message = "; ".join(exc.errors)
        return _settings_update_response(
            {
                "result": "error",
                "message": f"Settings update failed: {message}",
                "errors": exc.pairs,
                "data": {},
            }
        )
    except Exception as e:
        return _settings_update_response(
            {
                "result": "error",
                "message": f"Settings update failed: {e}",
                "errors": [],
                "data": {},
            }
        )


def _api_post_control(settings, request_json):
    """Queue a client-supplied control patch as a delta.

    A posted patch is ALREADY a statement of intent -- the client sent only what
    it means -- so it needs no reduction and no client change; it is wrapped, not
    rewritten. Three members are special, all handled by notify_ops_from_post()
    so this door and the Socket.IO one cannot drift:

      * `notify_updates` is the way to change notifications: one patch per
        addressed entry ({label, type, fields}), applied against live state, so
        a concurrent writer touching a different entry or field survives;
      * `notify_data` travels WHOLE (an omitted entry is a deletion, not
        silence), so it becomes an explicit notify.replace op rather than an
        implicit array swap. It cannot express which fields the client meant,
        so it CLOBBERS a concurrent write to the same array -- retained only
        for clients that already speak it; post `notify_updates` instead;
      * `timer` is refused. start/paused/end are one countdown and the control
        code branches on their combinations, so a value computed from a read
        that cannot see the write queue is exactly the race this endpoint used
        to feed. Use /api/set/timer/{start,pause,stop}, which queue ops the
        drain resolves against live state.
    """
    if "timer" in request_json:
        return jsonify(
            {
                "control": "error",
                "result": "error",
                "message": "control['timer'] cannot be set through /api/control; use /api/set/timer/...",
            }
        ), 400
    try:
        patch = ControlPatchRequest.model_validate(request_json, strict=True)
    except ValidationError as exc:
        payload = ControlPatchResponse(control="error", result="error", message=str(exc))
        return jsonify(payload.model_dump(mode="json", by_alias=True)), 400
    try:
        members, ops = notify_ops_from_post(patch.model_dump(mode="python", exclude_unset=True))
        enqueue_control_delta(control_delta(set_values=members, ops=ops), origin="app")
        payload = ControlPatchResponse(
            control="success",
            result="success",
            message="Settings updated successfully.",
        )
        return jsonify(payload.model_dump(mode="json", by_alias=True)), 201
    except ControlDeltaError as exc:
        payload = ControlPatchResponse(control="error", result="error", message=str(exc))
        return jsonify(payload.model_dump(mode="json", by_alias=True)), 400
    except Exception:
        payload = ControlPatchResponse(control="error", result="error", message="Settings update failed.")
        return jsonify(payload.model_dump(mode="json", by_alias=True)), 201


def _api_post_wled_push_profiles(settings, request_json):
    """Push PiFire profiles to WLED device"""
    try:
        from notify.wled_profiles import WLEDProfileManager

        raw_address = request_json.get("device_address", "")
        if not isinstance(raw_address, str) or not raw_address.strip():
            payload = WledActionResponse(result="error", message="Device address is required")
            return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 400
        try:
            request_payload = WledPushProfilesRequest.model_validate(request_json, strict=True)
        except ValidationError:
            payload = WledActionResponse(result="error", message="Profile numbers must be integers")
            return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 400

        profile_manager = WLEDProfileManager(request_payload.device_address.strip(), settings)
        result = profile_manager.push_all_profiles(custom_profile_numbers=request_payload.profile_numbers)

        if result["success"]:
            payload = WledActionResponse(
                result="success",
                message=f"Successfully pushed {result['profiles_pushed']} profiles",
                profiles_pushed=result["profiles_pushed"],
                profiles=result["profiles"],
            )
            return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 200
        payload = WledActionResponse(result="error", message=result["message"])
        return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 500

    except Exception as e:
        payload = WledActionResponse(result="error", message=f"Failed to push profiles: {e!s}")
        return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 500


def _api_post_wled_test_profile(settings, request_json):
    """Test a WLED profile"""
    import requests

    try:
        raw_address = request_json.get("device_address", "")
        if not isinstance(raw_address, str) or not raw_address.strip():
            payload = WledActionResponse(result="error", message="Device address is required")
            return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 400
        try:
            request_payload = WledTestProfileRequest.model_validate(request_json, strict=True)
        except ValidationError:
            payload = WledActionResponse(result="error", message="Profile number must be an integer")
            return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 400

        device_address = request_payload.device_address
        if "http://" in device_address:
            device_address = device_address.replace("http://", "")
        if "https://" in device_address:
            device_address = device_address.replace("https://", "")
        device_address = device_address.strip().rstrip("/")

        url = f"http://{device_address}/json/state"
        request_body = {"on": True, "bri": 128, "ps": request_payload.profile_number}
        response = requests.post(url, json=request_body, timeout=5)
        response.raise_for_status()

        payload = WledActionResponse(
            result="success",
            message=f"Profile {request_payload.profile_number} activated successfully",
        )
        return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 200

    except requests.RequestException as e:
        payload = WledActionResponse(
            result="error",
            message=f"Failed to communicate with WLED device: {e!s}",
        )
        return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 500
    except Exception as e:
        payload = WledActionResponse(result="error", message=f"Failed to test profile: {e!s}")
        return jsonify(payload.model_dump(mode="json", exclude_unset=True)), 500


def _api_post_pellets(settings, request_json):
    """One pellet action per request. body: {"action": <name>, "data": {...}}

    The action name travels in the BODY, not the path: api_page's POST branch
    calls handler(settings, request_json) and never forwards arg0.

    INTENT ONLY -- see common/pellets_actions.py's module docstring. This route
    must never grow a "here is the whole pellet database" form.
    """
    pelletdb = read_pellet_db()
    response = dispatch_pellet_action(
        pelletdb,
        request_json.get("action"),
        request_json.get("data") or {},
        invalid_action_message="Error: Received request without valid action",
    )
    return jsonify(response), 200


def _api_post_probe_map(settings, request_json):
    """Apply a whole probe map to LIVE settings.

    Whole-map, not intent-based, and that is deliberate (unlike
    _api_post_pellets above): a probe map is one interdependent graph --
    probe_info[].device references probe_devices[].device, virtual devices'
    config.probes_list references probe_info[].label, and the reposition
    invariant depends on probe_info ORDER. No per-item intent vocabulary
    expresses "this entry sorts after that one" without re-sending the order.

    Four guards, in the order a rejection is cheapest:
      1. shape          -> 400, nothing read
      2. control mode   -> 409, mirrors /api/wizard/finish (api_wizard:406)
      3. new modules    -> 422, the installer is the only thing that can
                           install dependencies and it does not run here
      4. bus kinds      -> 422, FULL cross-subsystem (settings passed, not
                           None) because this is live config, not a draft
    Only after all four does anything get written.
    """
    try:
        request_payload = ProbeMapRequest.model_validate(request_json, strict=True)
    except ValidationError:
        response = ProbeMapErrorResponse(result="error", message="bad_probe_map")
        return jsonify(response.model_dump(mode="json", by_alias=True, exclude_unset=True)), 400
    probe_map = request_payload.probe_map.model_dump(mode="json", by_alias=True, exclude_unset=True)

    control = read_control()
    if control.get("mode") != Mode.STOP:
        response = ProbeMapErrorResponse(result="error", message="system_active")
        return jsonify(response.model_dump(mode="json", by_alias=True, exclude_unset=True)), 409

    manifest_modules = read_wizard().get("modules", {}).get("probes", {})
    live_map = settings["probe_settings"]["probe_map"]
    offenders = unsupported_new_modules(probe_map, live_map, manifest_modules)
    if offenders:
        response = ProbeMapErrorResponse(
            result="error",
            message="modules_require_install",
            modules=offenders,
        )
        return jsonify(response.model_dump(mode="json", by_alias=True, exclude_unset=True)), 422

    try:
        validate_bus_kinds(configured_bus_kinds(settings, probe_map))
    except I2CBusConfigError as exc:
        response = ProbeMapErrorResponse(
            result="error",
            message="bus_conflict",
            detail=str(exc),
        )
        return jsonify(response.model_dump(mode="json", by_alias=True, exclude_unset=True)), 422

    settings = set_probe_map(settings, probe_map, control)
    # settings_update makes the loop re-read settings; probe_map_update is what
    # makes it REBUILD its probe devices (controller.py). probe_profile_update is
    # NOT enough on its own -- it only refills per-port profiles on
    # already-constructed devices (probes/base.py:393). notify_data travels as a
    # notify.replace op because set_probe_map() may have dropped entries, and "an
    # entry the incoming array omits is a deletion" is exactly what replace says
    # out loud.
    save_settings_and_flag_update(
        settings,
        control,
        "settings_update",
        "probe_map_update",
        origin="api",
        ops=[{"op": "notify.replace", "entries": control["notify_data"]}],
    )
    response = ProbeMapApplyResponse(
        result="success",
        message="Probe map applied.",
        data=ProbeMapApplyData(probe_map=request_payload.probe_map),
    )
    return jsonify(response.model_dump(mode="json", by_alias=True, exclude_unset=True)), 200


def _api_post_dismiss_warnings(settings, request_json):
    """Clear the warnings the client was showing, and only those.

    The client posts back the high-water mark it received with the banner
    (socket_dash_data's warningsMaxId), so a warning written between that
    payload and the click keeps a larger id and survives the clear.
    """
    try:
        request_payload = DismissWarningsRequest.model_validate(request_json, strict=True)
    except ValidationError:
        response = DismissWarningsResponse(
            result="ERROR",
            message="through_id must be an integer.",
            data=None,
        )
        return jsonify(response.model_dump(mode="json", by_alias=True, exclude_none=False)), 400
    clear_warnings_through(request_payload.through_id)
    response = DismissWarningsResponse(
        result="OK",
        message="Warnings dismissed.",
        data=None,
    )
    return jsonify(response.model_dump(mode="json", by_alias=True, exclude_none=False)), 200


def _api_post_mpc_calibration(settings, request_json):
    """Dispatch the body-only calibration command through the standard command guard."""
    try:
        request_payload = MpcCalibrationCommand.model_validate(request_json, strict=True)
    except ValidationError as error:
        response = MpcCalibrationCommandResponse.model_validate(
            {
                "result": "ERROR",
                "message": str(error),
                "data": {},
            },
            strict=True,
        )
        return jsonify(response.model_dump(mode="json", by_alias=True, exclude_none=False)), 422
    data = process_command(
        "set",
        ["mpc_calibration", request_payload.model_dump(mode="json")],
        origin="api",
    )
    response = MpcCalibrationCommandResponse.model_validate(data, strict=True)
    return (
        jsonify(response.model_dump(mode="json", by_alias=True, exclude_none=False)),
        201 if response.result == "OK" else 400,
    )


_API_POST_ACTIONS = {
    "settings": _api_post_settings,
    "settings_update": _api_post_settings_update,
    "control": _api_post_control,
    "pellets": _api_post_pellets,
    "probe_map": _api_post_probe_map,
    "wled_push_profiles": _api_post_wled_push_profiles,
    "wled_test_profile": _api_post_wled_test_profile,
    "dismiss_warnings": _api_post_dismiss_warnings,
    "set_mpc_calibration": _api_post_mpc_calibration,
}


@api_bp.route("/", methods=["POST", "GET"])
@api_bp.route("/<action>", methods=["POST", "GET"])
@api_bp.route("/<action>/<arg0>", methods=["POST", "GET"])
@api_bp.route("/<action>/<arg0>/<arg1>", methods=["POST", "GET"])
@api_bp.route("/<action>/<arg0>/<arg1>/<arg2>", methods=["POST", "GET"])
@api_bp.route("/<action>/<arg0>/<arg1>/<arg2>/<arg3>", methods=["POST", "GET"])
def api_page(action=None, arg0=None, arg1=None, arg2=None, arg3=None):
    settings = read_settings()
    # Get current server status
    server_status = get_server_status()

    #  `cmd` reboots, shuts down and restarts the machine. This branch runs
    #  BEFORE the method split below, so every one of those was reachable by a
    #  bare GET -- no body, no confirmation, no CSRF token, which makes any
    #  link, prefetch, crawler or <img src> pointing here enough to power the
    #  box off. Only `cmd` is narrowed: `get` is read-only and the mobile app
    #  polls it over GET, and narrowing `set`/`sys` is a separate decision.
    if action == "cmd" and request.method != "POST":
        return jsonify({"Error": "Method Not Allowed. /api/cmd/* requires POST."}), 405

    if action == "set" and arg0 == "mpc_calibration":
        return jsonify(api_response("ERROR", "Use POST /api/set_mpc_calibration with a JSON command body.")), 400

    if action in ["get", "set", "cmd", "sys"]:
        # print(f'action={action}\narg0={arg0}\narg1={arg1}\narg2={arg2}\narg3={arg3}')
        arglist = []
        arglist.extend([arg0, arg1, arg2, arg3])

        data = process_command(action=action, arglist=arglist, origin="api")

        if action == "sys":
            """ If system command, wait for output from control """
            data = get_system_command_output(requested=arg0)
        if action in {"set", "cmd"}:
            data = CommandResponse.model_validate(data, strict=True).model_dump(
                mode="json",
                by_alias=True,
                exclude_none=False,
            )
        elif action == "sys" and arg0 == "check_alive":
            data = ControlHealthResponse.model_validate(data, strict=True).model_dump(
                mode="json",
                by_alias=True,
                exclude_none=False,
            )
        elif action == "get" and arg0 == "mode":
            data = ModeResponse.model_validate(data, strict=True).model_dump(
                mode="json",
                by_alias=True,
                exclude_none=False,
            )
        elif action == "get" and arg0 == "notify":
            data = NotifyListResponse.model_validate(data, strict=True).model_dump(
                mode="json",
                by_alias=True,
                exclude_unset=True,
            )

        return jsonify(data), 201

    elif request.method == "GET":
        handler = _API_GET_ACTIONS.get(action)
        if handler is not None:
            return handler(settings, server_status)
        return jsonify({"Error": "Received GET request, without valid action"}), 404

    elif request.method == "POST":
        if not request.json:
            event = "Local API Call Failed"
            write_log(event)
            abort(400)
        else:
            request_json = request.json
            handler = _API_POST_ACTIONS.get(action)
            if handler is not None:
                return handler(settings, request_json)
            return jsonify({"Error": "Received POST request no valid action."}), 404
    else:
        return jsonify({"Error": "Received undefined/unsupported request."}), 404
