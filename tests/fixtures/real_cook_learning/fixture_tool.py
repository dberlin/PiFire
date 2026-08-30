#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

JSON = dict[str, Any]
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
BASELINE_TABLES = (
    "control_trace",
    "kv",
    "model_activation_state",
    "model_evidence",
)
ARCHIVE_MEMBERS_EXACT = (
    "metadata.json",
    "chamber_samples.json",
    "sessions.json",
    "transitions.json",
    "frames.json",
)
ARCHIVE_MEMBERS_THERMAL = ("metadata.json", "chamber_samples.json")
FORBIDDEN_TEXT = (
    re.compile(r"/(?:home|Users|var|tmp)/", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(
        r"(?:password|passwd|credential|access[_-]?token|api[_-]?key|ssid|hostname|username|notify_targets)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:food1|food2|mainprobe)\b", re.IGNORECASE),
)

CAMPAIGNS: tuple[JSON, ...] = (
    {
        "id": "mpc-aug27",
        "controller": "mpc",
        "diagnostic_arg": "diag_aug27",
        "duplicate_arg": "diag_aug27_duplicate",
        "baseline_path": "baselines/mpc-aug27.sqlite",
        "cooks": (
            {
                "arg": "cook_aug22",
                "source_name": "2026-08-22--1636-CookFile.pifire",
                "path": "cookfiles/2026-08-22--1636.pifire",
                "replay_kind": "thermal-smoke-only",
                "blocked_reason": (
                    "The source CookFile has no learning_diagnostics.json, and the earliest "
                    "August 27 diagnostic trace begins 436,? milliseconds after this CookFile "
                    "ends; no source-supported exact actuation-frame join exists."
                ),
            },
            {
                "arg": "cook_aug27",
                "source_name": "2026-08-27--2015-CookFile.pifire",
                "path": "cookfiles/2026-08-27--2015.pifire",
                "replay_kind": "exact-evidence",
            },
        ),
    },
    {
        "id": "pid-sp-aug28",
        "controller": "pid_sp",
        "diagnostic_arg": "diag_aug28",
        "duplicate_arg": "diag_aug28_duplicate",
        "baseline_path": "baselines/pid-sp-aug28.sqlite",
        "cooks": (
            {
                "arg": "cook_aug28",
                "source_name": "2026-08-28--1931-CookFile.pifire",
                "path": "cookfiles/2026-08-28--1931.pifire",
                "replay_kind": "exact-evidence",
            },
        ),
    },
    {
        "id": "mpc-aug29",
        "controller": "mpc",
        "diagnostic_arg": "diag_aug29",
        "duplicate_arg": None,
        "baseline_path": "baselines/mpc-aug29.sqlite",
        "cooks": (
            {
                "arg": "cook_aug29_1219",
                "source_name": "2026-08-29--1219-CookFile.pifire",
                "path": "cookfiles/2026-08-29--1219.pifire",
                "replay_kind": "exact-evidence",
            },
            {
                "arg": "cook_aug29_1625",
                "source_name": "2026-08-29--1625-CookFile.pifire",
                "path": "cookfiles/2026-08-29--1625.pifire",
                "replay_kind": "exact-evidence",
            },
        ),
    },
)


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def require_source(path: Path, expected_name: str) -> None:
    if path.name != expected_name:
        raise ValueError(f"expected source named {expected_name}, got {path.name}")
    if not path.is_file():
        raise FileNotFoundError(path)


def read_zip_json(path: Path, member: str) -> Any:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read(member))


def database_bytes(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name == "pifire.db"]
        if names != ["pifire.db"]:
            raise ValueError(f"{path.name} must contain exactly one pifire.db")
        return archive.read("pifire.db")


def memory_database(data: bytes) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.deserialize(data)
    return connection


def chamber_samples(raw_rows: list[JSON]) -> list[JSON]:
    result: list[JSON] = []
    last_ts = -1
    for row in raw_rows:
        timestamp_ms = int(row["T"])
        probes = row["P"]
        if not isinstance(probes, dict) or len(probes) != 1:
            raise ValueError("a chamber sample must contain exactly one primary probe")
        if timestamp_ms < last_ts:
            raise ValueError("chamber samples are not timestamp ordered")
        last_ts = timestamp_ms
        result.append(
            {
                "chamber_temperature_f": float(next(iter(probes.values()))),
                "setpoint_f": float(row["PSP"]),
                "timestamp_ms": timestamp_ms,
            }
        )
    return result


