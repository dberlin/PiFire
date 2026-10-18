"""Typed mutations for the shared probe-profile library."""

from flask import jsonify, request
from pydantic import ValidationError

from common.app import api_response
from common.probe_profiles import (
    ProbeProfileError,
    create_probe_profile,
    delete_probe_profile,
    update_probe_profile,
)
from common.web_contracts.operations import ProfileInput, ProfileValues, dump_error_data

from . import api_probe_profiles_bp


def _error(message: str, status: int, data: dict[str, str]):
    return jsonify(api_response("Error", message, dump_error_data(data))), status


def _validate_payload[T: ProfileValues](model: type[T]) -> T:
    return model.model_validate(request.get_json(silent=True), strict=True)


@api_probe_profiles_bp.errorhandler(ValidationError)
def invalid_profile(exc: ValidationError):
    location = exc.errors()[0]["loc"]
    field = next((part for part in location if isinstance(part, str)), "name")
    return _error("bad_request", 400, {"field": field})


@api_probe_profiles_bp.errorhandler(ProbeProfileError)
def refused_profile(exc: ProbeProfileError):
    return _error(exc.code, exc.status, exc.error_data())


@api_probe_profiles_bp.route("", methods=["POST"])
def create_profile():
    result = create_probe_profile(_validate_payload(ProfileInput))
    return jsonify(api_response("OK", None, result.model_dump(mode="json"))), 200


@api_probe_profiles_bp.route("/<profile_id>", methods=["PUT"])
def update_profile(profile_id: str):
    result = update_probe_profile(profile_id, _validate_payload(ProfileValues))
    return jsonify(api_response("OK", None, result.model_dump(mode="json"))), 200


@api_probe_profiles_bp.route("/<profile_id>", methods=["DELETE"])
def delete_profile(profile_id: str):
    result = delete_probe_profile(profile_id)
    return jsonify(api_response("OK", None, result.model_dump(mode="json"))), 200
