"""Privacy-preserving authority for installation-bound learned checkpoints."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

InstallationIdentityProvider = Callable[[], str | bytes | None]
_DOMAIN = b"pifire:model-installation-identity:v1\x00"
_MACHINE_ID_PATHS = (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id"))


class InstallationIdentityUnavailable(RuntimeError):
    """The local installation cannot safely authorize a learned checkpoint."""


def os_installation_identity() -> bytes:
    """Read the stable OS installation identifier without exposing it to persistence."""

    for path in _MACHINE_ID_PATHS:
        try:
            value = path.read_bytes().strip()
        except OSError:
            continue
        if value:
            return value
    raise InstallationIdentityUnavailable("installation identity authority is unavailable")


def installation_identity_digest(
    provider: InstallationIdentityProvider = os_installation_identity,
) -> str:
    """Return only a domain-separated digest suitable for durable records."""

    try:
        raw = provider()
    except InstallationIdentityUnavailable:
        raise
    except Exception as error:
        raise InstallationIdentityUnavailable("installation identity authority is unavailable") from error
    if isinstance(raw, str):
        value = raw.strip().encode("utf-8")
    elif isinstance(raw, bytes):
        value = raw.strip()
    else:
        value = b""
    if not value:
        raise InstallationIdentityUnavailable("installation identity authority is unavailable")
    return hashlib.sha256(_DOMAIN + value).hexdigest()