def f_to_c(value: float | None) -> float | None:
    if value is None:
        return None
    return (float(value) - 32.0) * (5.0 / 9.0)


def local_maps(records: list[JSON], fixture_cook_id: str) -> JSON:
    session_first: dict[str, int] = {}
    revisions: dict[tuple[str, int], int] = {}
    allocator_revisions: dict[tuple[str, int], int] = {}
    model_revisions: dict[int, int] = {}
    role_generations: dict[int, int] = {}
    for record in records:
        session = record["session_id"]
        session_first.setdefault(session, int(record["ts_ms"]))
        payload = record["payload"]
        revision = payload.get("result_revision")
        if isinstance(revision, int):
            revisions.setdefault((session, revision), len(revisions) + 1)
        allocator_revision = payload.get("allocator_revision")
        if isinstance(allocator_revision, int):
            allocator_revisions.setdefault((session, allocator_revision), len(allocator_revisions) + 1)
        model_revision = payload.get("model_revision")
        if isinstance(model_revision, int):
            model_revisions.setdefault(model_revision, len(model_revisions) + 1)
        role_generation = payload.get("role_generation")
        if isinstance(role_generation, int):
            role_generations.setdefault(role_generation, len(role_generations) + 1)
    sessions = {
        source: f"{fixture_cook_id}-session-{index:03d}"
        for index, source in enumerate(sorted(session_first, key=lambda value: (session_first[value], value)), 1)
    }
    return {
        "allocator_revisions": allocator_revisions,
        "cook_id": fixture_cook_id,
        "model_revisions": model_revisions,
        "revisions": revisions,
        "role_generations": role_generations,
        "sessions": sessions,
    }


def mapped_revision(maps: JSON, session: str, value: Any) -> Any:
    if not isinstance(value, int):
        return value
    return maps["revisions"][(session, value)]


def sanitize_sessions(records: list[JSON], maps: JSON) -> list[JSON]:
    allowed_payload = {
        "ambient_temperature",
        "control_period_seconds",
        "controller",
        "controller_config",
        "fan_authority",
        "fan_max_duty",
        "fan_min_duty",
        "fan_pwm_capable",
        "model_provenance",
        "model_revision",
        "payload_type",
        "pulse_frame_seconds",
        "pulse_slot_seconds",
        "setpoint",
        "temperature_unit",
    }
    result = []
    for record in records:
        if record["event_kind"] != "session":
            continue
        source_session = record["session_id"]
        payload = {key: value for key, value in record["payload"].items() if key in allowed_payload}
        if isinstance(payload.get("model_revision"), int):
            payload["model_revision"] = maps["model_revisions"][payload["model_revision"]]
        result.append(
            {
                "controller": record["controller"],
                "cook_id": maps["cook_id"],
                "payload": payload,
                "schema_version": int(record["schema_version"]),
                "session_id": maps["sessions"][source_session],
                "timestamp_ms": int(record["ts_ms"]),
            }
        )
    return result


def records_by_frame_key(records: list[JSON], event_kind: str) -> dict[tuple[str, int], list[JSON]]:
    result: dict[tuple[str, int], list[JSON]] = defaultdict(list)
    for record in records:
        if record["event_kind"] != event_kind:
            continue
        revision = record["payload"].get("result_revision")
        if isinstance(revision, int):
            result[(record["session_id"], revision)].append(record)
    return result


def matching_boundary(
    records: list[JSON],
    start_ms: int,
    end_ms: int,
    consumed: set[int],
    revision_is_one_to_one: bool,
) -> JSON | None:
    exact = [
        record
        for record in records
        if id(record) not in consumed
        and record["payload"].get("frame_start_ms") == start_ms
        and record["payload"].get("frame_end_ms") == end_ms
    ]
    if len(exact) > 1:
        raise ValueError(f"multiple source outcomes match frame {start_ms}..{end_ms}")
    match = exact[0] if exact else None
    if match is None and revision_is_one_to_one:
        remaining = [record for record in records if id(record) not in consumed]
        if len(remaining) == 1:
            match = remaining[0]
    if match is not None:
        identity = id(match)
        if identity in consumed:
            raise ValueError(f"source outcome reused for frame {start_ms}..{end_ms}")
        consumed.add(identity)
    return match


