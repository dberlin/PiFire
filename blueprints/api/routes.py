import json
import math
import logging
import time
from typing import get_args

from pydantic import ValidationError
from flask import Response, abort, jsonify, request
from common.common import WriteKind, write_log, read_generic_json, read_wizard
from common.control_delta import ControlDeltaError, control_delta, notify_ops_from_post
from common.datastore_accessors import (
    commit_model_rollback,
    read_settings,
    write_settings,
    read_control,
    write_control,
    read_pellet_db,
    read_current,
    read_status,
    read_probe_status,
    clear_warnings_through,
    read_model_activation,
    read_model_evidence,
)
from common.api_commands import mpc_calibration_command_revision, process_command
from common.app import get_system_command_output, create_ui_hash, save_settings_and_flag_update, api_response
from common.controller_model_state import ControllerModelStore
from common.model_evidence import EvidenceKind, FallbackEvidence, ModelEvidenceRecord, RollbackEvidence
from common.pellets_actions import PELLETS_DISPATCH
from blueprints.api.probe_map_actions import (
    module_requires_install,
    unsupported_new_modules,
)
from common.defaults import set_probe_map
from common.i2c_bus import (
    I2CBusConfigError,
    configured_bus_kinds,
    validate_bus_kinds,
)
from common.modes import Mode
from common.server_status import get_server_status
from common.settings_schema import (
    SettingsValidationError,
    apply_settings_delta,
    format_validation_pairs,
    validate_partial_settings_pairs,
)
from common.controller_deps import guard_controller_selection
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
    PidSpLearningReport,
    MpcCalibrationCommand,
    MpcCalibrationCommandResponse,
)
from common.web_contracts.settings import (
    ControllerCatalog,
    ModeResponse,
    SettingsResponse,
    SettingsFlag,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
)
from common.web_contracts.wizard import (
    ProbeModuleCatalog,
    _ProbeMapApplyData,
    _ProbeMapApplyResponse,
    _ProbeMapErrorResponse,
    _ProbeMapRequest,
)
from controller.model_learning.activation import (
    ActivationManager,
    GreyControlPairDescriptor,
    OwnedGreyControlPair,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.model_learning.report import backend_learning_report, build_learning_artifact
from controller.pid_sp_learning import backend_pid_sp_learning_report
from controller.runtime.model_persistence import ModelPersistenceWorker
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
    return jsonify(
        api_response(
            result="OK",
            data={"uuid": settings["server_info"]["uuid"], "pellets": read_pellet_db()},
        )
    ), 200


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
        return jsonify({"result": "success", "message": f"Found {len(devices)} WLED devices", "devices": devices}), 200

    except Exception as e:
        return jsonify({"result": "error", "message": f"WLED discovery failed: {str(e)}", "devices": []}), 500


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


def _model_activation_configuration(settings):
    selected = settings.get("controller", {}).get("selected")
    config = settings.get("controller", {}).get("config", {}).get(selected)
    if selected != "mpc" or not isinstance(config, dict):
        raise ValueError("MPC must be the selected controller")
    cycle_data = settings.get("cycle_data")
    units = settings.get("globals", {}).get("units")
    if not isinstance(cycle_data, dict) or not isinstance(units, str):
        raise ValueError("controller configuration is incomplete")
    return {
        "controller": selected,
        "config": config,
        "cycle_data": cycle_data,
        "units": units,
    }


def _build_manual_candidate_pair(descriptor: GreyControlPairDescriptor) -> OwnedGreyControlPair:
    """Build an inert pair from the exact reviewed durable configuration."""
    from controller.mpc import Controller as MpcController

    settings = read_settings()
    activation_configuration = _model_activation_configuration(settings)
    configured = activation_configuration["config"]
    if not isinstance(configured, dict):
        raise ValueError("controller configuration is incomplete")
    candidate_config = dict(configured)
    descriptor_config = dict(descriptor.configuration)
    nested = descriptor_config.get("controller_config")
    candidate_config.update(nested if isinstance(nested, dict) else descriptor_config)
    controller = MpcController(
        candidate_config,
        activation_configuration["units"],
        activation_configuration["cycle_data"],
    )
    pair = controller.active_control_pair
    if pair.descriptor.model_digest != descriptor.model_digest:
        pair.close()
        raise ValueError("candidate-digest-changed")
    return OwnedGreyControlPair(descriptor, pair.estimator, pair.solver)


def _manual_candidate_dry_solve(pair: OwnedGreyControlPair) -> bool:
    config = getattr(pair.solver, "config", None)
    state_size = getattr(config, "state_size", None)
    horizon = getattr(config, "horizon_steps", None)
    if not isinstance(state_size, int) or not isinstance(horizon, int):
        return False
    state = getattr(pair.estimator, "state", getattr(pair.estimator, "x", (0.0,) * state_size))
    result = pair.solver.solve(
        state,
        setpoint_c=float(getattr(config, "T_amb", 20.0)) + 50.0,
        q_previous=0.0,
        equilibrium_q=0.4,
    )
    sequence = tuple(result.sequence_q)
    return (
        len(sequence) == horizon
        and math.isfinite(float(result.objective))
        and all(math.isfinite(float(value)) for value in sequence)
    )


def _activation_checkpoint():
    checkpoint = ControllerModelStore().load("mpc")
    if not isinstance(checkpoint, dict):
        raise ValueError("candidate-snapshot-not-found")
    return checkpoint


def _activation_rejection(reason, status=409):
    payload = ModelActionRejected(
        accepted=False,
        active_kind="grey-box",
        error="model-activation-rejected",
        detail=str(reason),
    )
    return jsonify(payload.model_dump(mode="json")), status


@api_bp.post("/model-evidence/activate")
def api_model_evidence_activate():
    """Durably prepare the exact reviewed grey pair; runtime alone may activate it."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"candidate_digest", "decision_id"}:
        return _activation_rejection(
            "request must contain exactly candidate_digest and decision_id",
            422,
        )
    try:
        activation_request = ModelActivationRequest.model_validate(body, strict=True)
    except ValidationError as error:
        return _activation_rejection(str(error), 422)
    try:
        report, _records = _model_evidence_projection()
        projection = report.to_dict()
        if projection["status"] != "ready-for-review":
            raise ValueError("confidence decision is not ready-for-review")
        if projection["candidate"]["digest"] != activation_request.candidate_digest:
            raise ValueError("candidate-digest-changed")
        if projection["candidate"].get("policy") != ActivationPolicy.OPERATOR_REVIEWED.value:
            raise ValueError("manual activation requires operator-reviewed policy")
        if projection["decision_id"] != activation_request.decision_id:
            raise ValueError("stale-confidence-decision")
        checkpoint = _activation_checkpoint()
        incumbent_value = checkpoint.get("active_pair")
        candidate_value = checkpoint.get("candidate_pair")
        if not isinstance(incumbent_value, dict) or not isinstance(candidate_value, dict):
            raise ValueError("candidate-pair-not-found")
        incumbent = GreyControlPairDescriptor.from_dict(incumbent_value)
        candidate = GreyControlPairDescriptor.from_dict(candidate_value)
        if candidate.model_digest != activation_request.candidate_digest:
            raise ValueError("candidate-digest-changed")
    except (KeyError, TypeError, ValueError) as error:
        return _activation_rejection(str(error), 422 if isinstance(error, (KeyError, TypeError)) else 409)

    worker = ModelPersistenceWorker(
        ControllerModelStore(),
        logging.getLogger("control"),
    )
    incumbent_owner = OwnedGreyControlPair(incumbent, object(), object())
    manager = ActivationManager(
        incumbent_pair=incumbent_owner,
        build_candidate=_build_manual_candidate_pair,
        validate_candidate=lambda pair: pair.descriptor == candidate,
        native_dry_solve=_manual_candidate_dry_solve,
        persist_prepared=lambda record: worker.submit_activation_phase(
            record,
            expected_phase=None,
        ),
        receipt_timeout=2.0,
    )
    try:
        decision = manager.prepare(
            activation_request,
            candidate,
            origin=CandidateOrigin.OPERATOR_CALIBRATION,
            policy=ActivationPolicy.OPERATOR_REVIEWED,
        )
    finally:
        worker.flush_and_stop(timeout=2.0)
    if not decision.accepted:
        status = 503 if decision.reason.startswith("activation-persistence") else 409
        return _activation_rejection(decision.reason, status)
    if decision.candidate_pair is not None:
        decision.candidate_pair.close()
    record = decision.record
    assert record is not None
    payload = ModelActivationAccepted(
        accepted=True,
        phase="prepared",
        transaction_id=record.transaction_id,
        decision_id=record.decision_id,
        candidate_digest=record.candidate.model_digest,
        role_generation=record.candidate.role_generation,
    )
    return jsonify(payload.model_dump(mode="json")), 200


@api_bp.post("/model-evidence/rollback")
def api_model_evidence_rollback():
    """Record an explicit operator rollback reason for immediate runtime fallback."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"reason"}:
        return _activation_rejection("request must contain exactly reason", 422)
    try:
        rollback_request = ModelRollbackRequest.model_validate(body, strict=True)
    except ValidationError as error:
        return _activation_rejection(str(error), 422)
    reason = rollback_request.reason.strip()
    activation = read_model_activation()
    if activation is None or activation.phase != "active":
        return _activation_rejection("there is no active grey generation", 409)
    active_pair = activation.active_pair
    rollback_pair = activation.rollback_pair
    if active_pair is None or rollback_pair is None:
        return _activation_rejection("activation-lineage-missing", 409)
    now_ms = int(time.time() * 1_000)
    decision = ModelEvidenceRecord(
        evidence_id=f"rollback:{activation.evidence_decision_id}:{activation.role_generation + 1}:{now_ms}",
        kind=EvidenceKind.ROLLBACK,
        session_id="api-manual-rollback",
        cook_id=None,
        timestamp_ms=now_ms,
        role_generation=activation.role_generation + 1,
        model_digest=active_pair.model_digest,
        provenance_digest=rollback_pair.model_digest,
        payload=RollbackEvidence(
            decision_id=activation.evidence_decision_id,
            reason=reason.strip(),
        ),
    )
    try:
        outcome = commit_model_rollback(decision, expected_activation=activation)
    except ValueError as error:
        return _activation_rejection(str(error), 409)
    except Exception as error:
        return _activation_rejection(f"rollback-persistence-failed: {error}", 503)
    lifecycle = outcome.record.payload
    payload = ModelRollbackAccepted(
        accepted=True,
        active_kind="grey-box",
        decision_id=activation.evidence_decision_id,
        reason=lifecycle.reason,
        role_generation=outcome.record.role_generation,
        rollback_digest=rollback_pair.model_digest,
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
        members, ops = notify_ops_from_post(request_json)
        write_control(control_delta(set_values=members, ops=ops), WriteKind.DELTA, origin="app")
        return jsonify({"control": "success", "result": "success", "message": "Settings updated successfully."}), 201
    except ControlDeltaError as exc:
        # A malformed patch is the CLIENT's error and is named as such, rather
        # than falling into the generic 201 below where a caller cannot tell a
        # rejected request from an accepted one.
        return jsonify({"control": "error", "result": "error", "message": str(exc)}), 400
    except Exception:
        return jsonify({"control": "error", "result": "error", "message": "Settings update failed."}), 201


def _api_post_wled_push_profiles(settings, request_json):
    """Push PiFire profiles to WLED device"""
    try:
        from notify.wled_profiles import WLEDProfileManager

        device_address = request_json.get("device_address", "").strip()
        profile_numbers = request_json.get("profile_numbers", {})

        if not device_address:
            return jsonify({"result": "error", "message": "Device address is required"}), 400

        # Create profile manager and push profiles
        profile_manager = WLEDProfileManager(device_address, settings)
        result = profile_manager.push_all_profiles(custom_profile_numbers=profile_numbers)

        if result["success"]:
            return jsonify(
                {
                    "result": "success",
                    "message": f"Successfully pushed {result['profiles_pushed']} profiles",
                    "profiles_pushed": result["profiles_pushed"],
                    "profiles": result["profiles"],
                }
            ), 200
        else:
            return jsonify({"result": "error", "message": result["message"]}), 500

    except Exception as e:
        return jsonify({"result": "error", "message": f"Failed to push profiles: {str(e)}"}), 500


def _api_post_wled_test_profile(settings, request_json):
    """Test a WLED profile"""
    try:
        import requests

        device_address = request_json.get("device_address", "").strip()
        profile_number = request_json.get("profile_number", 1)

        if not device_address:
            return jsonify({"result": "error", "message": "Device address is required"}), 400

        # Clean device address
        if "http://" in device_address:
            device_address = device_address.replace("http://", "")
        if "https://" in device_address:
            device_address = device_address.replace("https://", "")
        device_address = device_address.strip().rstrip("/")

        # Send test command to WLED
        url = f"http://{device_address}/json/state"
        payload = {"on": True, "bri": 128, "ps": profile_number}

        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()

        return jsonify({"result": "success", "message": f"Profile {profile_number} activated successfully"}), 200

    except requests.RequestException as e:
        return jsonify({"result": "error", "message": f"Failed to communicate with WLED device: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"result": "error", "message": f"Failed to test profile: {str(e)}"}), 500


def _api_post_pellets(settings, request_json):
    """One pellet action per request. body: {"action": <name>, "data": {...}}

    The action name travels in the BODY, not the path: api_page's POST branch
    calls handler(settings, request_json) and never forwards arg0.

    INTENT ONLY -- see common/pellets_actions.py's module docstring. This route
    must never grow a "here is the whole pellet database" form.
    """
    handler = PELLETS_DISPATCH.get(request_json.get("action"))
    if handler is None:
        return jsonify(api_response(result="Error", message="Error: Received request without valid action")), 200
    pelletdb = read_pellet_db()
    # `or {}`: add_profile subscripts action_data["brand_name"] without .get(),
    # so a missing `data` must arrive as a dict and raise KeyError inside the
    # handler rather than TypeError on None. Mirrors socket_io.py's
    # _ACTIONS_REQUIRING_JSON_DATA.
    return jsonify(handler(pelletdb, request_json.get("data") or {})), 200


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
        request_payload = _ProbeMapRequest.model_validate(request_json, strict=True)
    except ValidationError:
        response = _ProbeMapErrorResponse(result="error", message="bad_probe_map")
        return jsonify(response.model_dump(mode="json", by_alias=True, exclude_unset=True)), 400
    probe_map = request_payload.probe_map.model_dump(mode="json", by_alias=True, exclude_unset=True)

    control = read_control()
    if control.get("mode") != Mode.STOP:
        response = _ProbeMapErrorResponse(result="error", message="system_active")
        return jsonify(response.model_dump(mode="json", by_alias=True, exclude_unset=True)), 409

    manifest_modules = read_wizard().get("modules", {}).get("probes", {})
    live_map = settings["probe_settings"]["probe_map"]
    offenders = unsupported_new_modules(probe_map, live_map, manifest_modules)
    if offenders:
        response = _ProbeMapErrorResponse(
            result="error",
            message="modules_require_install",
            modules=offenders,
        )
        return jsonify(response.model_dump(mode="json", by_alias=True, exclude_unset=True)), 422

    try:
        validate_bus_kinds(configured_bus_kinds(settings, probe_map))
    except I2CBusConfigError as exc:
        response = _ProbeMapErrorResponse(
            result="error",
            message="bus_conflict",
            detail=str(exc),
        )
        return jsonify(response.model_dump(mode="json", by_alias=True, exclude_unset=True)), 422

    settings = set_probe_map(settings, probe_map, control)
    # settings_update makes the loop re-read settings; probe_map_update is what
    # makes it REBUILD its probe devices (controller.py, Task 3).
    # probe_profile_update is NOT enough on its own -- it only refills
    # per-port profiles on already-constructed devices (probes/base.py:393).
    # notify_data travels as a notify.replace op because set_probe_map() may
    # have dropped entries, and "an entry the incoming array omits is a
    # deletion" is exactly what replace says out loud.
    save_settings_and_flag_update(
        settings,
        control,
        "settings_update",
        "probe_map_update",
        origin="api",
        ops=[{"op": "notify.replace", "entries": control["notify_data"]}],
    )
    response = _ProbeMapApplyResponse(
        result="success",
        message="Probe map applied.",
        data=_ProbeMapApplyData(probe_map=request_payload.probe_map),
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
