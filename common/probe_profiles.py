"""Profile library mutations and their runtime calibration reloads."""

from typing import Literal

from common import datastore
from common.app import save_settings_and_flag_update
from common.common import generate_uuid
from common.modes import Mode
from common.persistence.control import read_control
from common.persistence.runtime import read_settings, write_settings
from common.web_contracts.operations import ProbeProfileMutation, ProfileInput, ProfileValues, SavedProfile
from common.web_contracts.wizard import ProbeProfile


class ProbeProfileError(Exception):
    """A profile mutation refused before any settings or queue write."""

    def __init__(
        self,
        code: Literal["not_found", "system_active", "profile_in_use"],
        status: Literal[404, 409],
        *,
        field: str | None = None,
        mode: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.field = field
        self.mode = mode
        self.detail = detail

    def error_data(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (("field", self.field), ("mode", self.mode), ("detail", self.detail))
            if value is not None
        }


def _profile(profile_id: str, payload: ProfileValues) -> ProbeProfile:
    return ProbeProfile(id=profile_id, name=payload.name, A=payload.a, B=payload.b, C=payload.c)


def _require_stopped() -> None:
    mode = read_control().get("mode")
    if mode != Mode.STOP:
        raise ProbeProfileError("system_active", 409, mode=mode)


def create_probe_profile(payload: ProfileInput) -> SavedProfile:
    """Create a library entry, optionally applying it to one configured probe."""
    # The settings blob and reload intent share the datastore's transaction.
    # Read/guard inside it so concurrent library writers cannot lose each other.
    with datastore.transaction():
        settings = read_settings()
        probe_settings = settings["probe_settings"]
        target = None
        if payload.apply_to is not None:
            target = next(
                (probe for probe in probe_settings["probe_map"]["probe_info"] if probe["label"] == payload.apply_to),
                None,
            )
            if target is None:
                raise ProbeProfileError("not_found", 404, field="apply_to")
            _require_stopped()
        profile_id = generate_uuid()
        profile = _profile(profile_id, payload).model_dump(mode="json")
        probe_settings["probe_profiles"][profile_id] = profile
        if target is not None:
            target["profile"] = profile.copy()
            save_settings_and_flag_update(settings, {}, "probe_profile_update", origin="probe-profiles")
        else:
            write_settings(settings)
    return SavedProfile(id=profile_id, applied=payload.apply_to)


def update_probe_profile(profile_id: str, payload: ProfileValues) -> ProbeProfileMutation:
    """Replace the library entry and every configured copy, including disabled probes."""
    with datastore.transaction():
        settings = read_settings()
        probe_settings = settings["probe_settings"]
        profiles = probe_settings["probe_profiles"]
        if profile_id not in profiles:
            raise ProbeProfileError("not_found", 404, field="id")
        assigned = [
            probe for probe in probe_settings["probe_map"]["probe_info"] if probe["profile"].get("id") == profile_id
        ]
        if assigned:
            _require_stopped()
        profile = _profile(profile_id, payload).model_dump(mode="json")
        profiles[profile_id] = profile
        for probe in assigned:
            probe["profile"] = profile.copy()
        if assigned:
            save_settings_and_flag_update(settings, {}, "probe_profile_update", origin="probe-profiles")
        else:
            write_settings(settings)
    return ProbeProfileMutation(id=profile_id, updated_probes=[probe["label"] for probe in assigned])


def delete_probe_profile(profile_id: str) -> ProbeProfileMutation:
    """Delete only an unused profile; any configured reference blocks deletion."""
    with datastore.transaction():
        settings = read_settings()
        probe_settings = settings["probe_settings"]
        if profile_id not in probe_settings["probe_profiles"]:
            raise ProbeProfileError("not_found", 404, field="id")
        assigned = [
            probe["name"] or probe["label"]
            for probe in probe_settings["probe_map"]["probe_info"]
            if probe["profile"].get("id") == profile_id
        ]
        if assigned:
            raise ProbeProfileError(
                "profile_in_use",
                409,
                detail=f"Used by {', '.join(assigned)}. Unassign this profile from these probes before deleting it.",
            )
        del probe_settings["probe_profiles"][profile_id]
        write_settings(settings)
    return ProbeProfileMutation(id=profile_id, updated_probes=[])