def weighted_realized(applied: list[JSON], key: str, frame_seconds: float) -> float | None:
    if frame_seconds <= 0:
        return None
    weighted = 0.0
    covered = 0.0
    for record in applied:
        payload = record["payload"]
        value = payload.get(key)
        if value is None:
            continue
        duration = max(0.0, (int(payload["interval_end_ms"]) - int(payload["interval_start_ms"])) / 1000.0)
        weighted += float(value) * duration
        covered += duration
    if covered == 0.0:
        return None
    return weighted / frame_seconds


def sanitize_frames(records: list[JSON], maps: JSON) -> tuple[list[JSON], dict[str, int]]:
    observations = records_by_frame_key(records, "model_observation")
    gaps = records_by_frame_key(records, "recorder_gap")
    updates = records_by_frame_key(records, "control_update")
    applied_outputs = records_by_frame_key(records, "applied_output")
    actuation = [record for record in records if record["event_kind"] == "actuation_frame"]
    actuation.sort(
        key=lambda record: (
            int(record["payload"]["frame_start_ms"]),
            int(record["payload"]["frame_end_ms"]),
            int(record["ts_ms"]),
        )
    )
    actuation_counts = Counter(
        (record["session_id"], int(record["payload"]["result_revision"])) for record in actuation
    )
    result: list[JSON] = []
    prior_end_by_session: dict[str, int] = {}
    consumed_observations: set[int] = set()
    consumed_gaps: set[int] = set()
    for index, record in enumerate(actuation, 1):
        source_session = record["session_id"]
        payload = record["payload"]
        source_revision = int(payload["result_revision"])
        key = (source_session, source_revision)
        start_ms = int(payload["frame_start_ms"])
        end_ms = int(payload["frame_end_ms"])
        revision_is_one_to_one = actuation_counts[key] == 1
        observation_record = matching_boundary(
            observations.get(key, []),
            start_ms,
            end_ms,
            consumed_observations,
            revision_is_one_to_one and len(observations.get(key, [])) == 1,
        )
        gap_record = matching_boundary(
            gaps.get(key, []),
            start_ms,
            end_ms,
            consumed_gaps,
            revision_is_one_to_one and len(gaps.get(key, [])) == 1,
        )
        update_records = updates.get(key, [])
        update = update_records[0]["payload"] if update_records else {}
        observation = observation_record["payload"] if observation_record else {}
        gap = gap_record["payload"] if gap_record else {}
        applied = [
            item
            for item in applied_outputs.get(key, [])
            if int(item["payload"]["interval_start_ms"]) >= start_ms
            and int(item["payload"]["interval_end_ms"]) <= end_ms
        ]
        frame_seconds = float(payload["frame_seconds"])
        realized_auger = observation.get("realized_auger_duty")
        if realized_auger is None:
            realized_auger = float(payload["delivered_on_seconds"]) / frame_seconds
        realized_combustion = observation.get("realized_combustion_load")
        if realized_combustion is None:
            realized_combustion = weighted_realized(applied, "realized_combustion_load", frame_seconds)
        actual_fan = observation.get("actual_fan_duty", payload.get("applied_fan_duty"))
        temperature_c = observation.get("temp_c")
        setpoint_c = observation.get("setpoint_c")
        ambient_c = observation.get("ambient_c")
        if temperature_c is None:
            temperature_c = f_to_c(update.get("measured_temperature"))
        if setpoint_c is None:
            setpoint_c = f_to_c(update.get("setpoint"))
        if ambient_c is None:
            ambient_c = None
        if gap:
            source_outcome = {
                "kind": "gap",
                "lost_record_count": int(gap["lost_record_count"]),
                "reason": str(gap["reason"]),
            }
        elif observation_record is not None:
            if observation.get("eligible") is True:
                source_outcome = {"kind": "accepted", "reasons": []}
            else:
                source_outcome = {
                    "kind": "rejected",
                    "reasons": list(observation.get("rejection_reasons", [])),
                }
        else:
            source_outcome = {
                "kind": "unmatched",
                "reason": "no-source-terminal-record",
            }
        boundary_reason: JSON | None = None
        if gap:
            boundary_reason = {"kind": "recorder-gap", "reason": str(gap["reason"])}
        elif payload.get("reset_reason") is not None:
            boundary_reason = {"kind": "reset", "reason": str(payload["reset_reason"])}
        elif payload.get("inhibit_reason") not in (None, "none"):
            boundary_reason = {"kind": "inhibit", "reason": str(payload["inhibit_reason"])}
        elif payload.get("skipped"):
            boundary_reason = {"kind": "skipped", "reason": "source-frame-skipped"}
        elif payload.get("stale_command"):
            boundary_reason = {"kind": "stale", "reason": "source-command-stale"}
        allocator_revision = observation.get("allocator_revision")
        if isinstance(allocator_revision, int):
            allocator_revision = maps["allocator_revisions"][(source_session, allocator_revision)]
        role_generation = observation.get("role_generation")
        if isinstance(role_generation, int):
            role_generation = maps["role_generations"][role_generation]
        result.append(
            {
                "actual_fan_duty": actual_fan,
                "allocated_combustion_load": observation.get("allocated_combustion_load"),
                "allocator_revision": allocator_revision,
                "ambient_c": ambient_c,
                "boundary_reason": boundary_reason,
                "chamber_temperature_c": temperature_c,
                "continuous": observation.get("continuous"),
                "controller": record["controller"],
                "cook_id": maps["cook_id"],
                "delivered_on_seconds": float(payload["delivered_on_seconds"]),
                "frame_end_ms": end_ms,
                "frame_seconds": frame_seconds,
                "frame_start_ms": start_ms,
                "interval_contiguous": prior_end_by_session.get(source_session, start_ms) == start_ms,
                "lid_open": observation.get("lid_open"),
                "manual_override": observation.get("manual_override"),
                "observation_sequence": index,
                "output_source": observation.get("output_source", update.get("output_source")),
                "realized_auger_duty": realized_auger,
                "realized_combustion_load": realized_combustion,
                "requested_auger_duty": float(payload["requested_auger_duty"]),
                "requested_combustion_load": float(payload["requested_combustion_load"]),
                "requested_fan_duty": payload.get("requested_fan_duty"),
                "reset": observation.get("reset", payload.get("reset_reason") is not None),
                "result_revision": mapped_revision(maps, source_session, source_revision),
                "safety_inhibited": observation.get("safety_inhibited"),
                "sample_complete": bool(applied)
                and all(bool(item["payload"].get("sample_complete")) for item in applied),
                "scheduled_on_seconds": float(payload["scheduled_on_seconds"]),
                "schema_version": int(record["schema_version"]),
                "session_id": maps["sessions"][source_session],
                "setpoint_c": setpoint_c,
                "skipped": bool(payload["skipped"]),
                "source_outcome": source_outcome,
                "stale": bool(observation.get("stale", payload.get("stale_command", False))),
                "trace_sequence": index,
            }
        )
        prior_end_by_session[source_session] = end_ms
    outcome_counter = Counter(frame["source_outcome"]["kind"] for frame in result)
    source_outcome_counts = {
        kind: outcome_counter.get(kind, 0) for kind in ("accepted", "gap", "rejected", "unmatched")
    }
    if len(consumed_observations) != (
        source_outcome_counts.get("accepted", 0) + source_outcome_counts.get("rejected", 0)
    ):
        raise ValueError("a retained source observation was consumed more than once")
    if len(consumed_gaps) != source_outcome_counts.get("gap", 0):
        raise ValueError("a retained source gap was consumed more than once")
    return result, source_outcome_counts


