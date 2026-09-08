"""Current-contract clock fixtures; historical payloads stay literal in their tests."""

from common.clock_domain import CLOCK_STAMP_SCHEMA, ClockStamp

BOOT_ID = "9c898927-5e50-4c98-9e50-ae092e623629"
RUNTIME_ID = "3d6b4545-8cd4-4fce-8251-555d84d9e8aa"


def clock_stamp(
    *,
    monotonic_s: float = 100.0,
    wall_s: float = 1_800_000_000.0,
    boot_id: str | None = BOOT_ID,
    runtime_id: str = RUNTIME_ID,
    suspend_offset_s: float | None = 2.0,
) -> ClockStamp:
    return ClockStamp(
        schema_version=CLOCK_STAMP_SCHEMA,
        boot_id=boot_id,
        runtime_id=runtime_id,
        observed_monotonic_s=monotonic_s,
        observed_wall_s=wall_s,
        suspend_offset_s=suspend_offset_s,
    )
