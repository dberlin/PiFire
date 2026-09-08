"""Identity-qualified physical time; wall coordinates are provenance only."""

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TypedDict, TypeGuard
from uuid import UUID, uuid4

CLOCK_STAMP_SCHEMA = 1
CONTROL_DISCONTINUITY_SECONDS = 60.0


def _finite(value: object) -> TypeGuard[float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _valid_identity(value: object) -> TypeGuard[str]:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


@cache
def system_boot_id() -> str | None:
    """Read Linux boot identity once; unavailable identity cannot authorize age."""
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError, UnicodeError:
        return None
    return value if _valid_identity(value) else None


class ClockStampPayload(TypedDict):
    schema_version: int
    boot_id: str | None
    runtime_id: str
    observed_monotonic_s: float
    observed_wall_s: float
    suspend_offset_s: float | None


@dataclass(frozen=True, slots=True)
class ClockStamp:
    schema_version: int
    boot_id: str | None
    runtime_id: str
    observed_monotonic_s: float
    observed_wall_s: float
    suspend_offset_s: float | None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != CLOCK_STAMP_SCHEMA:
            raise ValueError("Unsupported clock stamp schema")
        if self.boot_id is not None and not _valid_identity(self.boot_id):
            raise ValueError("Invalid boot identity")
        if not _valid_identity(self.runtime_id):
            raise ValueError("Invalid runtime identity")
        if not _finite(self.observed_monotonic_s) or not _finite(self.observed_wall_s):
            raise ValueError("Clock coordinates must be finite numbers")
        if self.suspend_offset_s is not None and not _finite(self.suspend_offset_s):
            raise ValueError("Suspend offset must be a finite number or unknown")

    def as_dict(self) -> ClockStampPayload:
        return {
            "schema_version": self.schema_version,
            "boot_id": self.boot_id,
            "runtime_id": self.runtime_id,
            "observed_monotonic_s": self.observed_monotonic_s,
            "observed_wall_s": self.observed_wall_s,
            "suspend_offset_s": self.suspend_offset_s,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ClockStamp:
        if frozenset(value) != ClockStampPayload.__required_keys__:
            raise ValueError("Clock stamp fields do not match the current schema")
        schema = value["schema_version"]
        boot = value["boot_id"]
        runtime = value["runtime_id"]
        monotonic = value["observed_monotonic_s"]
        wall = value["observed_wall_s"]
        offset = value["suspend_offset_s"]
        if type(schema) is not int or schema != CLOCK_STAMP_SCHEMA:
            raise ValueError("Unsupported clock stamp schema")
        if boot is not None and not _valid_identity(boot):
            raise ValueError("Invalid boot identity")
        if not _valid_identity(runtime):
            raise ValueError("Invalid runtime identity")
        if not _finite(monotonic) or not _finite(wall):
            raise ValueError("Clock coordinates must be finite numbers")
        if offset is not None and not _finite(offset):
            raise ValueError("Suspend offset must be a finite number or unknown")
        return cls(schema, boot, runtime, monotonic, wall, offset)


def parse_clock_stamp(value: object) -> ClockStamp | None:
    """Parse untrusted persisted stamp data without guessing legacy coordinates."""
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        return None
    payload: dict[str, object] = {key: item for key, item in value.items() if isinstance(key, str)}
    try:
        return ClockStamp.from_dict(payload)
    except ValueError:
        return None


def stamp_age_s(
    stamp: ClockStamp,
    *,
    monotonic_s: float,
    boot_id: str | None,
    runtime_id: str | None,
    suspend_offset_s: float | None,
) -> float | None:
    """Return a same-generation age, or unknown; never subtract wall time."""
    if not boot_id or not runtime_id or stamp.boot_id != boot_id or stamp.runtime_id != runtime_id:
        return None
    if not _finite(monotonic_s) or not _finite(suspend_offset_s) or not _finite(stamp.suspend_offset_s):
        return None
    assert suspend_offset_s is not None and stamp.suspend_offset_s is not None
    if suspend_offset_s - stamp.suspend_offset_s > CONTROL_DISCONTINUITY_SECONDS:
        return None
    age = monotonic_s - stamp.observed_monotonic_s
    return age if age >= 0 else None


def continuity_lost(
    previous: ClockStamp,
    current: ClockStamp,
    *,
    max_active_gap_s: float = CONTROL_DISCONTINUITY_SECONDS,
) -> bool:
    if not _finite(max_active_gap_s) or max_active_gap_s < 0:
        raise ValueError("Active gap threshold must be finite and nonnegative")
    age = stamp_age_s(
        previous,
        monotonic_s=current.observed_monotonic_s,
        boot_id=current.boot_id,
        runtime_id=current.runtime_id,
        suspend_offset_s=current.suspend_offset_s,
    )
    return age is None or age > max_active_gap_s


class RuntimeClockDomain:
    """Capture one process generation using injected independent clock sources."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float],
        wall_time: Callable[[], float],
        boot_id: str | None,
        boottime: Callable[[], float] | None = None,
    ) -> None:
        if boot_id is not None and not _valid_identity(boot_id):
            raise ValueError("Invalid boot identity")
        self._monotonic: Callable[[], float] = monotonic
        self._wall_time: Callable[[], float] = wall_time
        self._boottime: Callable[[], float] | None = boottime
        self.boot_id: str | None = boot_id
        self.runtime_id: str = str(uuid4())

    @classmethod
    def for_system(cls, *, monotonic: Callable[[], float], wall_time: Callable[[], float]) -> RuntimeClockDomain:
        boottime: Callable[[], float] | None = None
        if hasattr(time, "CLOCK_BOOTTIME"):
            boottime = lambda: time.clock_gettime(time.CLOCK_BOOTTIME)
        return cls(
            monotonic=monotonic,
            wall_time=wall_time,
            boot_id=system_boot_id(),
            boottime=boottime,
        )

    def rotate_runtime(self) -> str:
        self.runtime_id = str(uuid4())
        return self.runtime_id

    def capture(self, *, monotonic_s: float | None = None, wall_s: float | None = None) -> ClockStamp:
        if (monotonic_s is None) != (wall_s is None):
            raise ValueError("Acquisition coordinates must be supplied as a pair")
        monotonic = self._monotonic()
        if not _finite(monotonic):
            raise ValueError("Monotonic source must return finite seconds")
        offset = None
        if self._boottime is not None:
            boottime = self._boottime()
            if not _finite(boottime):
                raise ValueError("Boottime source must return finite seconds")
            offset = boottime - monotonic
        return ClockStamp(
            schema_version=CLOCK_STAMP_SCHEMA,
            boot_id=self.boot_id,
            runtime_id=self.runtime_id,
            observed_monotonic_s=monotonic if monotonic_s is None else monotonic_s,
            observed_wall_s=self._wall_time() if wall_s is None else wall_s,
            suspend_offset_s=offset,
        )


@cache
def _reader_clock_domain() -> RuntimeClockDomain:
    return RuntimeClockDomain.for_system(monotonic=time.monotonic, wall_time=time.time)


def local_clock_stamp() -> ClockStamp | None:
    """Sample this reader's local boot/offset; its runtime is not control authority."""
    try:
        return _reader_clock_domain().capture()
    except OSError, ValueError:
        return None


def heartbeat_runtime_id(
    heartbeat: ClockStamp | None,
    current: ClockStamp | None,
    *,
    stale_after_s: float,
) -> str | None:
    """Accept a control generation only from its fresh identity-qualified heartbeat."""
    if heartbeat is None or current is None:
        return None
    age = stamp_age_s(
        heartbeat,
        monotonic_s=current.observed_monotonic_s,
        boot_id=current.boot_id,
        runtime_id=heartbeat.runtime_id,
        suspend_offset_s=current.suspend_offset_s,
    )
    return heartbeat.runtime_id if age is not None and age <= stale_after_s else None


def qualified_stamp_age_s(
    stamp: ClockStamp | None,
    *,
    heartbeat: ClockStamp | None,
    current: ClockStamp | None,
    heartbeat_stale_after_s: float,
) -> float | None:
    """Age a retained observation only against a live control generation."""
    if stamp is None or current is None:
        return None
    runtime_id = heartbeat_runtime_id(heartbeat, current, stale_after_s=heartbeat_stale_after_s)
    return stamp_age_s(
        stamp,
        monotonic_s=current.observed_monotonic_s,
        boot_id=current.boot_id,
        runtime_id=runtime_id,
        suspend_offset_s=current.suspend_offset_s,
    )