def sanitize_transitions(records: list[JSON], maps: JSON) -> list[JSON]:
    result: list[JSON] = []
    prior_by_session: dict[str, tuple[Any, ...]] = {}
    for record in records:
        if record["event_kind"] != "control_update":
            continue
        payload = record["payload"]
        source_session = record["session_id"]
        values = (
            payload.get("actuation_mode"),
            payload.get("output_source"),
            payload.get("policy_kind"),
            payload.get("branch"),
            payload.get("setpoint"),
        )
        if prior_by_session.get(source_session) == values:
            continue
        prior_by_session[source_session] = values
        result.append(
            {
                "actuation_mode": payload.get("actuation_mode"),
                "branch": payload.get("branch"),
                "controller": record["controller"],
                "cook_id": maps["cook_id"],
                "output_source": payload.get("output_source"),
                "policy_kind": payload.get("policy_kind"),
                "result_revision": mapped_revision(maps, source_session, payload.get("result_revision")),
                "session_id": maps["sessions"][source_session],
                "setpoint_c": f_to_c(payload.get("setpoint")),
                "timestamp_ms": int(record["ts_ms"]),
            }
        )
    return result


def write_deterministic_zip(path: Path, members: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in members.items():
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info,
                canonical_json(value),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )


def sanitize_cook(source: Path, output: Path, campaign: JSON, cook: JSON, cook_index: int) -> JSON:
    require_source(source, cook["source_name"])
    source_hash = sha256_file(source)
    with zipfile.ZipFile(source) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        raw_data = json.loads(archive.read("raw_data.json"))
        learning = (
            json.loads(archive.read("learning_diagnostics.json"))
            if "learning_diagnostics.json" in archive.namelist()
            else None
        )
    fixture_cook_id = f"fixture-{campaign['id']}-cook-{cook_index:03d}"
    thermal = chamber_samples(raw_data)
    blocked_reason = cook.get("blocked_reason")
    if blocked_reason:
        gap_ms = 1787867418005 - int(metadata["endtime"])
        blocked_reason = blocked_reason.replace("436,?", f"{gap_ms}")
    sanitized_metadata = {
        "blocked_reason": blocked_reason,
        "chamber_sample_count": len(thermal),
        "controller": campaign["controller"],
        "cook_end_ms": int(metadata["endtime"]),
        "cook_id": fixture_cook_id,
        "cook_start_ms": int(metadata["starttime"]),
        "replay_kind": cook["replay_kind"],
        "schema_version": 1,
        "trace_schema_version": None,
        "source_outcome_counts": None,
        "units": "F",
    }
    members: dict[str, Any] = {
        "metadata.json": sanitized_metadata,
        "chamber_samples.json": thermal,
    }
    expected_input_frame_count: int | None = None
    trace_schema_version: int | None = None
    source_outcome_counts: dict[str, int] | None = None
    if cook["replay_kind"] == "exact-evidence":
        if learning is None:
            raise ValueError(f"{source.name} lacks learning_diagnostics.json")
        controllers = learning.get("controllers")
        if controllers != [campaign["controller"]]:
            raise ValueError(f"unexpected controllers in {source.name}: {controllers!r}")
        records = list(learning["control_trace"]["records"])
        versions = sorted({int(record["schema_version"]) for record in records})
        if len(versions) != 1:
            raise ValueError(f"mixed control-trace schemas in {source.name}: {versions}")
        trace_schema_version = versions[0]
        maps = local_maps(records, fixture_cook_id)
        sessions = sanitize_sessions(records, maps)
        frames, source_outcome_counts = sanitize_frames(records, maps)
        transitions = sanitize_transitions(records, maps)
        if not sessions or not frames:
            raise ValueError(f"{source.name} does not contain exact replay inputs")
        expected_input_frame_count = len(frames)
        sanitized_metadata["trace_schema_version"] = trace_schema_version
        sanitized_metadata["source_outcome_counts"] = source_outcome_counts
        members.update(
            {
                "sessions.json": sessions,
                "transitions.json": transitions,
                "frames.json": frames,
            }
        )
    write_deterministic_zip(output, members)
    return {
        "blocked_reason": blocked_reason,
        "cook_end_ms": int(metadata["endtime"]),
        "cook_start_ms": int(metadata["starttime"]),
        "expected_input_frame_count": expected_input_frame_count,
        "path": cook["path"],
        "replay_kind": cook["replay_kind"],
        "sanitized_sha256": sha256_file(output),
        "source_outcome_counts": source_outcome_counts,
        "source_sha256": source_hash,
        "trace_schema_version": trace_schema_version,
    }


def source_schema(connection: sqlite3.Connection) -> tuple[int, dict[str, str], list[tuple[str, str]]]:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    tables: dict[str, str] = {}
    for table in BASELINE_TABLES:
        row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if row is None or not row[0]:
            raise ValueError(f"source database lacks required table {table}")
        tables[table] = str(row[0])
    indexes = [
        (str(name), str(sql))
        for name, sql, table in connection.execute(
            "SELECT name,sql,tbl_name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name"
        )
        if table in BASELINE_TABLES
    ]
    return version, tables, indexes


def write_baseline(
    output: Path,
    campaign_id: str,
    cutoff_wall_ms: int,
    source_database_hash: str,
    schema_version: int,
    tables: dict[str, str],
    indexes: list[tuple[str, str]],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    connection = sqlite3.connect(output)
    try:
        connection.execute("PRAGMA page_size=4096")
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        for table in BASELINE_TABLES:
            connection.execute(tables[table])
        for _name, sql in indexes:
            connection.execute(sql)
        connection.execute(
            "CREATE TABLE fixture_metadata ("
            "singleton INTEGER PRIMARY KEY CHECK (singleton = 1),"
            "campaign_id TEXT NOT NULL,"
            "cutoff_wall_ms INTEGER NOT NULL,"
            "source_database_sha256 TEXT NOT NULL,"
            "source_schema_version INTEGER NOT NULL,"
            "retained_state TEXT NOT NULL CHECK (retained_state = 'factory-fallback')"
            ")"
        )
        connection.execute(
            "INSERT INTO fixture_metadata VALUES (1,?,?,?,?,?)",
            (campaign_id, cutoff_wall_ms, source_database_hash, schema_version, "factory-fallback"),
        )
        connection.execute(f"PRAGMA user_version={schema_version}")
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()


def sanitize_baseline(
    diagnostic: Path,
    duplicate: Path | None,
    output: Path,
    campaign_id: str,
    cutoff_wall_ms: int,
) -> JSON:
    require_source(diagnostic, diagnostic.name)
    source_archive_hash = sha256_file(diagnostic)
    source_db = database_bytes(diagnostic)
    source_db_hash = sha256_bytes(source_db)
    if duplicate is not None:
        duplicate_db = database_bytes(duplicate)
        if duplicate_db != source_db:
            raise ValueError(f"duplicate database differs for {diagnostic.name}")
    source = memory_database(source_db)
    try:
        schema_version, tables, indexes = source_schema(source)
        if source.execute("SELECT COUNT(*) FROM model_activation_state").fetchone()[0] != 0:
            raise ValueError("source has activation state but no approved pre-cutoff lineage sanitizer")
    finally:
        source.close()
    write_baseline(
        output,
        campaign_id,
        cutoff_wall_ms,
        source_db_hash,
        schema_version,
        tables,
        indexes,
    )
    return {
        "cutoff_wall_ms": cutoff_wall_ms,
        "path": output.as_posix(),
        "sanitized_sha256": sha256_file(output),
        "schema_version": schema_version,
        "source_archive_sha256": source_archive_hash,
        "source_database_sha256": source_db_hash,
    }


def generate(args: argparse.Namespace) -> None:
    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_campaigns: list[JSON] = []
    for campaign in CAMPAIGNS:
        cook_entries = []
        for index, cook in enumerate(campaign["cooks"], 1):
            source = Path(getattr(args, cook["arg"]))
            output = output_root / cook["path"]
            cook_entries.append(sanitize_cook(source, output, campaign, cook, index))
        cutoff_wall_ms = int(cook_entries[0]["cook_start_ms"])
        diagnostic = Path(getattr(args, campaign["diagnostic_arg"]))
        duplicate_arg = campaign["duplicate_arg"]
        duplicate = Path(getattr(args, duplicate_arg)) if duplicate_arg else None
        expected_diagnostic_name = {
            "mpc-aug27": "PiFire_Diagnostics_20260827-202055.zip",
            "pid-sp-aug28": "PiFire_Diagnostics_20260828-210051.zip",
            "mpc-aug29": "PiFire_Diagnostics_20260829-Today.zip",
        }[campaign["id"]]
        require_source(diagnostic, expected_diagnostic_name)
        if duplicate is not None:
            require_source(duplicate, expected_diagnostic_name.replace(".zip", " (1).zip"))
        baseline_output = output_root / campaign["baseline_path"]
        baseline = sanitize_baseline(
            diagnostic,
            duplicate,
            baseline_output,
            campaign["id"],
            cutoff_wall_ms,
        )
        baseline["path"] = campaign["baseline_path"]
        manifest_campaigns.append(
            {
                "baseline": baseline,
                "controller": campaign["controller"],
                "cooks": cook_entries,
                "id": campaign["id"],
            }
        )
    manifest = {"campaigns": manifest_campaigns, "schema_version": 1}
    (output_root / "manifest.json").write_bytes(canonical_json(manifest))
    verify(output_root)


def reject_forbidden_text(value: str, context: str) -> None:
    for pattern in FORBIDDEN_TEXT:
        if pattern.search(value):
            raise ValueError(f"forbidden text in {context}: {value!r}")


def walk_strings(value: Any, context: str) -> None:
    if isinstance(value, str):
        reject_forbidden_text(value, context)
    elif isinstance(value, dict):
        for key, child in value.items():
            reject_forbidden_text(key, context)
            walk_strings(child, f"{context}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_strings(child, f"{context}[{index}]")


def verify_archive(root: Path, entry: JSON, campaign_id: str, controller: str) -> None:
    path = root / entry["path"]
    if sha256_file(path) != entry["sanitized_sha256"]:
        raise ValueError(f"sanitized hash mismatch: {entry['path']}")
    with zipfile.ZipFile(path) as archive:
        names = tuple(archive.namelist())
        expected = ARCHIVE_MEMBERS_EXACT if entry["replay_kind"] == "exact-evidence" else ARCHIVE_MEMBERS_THERMAL
        if names != expected:
            raise ValueError(f"unexpected members in {entry['path']}: {names!r}")
        decoded = {name: json.loads(archive.read(name)) for name in names}
    walk_strings(decoded, entry["path"])
    metadata = decoded["metadata.json"]
    if metadata["controller"] != controller or metadata["replay_kind"] != entry["replay_kind"]:
        raise ValueError(f"metadata mismatch: {entry['path']}")
    if metadata["cook_start_ms"] != entry["cook_start_ms"] or metadata["cook_end_ms"] != entry["cook_end_ms"]:
        raise ValueError(f"cook range mismatch: {entry['path']}")
    samples = decoded["chamber_samples.json"]
    if len(samples) != metadata["chamber_sample_count"]:
        raise ValueError(f"chamber sample count mismatch: {entry['path']}")
    if samples and (
        samples[0]["timestamp_ms"] != entry["cook_start_ms"] or samples[-1]["timestamp_ms"] != entry["cook_end_ms"]
    ):
        raise ValueError(f"chamber sample boundaries mismatch: {entry['path']}")
    if entry["replay_kind"] == "exact-evidence":
        if entry["blocked_reason"] is not None:
            raise ValueError(f"exact replay has blocked_reason: {entry['path']}")
        if not isinstance(entry["trace_schema_version"], int) or not isinstance(
            entry["expected_input_frame_count"], int
        ):
            raise ValueError(f"exact replay has null trace fields: {entry['path']}")
        frames = decoded["frames.json"]
        if len(frames) != entry["expected_input_frame_count"]:
            raise ValueError(f"frame count mismatch: {entry['path']}")
        if any(frame["schema_version"] != entry["trace_schema_version"] for frame in frames):
            raise ValueError(f"trace schema mismatch: {entry['path']}")
        if [frame["trace_sequence"] for frame in frames] != list(range(1, len(frames) + 1)):
            raise ValueError(f"noncanonical frame sequence: {entry['path']}")
        outcome_counter = Counter(frame["source_outcome"]["kind"] for frame in frames)
        source_outcome_counts = {
            kind: outcome_counter.get(kind, 0) for kind in ("accepted", "gap", "rejected", "unmatched")
        }
        if sum(source_outcome_counts.values()) != len(frames):
            raise ValueError(f"source outcome cardinality mismatch: {entry['path']}")
        if source_outcome_counts != entry["source_outcome_counts"]:
            raise ValueError(f"manifest source outcome counts mismatch: {entry['path']}")
        if source_outcome_counts != metadata["source_outcome_counts"]:
            raise ValueError(f"archive source outcome counts mismatch: {entry['path']}")
        for frame in frames:
            if frame["cook_id"] != metadata["cook_id"] or frame["controller"] != controller:
                raise ValueError(f"frame identity mismatch: {entry['path']}")
            if frame["frame_start_ms"] >= frame["frame_end_ms"]:
                raise ValueError(f"invalid frame boundary: {entry['path']}")
    else:
        if entry["trace_schema_version"] is not None or entry["expected_input_frame_count"] is not None:
            raise ValueError(f"thermal-only cook claims exact trace: {entry['path']}")
        if entry["source_outcome_counts"] is not None or metadata["source_outcome_counts"] is not None:
            raise ValueError(f"thermal-only cook claims source outcomes: {entry['path']}")
        if not entry["blocked_reason"]:
            raise ValueError(f"thermal-only cook lacks blocked_reason: {entry['path']}")


def verify_baseline(root: Path, entry: JSON, campaign_id: str) -> None:
    path = root / entry["path"]
    if sha256_file(path) != entry["sanitized_sha256"]:
        raise ValueError(f"sanitized hash mismatch: {entry['path']}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        expected_tables = tuple(sorted((*BASELINE_TABLES, "fixture_metadata")))
        if tables != expected_tables:
            raise ValueError(f"unexpected baseline tables in {entry['path']}: {tables!r}")
        for table in BASELINE_TABLES:
            if connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] != 0:
                raise ValueError(f"pre-campaign baseline retains rows in {table}: {entry['path']}")
        metadata = connection.execute(
            "SELECT campaign_id,cutoff_wall_ms,source_database_sha256,source_schema_version,retained_state "
            "FROM fixture_metadata"
        ).fetchall()
        expected_metadata = [
            (
                campaign_id,
                entry["cutoff_wall_ms"],
                entry["source_database_sha256"],
                entry["schema_version"],
                "factory-fallback",
            )
        ]
        if metadata != expected_metadata:
            raise ValueError(f"baseline metadata mismatch: {entry['path']}")
        if connection.execute("PRAGMA user_version").fetchone()[0] != entry["schema_version"]:
            raise ValueError(f"baseline schema mismatch: {entry['path']}")
        for table in tables:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            walk_strings(columns, f"{entry['path']}:{table}:columns")
        for table in tables:
            for row in connection.execute(f'SELECT * FROM "{table}"'):
                walk_strings(list(row), f"{entry['path']}:{table}:row")
    finally:
        connection.close()
    reject_forbidden_text(path.read_bytes().decode("latin1", errors="ignore"), entry["path"])


def verify(root: Path) -> None:
    manifest_path = root / "manifest.json"
    raw_manifest = manifest_path.read_bytes()
    manifest = json.loads(raw_manifest)
    if raw_manifest != canonical_json(manifest):
        raise ValueError("manifest.json is not sorted-key compact canonical JSON")
    if manifest.get("schema_version") != 1:
        raise ValueError("unexpected manifest schema version")
    expected_campaigns = [(item["id"], item["controller"]) for item in CAMPAIGNS]
    actual_campaigns = [(item["id"], item["controller"]) for item in manifest["campaigns"]]
    if actual_campaigns != expected_campaigns:
        raise ValueError("manifest campaign order or controller mismatch")
    expected_paths = {"manifest.json"}
    for campaign in manifest["campaigns"]:
        baseline = campaign["baseline"]
        expected_paths.add(baseline["path"])
        verify_baseline(root, baseline, campaign["id"])
        source_campaign = next(item for item in CAMPAIGNS if item["id"] == campaign["id"])
        expected_cooks = [item["path"] for item in source_campaign["cooks"]]
        if [item["path"] for item in campaign["cooks"]] != expected_cooks:
            raise ValueError(f"manifest cook order mismatch: {campaign['id']}")
        for cook in campaign["cooks"]:
            expected_paths.add(cook["path"])
            verify_archive(root, cook, campaign["id"], campaign["controller"])
    actual_fixture_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "fixture_tool.py" and "__pycache__" not in path.parts
    }
    if actual_fixture_paths != expected_paths:
        raise ValueError(
            f"fixture set mismatch: missing={sorted(expected_paths - actual_fixture_paths)!r} "
            f"extra={sorted(actual_fixture_paths - expected_paths)!r}"
        )
    print(f"verified {len(expected_paths) - 1} fixture files and manifest")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build or verify the sanitized real-cook fixture corpus")
    subparsers = result.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate")
    for option in (
        "diag-aug27",
        "diag-aug27-duplicate",
        "diag-aug28",
        "diag-aug28-duplicate",
        "diag-aug29",
        "cook-aug22",
        "cook-aug27",
        "cook-aug28",
        "cook-aug29-1219",
        "cook-aug29-1625",
        "output",
    ):
        generate_parser.add_argument(f"--{option}", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--fixture-root", required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.command == "generate":
        generate(args)
    else:
        verify(Path(args.fixture_root))


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, KeyError, TypeError, ValueError, zipfile.BadZipFile, sqlite3.Error) as error:
        print(f"fixture error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
