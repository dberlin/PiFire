from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, Callable, Iterator, Mapping

import pytest
import tools.rebuild_acados as rebuild_module

from tools.rebuild_acados import (
    BuildInputIdentity,
    BuildMode,
    BuildPaths,
    LocalPublicationFilesystem,
    PublicationCollisionError,
    PublicationError,
    RebuildError,
    SolveSample,
    StalenessClass,
    TimingGateError,
    canonical_build_manifest,
    classify_staleness,
    format_timing_evidence,
    publish_immutable_release,
    run_rebuild,
    validate_timing_gate,
)


LIBRARY_DIGEST = "ab" * 32
RUNTIME_GATES = (
    "abi",
    "loader",
    "horizons-5-through-24",
    "invalid-horizons",
    "cost-scaling",
    "cold-solves",
    "warm-solves",
)


def identity() -> BuildInputIdentity:
    return BuildInputIdentity(
        generated_manifest={
            "schema": 1,
            "acados": {
                "url": "https://github.com/acados/acados.git",
                "tag": "v0.6.0",
                "revision": "503364817c872d474ab5bed219c26760ac267769",
                "recursive_dependencies": {
                    "external/blasfeo": "d6251233923c9b475fe894fb729fb63ab693e301",
                    "external/hpipm": "e3a56c1caddd7f12d125d84f337b9a9e5c186271",
                },
            },
            "python_generator_dependencies": {"casadi": "3.7.2", "Cython": "3.2.9"},
            "model_definitions": {"controller/acados/codegen/grey_box_ocp.py": "11" * 32},
            "files": {"grey_box/acados_solver_pifire_grey.c": "22" * 32},
        },
        native_source_sha256={
            "native/src/grey_box.c": "31" * 32,
            "native/include/acados_pifire.h": "32" * 32,
            "native/acados_pifire.exports": "33" * 32,
            "native/acados_pifire.version-script": "34" * 32,
            "CMakeLists.txt": "35" * 32,
            "native/CMakeLists.txt": "36" * 32,
            "native/AcadosPifireExports.cmake": "37" * 32,
            "cmake/AcadosPifirePlatform.cmake": "38" * 32,
        },
        abi_version=2,
        host={"system": "Linux", "machine": "aarch64"},
        compiler={"id": "GNU", "version": "14.2.1", "target": "aarch64-linux-gnu"},
        cmake={
            "version": "3.31.6",
            "generator": "Unix Makefiles",
            "flags": ["CMAKE_BUILD_TYPE=Release", "ACADOS_WITH_OPENMP=ON"],
        },
        loader={
            "platform": "linux",
            "library_filename": "libacados_pifire.so",
            "python_implementation": "cpython",
            "python_version": "3.14",
        },
    )


def mutate_generated(
    value: BuildInputIdentity, mutation: Callable[[dict[str, Any]], None]
) -> BuildInputIdentity:
    generated = dict(deepcopy(value.generated_manifest))
    mutation(generated)
    return replace(value, generated_manifest=generated)


def mutate_file(value: BuildInputIdentity, path: str) -> BuildInputIdentity:
    files = dict(value.native_source_sha256)
    files[path] = "fe" * 32
    return replace(value, native_source_sha256=files)


def test_build_manifest_is_canonical_value_identity_and_timestamps_are_informational() -> None:
    value = identity()
    first = canonical_build_manifest(value, library_sha256=LIBRARY_DIGEST, built_at="2026-08-09T10:00:00Z")
    reordered = replace(value, host=dict(reversed(tuple(value.host.items()))))
    second = canonical_build_manifest(reordered, library_sha256=LIBRARY_DIGEST, built_at="2099-01-01T00:00:00Z")

    canonical = json.dumps(
        {"build_inputs": first["build_inputs"], "library_sha256": LIBRARY_DIGEST},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert first["build_digest"] == hashlib.sha256(canonical).hexdigest()
    assert first["build_digest"] == second["build_digest"]
    assert first["library_sha256"] == LIBRARY_DIGEST
    assert first["built_at"] != second["built_at"]
    assert first["build_inputs"]["generated_manifest"]["acados"] == value.generated_manifest["acados"]
    assert classify_staleness(second, value, actual_library_sha256=LIBRARY_DIGEST) == ()


STALENESS_CASES: tuple[
    tuple[str, Callable[[BuildInputIdentity], BuildInputIdentity], StalenessClass], ...
] = (
    ("acados-pin", lambda v: mutate_generated(v, lambda m: m["acados"].__setitem__("revision", "99" * 20)), StalenessClass.ACADOS_SOURCE),
    ("dependency-revisions", lambda v: mutate_generated(v, lambda m: m["acados"]["recursive_dependencies"].__setitem__("external/hpipm", "98" * 20)), StalenessClass.ACADOS_DEPENDENCIES),
    ("generator-dependencies", lambda v: mutate_generated(v, lambda m: m["python_generator_dependencies"].__setitem__("casadi", "3.7.3")), StalenessClass.GENERATOR_DEPENDENCIES),
    ("generated-provenance", lambda v: mutate_generated(v, lambda m: m["model_definitions"].__setitem__("controller/acados/codegen/grey_box_ocp.py", "97" * 32)), StalenessClass.GENERATED_PROVENANCE),
    ("generated-tree", lambda v: mutate_generated(v, lambda m: m["files"].__setitem__("grey_box/acados_solver_pifire_grey.c", "96" * 32)), StalenessClass.GENERATED_TREE),
    ("wrapper", lambda v: mutate_file(v, "native/src/grey_box.c"), StalenessClass.NATIVE_WRAPPER),
    ("header", lambda v: mutate_file(v, "native/include/acados_pifire.h"), StalenessClass.PUBLIC_HEADER),
    ("exports", lambda v: mutate_file(v, "native/acados_pifire.version-script"), StalenessClass.EXPORT_CONTROLS),
    ("cmake", lambda v: mutate_file(v, "CMakeLists.txt"), StalenessClass.CMAKE_SOURCES),
    ("platform-module", lambda v: mutate_file(v, "cmake/AcadosPifirePlatform.cmake"), StalenessClass.PLATFORM_MAPPING),
    ("abi", lambda v: replace(v, abi_version=3), StalenessClass.ABI),
    ("host", lambda v: replace(v, host={"system": "Darwin", "machine": "arm64"}), StalenessClass.HOST_PLATFORM),
    ("compiler", lambda v: replace(v, compiler={**v.compiler, "id": "Clang"}), StalenessClass.COMPILER),
    ("cmake-flags", lambda v: replace(v, cmake={**v.cmake, "flags": ["CMAKE_BUILD_TYPE=Debug"]}), StalenessClass.CMAKE_CONFIGURATION),
    ("loader", lambda v: replace(v, loader={**v.loader, "library_filename": "libacados_pifire.dylib"}), StalenessClass.LOADER),
)


@pytest.mark.parametrize(("_label", "mutation", "reason"), STALENESS_CASES, ids=[case[0] for case in STALENESS_CASES])
def test_each_build_input_class_has_one_specific_staleness_reason(
    _label: str,
    mutation: Callable[[BuildInputIdentity], BuildInputIdentity],
    reason: StalenessClass,
) -> None:
    installed = canonical_build_manifest(identity(), library_sha256=LIBRARY_DIGEST)
    assert classify_staleness(installed, mutation(identity()), actual_library_sha256=LIBRARY_DIGEST) == (reason,)


def test_library_digest_and_invalid_manifest_have_explicit_staleness_classes() -> None:
    installed = canonical_build_manifest(identity(), library_sha256=LIBRARY_DIGEST)
    assert classify_staleness(installed, identity(), actual_library_sha256="cd" * 32) == (StalenessClass.LIBRARY,)
    assert classify_staleness(None, identity(), actual_library_sha256=None) == (StalenessClass.MISSING_RELEASE,)
    assert classify_staleness({"build_digest": "bad"}, identity(), actual_library_sha256=None) == (StalenessClass.INVALID_MANIFEST,)


class Operations:
    def __init__(self, root: Path, *, stale: tuple[StalenessClass, ...] = (StalenessClass.GENERATED_TREE,), fail_at: str | None = None, lock: threading.Lock | None = None) -> None:
        self.paths = BuildPaths.for_repository(root)
        self.stale = stale
        self.fail_at = fail_at
        self.events: list[str] = []
        self.commands: list[tuple[str, ...]] = []
        self.lines: list[str] = []
        self.current_release: str | None = "a" * 64
        self.lock = lock or threading.Lock()
        self.entered = threading.Event()

    def event(self, name: str) -> None:
        self.events.append(name)
        if name == self.fail_at:
            raise RuntimeError(f"injected {name} failure")

    @contextmanager
    def build_lock(self) -> Iterator[None]:
        with self.lock:
            self.event("lock:enter")
            self.entered.set()
            try:
                yield
            finally:
                self.events.append("lock:exit")

    def inspect_staleness(self) -> tuple[StalenessClass, ...]:
        assert self.entered.is_set()
        self.event("inspect")
        return self.stale

    def configure_and_fetch(self) -> None: self.event("configure-fetch")
    def create_generated_staging(self) -> Path:
        self.event("stage:generated"); return self.paths.generated_staging_root / "candidate"
    def generate(self, command: tuple[str, ...], destination: Path) -> None:
        assert destination.is_relative_to(self.paths.generated_staging_root); self.commands.append(command); self.event("generate")
    def validate_generated(self, gate: str, destination: Path) -> None: self.event(f"gate:{gate}")
    def create_runtime_staging(self) -> Path:
        self.event("stage:runtime"); return self.paths.runtime_staging_root / "candidate"
    def compile_native(self, generated: Path, destination: Path) -> None:
        assert generated in (self.paths.generated_target, self.paths.generated_staging_root / "candidate"); self.event("compile")
    def validate_runtime(self, gate: str, destination: Path) -> None: self.event(f"gate:{gate}")
    def run_timing_gate(self, destination: Path, *, horizons: tuple[int, ...], solves_per_horizon: int) -> tuple[Mapping[str, Any], ...]:
        assert horizons == tuple(range(5, 25)) and solves_per_horizon == 1_000
        self.event("gate:timing")
        return tuple({"horizon": n, "solve_count": 1000, "failure_count": 0, "consecutive_failure_count": 0, "recovery_result": "finite-success", "p99_seconds": 0.001, "maximum_seconds": 0.002, "p99_threshold_seconds": 5.0, "maximum_threshold_seconds": 25.0} for n in range(5, 25))
    def publish_generated(self, destination: Path) -> None: self.event("publish:generated")
    def publish_runtime(self, destination: Path) -> str: self.event("publish:runtime"); return "b" * 64
    def replace_selector(self, digest: str) -> None: self.event("selector:replace"); self.current_release = digest
    def emit(self, line: str) -> None: self.lines.append(line)


FULL_ORDER = [
    "lock:enter", "configure-fetch", "stage:generated", "generate",
    "gate:provenance", "gate:equation", "gate:parity", "stage:runtime", "compile",
    *(f"gate:{gate}" for gate in RUNTIME_GATES), "gate:timing",
    "publish:generated", "publish:runtime", "selector:replace", "lock:exit",
]


def test_full_mode_runs_the_complete_locked_order_and_isolated_codegen_command() -> None:
    operations = Operations(Path("/repository"))
    result = run_rebuild(BuildMode.FULL, operations=operations)
    assert result.changed is True and result.build_digest == "b" * 64
    assert operations.events == FULL_ORDER
    assert operations.commands == [("uv", "run", "--no-default-groups", "--group", "codegen", "python", "-m", "controller.acados.codegen.cli", "--stage", str(operations.paths.generated_staging_root / "candidate"))]


def test_stale_conditional_uses_committed_c_same_gates_and_no_codegen() -> None:
    operations = Operations(Path("/repository"))
    result = run_rebuild(BuildMode.IF_NEEDED, operations=operations)
    assert result.stale_reasons == (StalenessClass.GENERATED_TREE,)
    assert operations.events == [
        "lock:enter", "inspect", "configure-fetch", "stage:runtime", "compile",
        *(f"gate:{gate}" for gate in RUNTIME_GATES), "gate:timing",
        "publish:runtime", "selector:replace", "lock:exit",
    ]
    assert operations.commands == []


def test_conditional_exact_match_is_an_exact_noop() -> None:
    operations = Operations(Path("/repository"), stale=())
    result = run_rebuild(BuildMode.IF_NEEDED, operations=operations)
    assert result.changed is False and result.build_digest == "a" * 64
    assert operations.events == ["lock:enter", "inspect", "lock:exit"]
    assert operations.commands == []
    assert operations.lines == ["acados build inputs exactly match; no rebuild needed"]


def test_paths_stage_on_each_destination_filesystem() -> None:
    paths = BuildPaths.for_repository(Path("/repo"))
    assert paths.generated_staging_root.parent == paths.generated_target.parent
    assert paths.runtime_staging_root.parent == paths.runtime_releases.parent
    assert paths.selector == Path("/repo/controller/_native/current")
    assert paths.lock_file.parent == paths.runtime_releases.parent


def test_full_and_conditional_share_one_lock_before_inspection() -> None:
    lock = threading.Lock()
    release = threading.Event()
    full = Operations(Path("/repo"), lock=lock)
    conditional = Operations(Path("/repo"), stale=(), lock=lock)
    original = full.configure_and_fetch
    def pause() -> None:
        original(); assert release.wait(2)
    full.configure_and_fetch = pause  # type: ignore[method-assign]
    one = threading.Thread(target=run_rebuild, args=(BuildMode.FULL,), kwargs={"operations": full})
    two = threading.Thread(target=run_rebuild, args=(BuildMode.IF_NEEDED,), kwargs={"operations": conditional})
    one.start(); assert full.entered.wait(1); two.start()
    assert not conditional.entered.wait(0.05)
    release.set(); one.join(2); two.join(2)
    assert not one.is_alive() and not two.is_alive()
    assert conditional.events[:2] == ["lock:enter", "inspect"]


@pytest.mark.parametrize("phase", FULL_ORDER[1:-1])
def test_every_failure_before_selector_commit_preserves_prior_release(phase: str) -> None:
    operations = Operations(Path("/repo"), fail_at=phase)
    with pytest.raises(RebuildError, match=phase):
        run_rebuild(BuildMode.FULL, operations=operations)
    assert operations.current_release == "a" * 64
    assert "lock:exit" in operations.events


def samples(*, count: int = 1_000, elapsed: float = 0.05, horizons: range = range(5, 25)) -> dict[int, tuple[SolveSample, ...]]:
    return {h: tuple(SolveSample(status=0, elapsed_seconds=elapsed, result_is_finite=True) for _ in range(count)) for h in horizons}


def test_timing_gate_covers_1000_solves_at_every_integer_horizon_with_exact_evidence() -> None:
    rows = validate_timing_gate(samples(), configured_control_period_seconds=1.0, catalog_minimum_control_period_seconds=0.5)
    assert tuple(row.horizon for row in rows) == tuple(range(5, 25))
    assert all((row.solve_count, row.failure_count, row.consecutive_failure_count, row.recovery_result) == (1000, 0, 0, "finite-success") for row in rows)
    assert all((row.p99_seconds, row.maximum_seconds, row.p99_threshold_seconds, row.maximum_threshold_seconds) == pytest.approx((0.05, 0.05, 0.2, 1.0)) for row in rows)
    assert format_timing_evidence(rows[0]) == "horizon=5 solves=1000 failures=0 consecutive_failures=0 recovery=finite-success p99_seconds=0.050000 maximum_seconds=0.050000 p99_threshold_seconds=0.200000 maximum_threshold_seconds=1.000000"


@pytest.mark.parametrize("matrix", [samples(count=999), samples(count=1001), samples(horizons=range(5, 24))])
def test_timing_gate_rejects_wrong_count_or_missing_horizon(matrix: Mapping[int, tuple[SolveSample, ...]]) -> None:
    with pytest.raises(TimingGateError, match="1,000|horizon"):
        validate_timing_gate(matrix, configured_control_period_seconds=1.0, catalog_minimum_control_period_seconds=0.5)


def altered(replacements: Mapping[int, SolveSample]) -> dict[int, tuple[SolveSample, ...]]:
    matrix = samples(); row = list(matrix[12])
    for index, value in replacements.items(): row[index] = value
    matrix[12] = tuple(row); return matrix


@pytest.mark.parametrize(("replacements", "message"), [
    ({i: SolveSample(4, 0.05, False) for i in (100, 200, 300, 400, 500, 600)}, "five"),
    ({100: SolveSample(4, 0.05, False), 101: SolveSample(4, 0.05, False)}, "consecutive"),
    ({100: SolveSample(4, 0.05, False), 101: SolveSample(0, 0.05, False)}, "finite.*recovery"),
    ({999: SolveSample(4, 0.05, False)}, "recovery"),
])
def test_timing_gate_rejects_failure_and_recovery_violations(replacements: Mapping[int, SolveSample], message: str) -> None:
    with pytest.raises(TimingGateError, match=message):
        validate_timing_gate(altered(replacements), configured_control_period_seconds=1.0, catalog_minimum_control_period_seconds=0.5)


@pytest.mark.parametrize(("elapsed", "message"), [(0.2, "p99"), (1.0, "maximum"), (float("nan"), "finite")])
def test_timing_thresholds_are_strict(elapsed: float, message: str) -> None:
    with pytest.raises(TimingGateError, match=message):
        validate_timing_gate(samples(elapsed=elapsed), configured_control_period_seconds=1.0, catalog_minimum_control_period_seconds=0.5)


def test_settings_absence_uses_catalog_minimum_period() -> None:
    row = validate_timing_gate(samples(elapsed=0.09), configured_control_period_seconds=None, catalog_minimum_control_period_seconds=0.5)[0]
    assert (row.p99_threshold_seconds, row.maximum_threshold_seconds) == pytest.approx((0.1, 0.5))


class PublicationFilesystem:
    def __init__(self, *, fail: str | None = None, collision: str | None = None) -> None:
        self.fail = fail; self.collision = collision; self.events: list[str] = []; self.selected = "a" * 64; self.releases = {self.selected: b"old"}; self.stage_device = self.release_device = 1
    def device_id(self, path: Path) -> int: return self.stage_device if ".staging" in path.parts else self.release_device
    def verify_complete_contained(self, stage: Path) -> tuple[str, bytes]: self.events.append("verify"); return "b" * 64, b"new"
    def fsync_staged_files(self, stage: Path) -> None: self._event("fsync-files")
    def fsync_directory(self, path: Path) -> None: self._event(f"fsync-dir:{path.name}")
    def rename_noreplace(self, stage: Path, release: Path) -> None:
        self._event("rename-noreplace")
        if self.collision is not None: raise FileExistsError(release)
        self.releases[release.name] = b"new"
    def existing_release_bytes(self, release: Path) -> bytes:
        if self.collision == "different":
            return b"different"
        if self.collision == "same":
            return b"new"
        return self.releases[release.name]
    def seal_and_verify_release(self, release: Path, expected: bytes) -> None:
        assert self.existing_release_bytes(release) == expected
        self._event("seal-release")
    def discard_stage(self, stage: Path) -> None: self._event("discard-stage")
    def atomic_replace_selector(self, selector: Path, digest: str) -> None: self._event("selector-replace"); self.selected = digest
    def _event(self, event: str) -> None:
        self.events.append(event)
        if event == self.fail: raise OSError(f"injected {event}")


def test_immutable_publication_fsyncs_before_atomic_selector_and_preserves_prior() -> None:
    fs = PublicationFilesystem()
    selected = publish_immutable_release(Path("/repo/controller/_native/.staging/candidate"), Path("/repo/controller/_native"), filesystem=fs)
    assert selected.name == "b" * 64
    assert fs.events == ["verify", "fsync-files", "fsync-dir:candidate", "rename-noreplace", "seal-release", "fsync-dir:releases", "selector-replace", "fsync-dir:_native"]
    assert fs.selected == "b" * 64 and fs.releases["a" * 64] == b"old"


@pytest.mark.parametrize("failure", ["fsync-files", "fsync-dir:candidate", "rename-noreplace", "seal-release", "fsync-dir:releases", "selector-replace"])
def test_publication_failure_keeps_prior_release_loadable(failure: str) -> None:
    fs = PublicationFilesystem(fail=failure)
    with pytest.raises(PublicationError):
        publish_immutable_release(Path("/repo/controller/_native/.staging/candidate"), Path("/repo/controller/_native"), filesystem=fs)
    assert fs.selected == "a" * 64 and fs.releases[fs.selected] == b"old"


def test_same_release_collision_recovers_but_different_collision_never_overwrites() -> None:
    same = PublicationFilesystem(collision="same")
    publish_immutable_release(Path("/repo/controller/_native/.staging/candidate"), Path("/repo/controller/_native"), filesystem=same)
    assert same.selected == "b" * 64 and "rename-noreplace" in same.events and "discard-stage" in same.events

    different = PublicationFilesystem(collision="different")
    with pytest.raises(PublicationCollisionError):
        publish_immutable_release(Path("/repo/controller/_native/.staging/candidate"), Path("/repo/controller/_native"), filesystem=different)
    assert different.selected == "a" * 64 and different.releases["a" * 64] == b"old"


def test_cross_filesystem_stage_is_rejected_before_inspection_or_mutation() -> None:
    fs = PublicationFilesystem(); fs.stage_device = 1; fs.release_device = 2
    with pytest.raises(PublicationError, match="same filesystem"):
        publish_immutable_release(Path("/repo/controller/_native/.staging/candidate"), Path("/repo/controller/_native"), filesystem=fs)
    assert fs.events == [] and fs.selected == "a" * 64


def test_local_publication_seals_release_only_after_cross_directory_rename(tmp_path: Path) -> None:
    runtime = tmp_path / "_native"
    stage = runtime / ".staging" / "candidate"
    release = runtime / "releases" / ("b" * 64)
    stage.mkdir(parents=True)
    release.parent.mkdir()
    library = stage / "libacados_pifire.so"
    library.write_bytes(b"native")
    library_digest = hashlib.sha256(b"native").hexdigest()
    (stage / "build-manifest.json").write_text(
        json.dumps(
            canonical_build_manifest(
                identity(),
                library_sha256=library_digest,
                built_at="2026-08-09T10:00:00Z",
            )
        ),
        encoding="utf-8",
    )
    filesystem = LocalPublicationFilesystem()

    filesystem.fsync_staged_files(stage)

    assert stage.stat().st_mode & 0o200
    assert library.stat().st_mode & 0o222 == 0
    filesystem.rename_noreplace(stage, release)
    filesystem.seal_and_verify_release(
        release, filesystem.existing_release_bytes(release)
    )
    assert release.is_dir()
    assert release.stat().st_mode & 0o222 == 0



def test_release_collision_identity_ignores_informational_built_at(
    tmp_path: Path,
) -> None:
    filesystem = LocalPublicationFilesystem()
    releases = []
    for name, built_at in (
        ("first", "2026-08-09T00:00:00Z"),
        ("retry", "2099-01-01T00:00:00Z"),
    ):
        release = tmp_path / name
        release.mkdir()
        library = release / "libacados_pifire.so"
        library.write_bytes(b"same native bytes")
        manifest = canonical_build_manifest(
            identity(),
            library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
            built_at=built_at,
        )
        (release / "build-manifest.json").write_text(json.dumps(manifest))
        releases.append(release)
    assert filesystem.existing_release_bytes(
        releases[0]
    ) == filesystem.existing_release_bytes(releases[1])


def test_same_release_collision_is_sealed_before_selector_recovery(
    tmp_path: Path,
) -> None:
    filesystem = LocalPublicationFilesystem()
    runtime = tmp_path / "_native"
    stage = runtime / ".staging/candidate"
    releases = runtime / "releases"
    stage.mkdir(parents=True)
    releases.mkdir()
    library = stage / "libacados_pifire.so"
    library.write_bytes(b"same native bytes")
    manifest = canonical_build_manifest(
        identity(),
        library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
    )
    (stage / "build-manifest.json").write_text(json.dumps(manifest))
    release = releases / str(manifest["build_digest"])
    release.mkdir()
    for source in stage.iterdir():
        (release / source.name).write_bytes(source.read_bytes())
    release.chmod(0o755)
    for path in release.iterdir():
        path.chmod(0o644)

    selected = publish_immutable_release(
        stage, runtime, filesystem=filesystem
    )

    assert selected == release
    assert release.stat().st_mode & 0o222 == 0
    assert all(path.stat().st_mode & 0o222 == 0 for path in release.iterdir())
    assert (runtime / "current").resolve() == release.resolve()


def test_generated_identity_recomputes_changed_tree_and_model_hashes(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "native/generated"
    model = tmp_path / "controller/acados/codegen/grey_box_ocp.py"
    generated_file = generated / "grey_box/solver.c"
    generated_file.parent.mkdir(parents=True)
    model.parent.mkdir(parents=True)
    generated_file.write_text("changed generated bytes")
    model.write_text("changed model bytes")
    manifest = {
        "schema": 1,
        "acados": {},
        "python_generator_dependencies": {},
        "model_definitions": {
            "controller/acados/codegen/grey_box_ocp.py": "0" * 64
        },
        "files": {"grey_box/solver.c": "0" * 64},
    }
    (generated / "manifest.json").write_text(json.dumps(manifest))
    actual = rebuild_module._validated_generated_manifest(tmp_path, generated)
    assert actual["files"]["grey_box/solver.c"] == hashlib.sha256(
        generated_file.read_bytes()
    ).hexdigest()
    assert actual["model_definitions"][
        "controller/acados/codegen/grey_box_ocp.py"
    ] == hashlib.sha256(model.read_bytes()).hexdigest()


def test_selector_release_name_must_equal_manifest_digest() -> None:
    manifest = canonical_build_manifest(identity(), library_sha256=LIBRARY_DIGEST)
    assert rebuild_module._selector_manifest_is_consistent(
        Path(manifest["build_digest"]), manifest
    )
    assert not rebuild_module._selector_manifest_is_consistent(
        Path("f" * 64), manifest
    )




def test_native_output_requires_exact_requested_sequence_length() -> None:
    output = rebuild_module._GreyOutput()
    output.sequence_length = 4
    output.objective = 1.0
    assert not rebuild_module._native_output_is_finite(output, expected_horizon=5)
    output.sequence_length = 5
    assert rebuild_module._native_output_is_finite(output, expected_horizon=5)


def test_cost_cold_and_warm_gates_check_distinct_evidence() -> None:
    cold = rebuild_module.NativeEvidence(
        status=0,
        finite=True,
        objective_matches=True,
        warm_started=False,
    )
    warm = replace(cold, warm_started=True)
    rebuild_module._validate_native_behavior("cost-scaling", cold, warm)
    rebuild_module._validate_native_behavior("cold-solves", cold, warm)
    rebuild_module._validate_native_behavior("warm-solves", cold, warm)
    with pytest.raises(RebuildError, match="cost"):
        rebuild_module._validate_native_behavior(
            "cost-scaling", replace(cold, objective_matches=False), warm
        )
    with pytest.raises(RebuildError, match="cold"):
        rebuild_module._validate_native_behavior("cold-solves", warm, warm)
    with pytest.raises(RebuildError, match="warm"):
        rebuild_module._validate_native_behavior("warm-solves", cold, cold)


def test_active_control_period_uses_settings_and_falls_back_to_catalog(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "controllers.json"
    catalog.write_text(
        json.dumps(
            {
                "metadata": {
                    "mpc": {
                        "config": [
                            {
                                "option_name": "control_period",
                                "option_min": 1.0,
                            }
                        ]
                    }
                }
            }
        )
    )
    database = tmp_path / "pifire.db"
    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO kv VALUES (?, ?)",
            (
                "settings:general",
                json.dumps(
                    {
                        "current": {
                            "controller": {
                                "config": {"mpc": {"control_period": 5.0}}
                            }
                        }
                    }
                ),
            ),
        )
    assert rebuild_module._control_periods(catalog, database) == (5.0, 1.0)
    assert rebuild_module._control_periods(catalog, tmp_path / "missing.db") == (
        None,
        1.0,
    )


def test_configured_compiler_identity_comes_from_cmake_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler_file = tmp_path / "CMakeFiles/3.31/CMakeCCompiler.cmake"
    compiler_file.parent.mkdir(parents=True)
    compiler_file.write_text(
        "\n".join(
            (
                'set(CMAKE_C_COMPILER "/usr/bin/cc")',
                'set(CMAKE_C_COMPILER_ID "GNU")',
                'set(CMAKE_C_COMPILER_VERSION "14.2.1")',
                'set(CMAKE_C_COMPILER_TARGET "")',
            )
        )
    )
    monkeypatch.setattr(
        rebuild_module,
        "_command_compiler_identity",
        lambda _: {
            "id": "GNU",
            "version": "14.2.1",
            "target": "aarch64-linux-gnu",
            "executable": "/usr/bin/gcc",
        },
    )
    assert rebuild_module._configured_compiler_identity(tmp_path) == {
        "id": "GNU",
        "version": "14.2.1",
        "target": "aarch64-linux-gnu",
        "executable": "/usr/bin/gcc",
    }


def test_fetched_source_verification_rejects_dirty_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "acados"
    dependency = source / "external/blasfeo"
    dependency.mkdir(parents=True)
    expected = {
        "acados": {
            "url": "https://github.com/acados/acados.git",
            "revision": "a" * 40,
            "tag": "v0.6.0",
            "recursive_dependencies": {"external/blasfeo": "b" * 40},
        }
    }

    def git(path: Path, *arguments: str) -> str:
        if arguments == ("rev-parse", "HEAD"):
            return "a" * 40
        if arguments == ("remote", "get-url", "origin"):
            return expected["acados"]["url"]
        if arguments == ("describe", "--tags", "--exact-match", "HEAD"):
            return "v0.6.0"
        if arguments == ("submodule", "status", "--recursive"):
            return f" {'b' * 40} external/blasfeo"
        if arguments[0] == "status":
            return " M changed.c" if path == dependency else ""
        raise AssertionError(arguments)

    monkeypatch.setattr(rebuild_module, "_git", git)
    with pytest.raises(RebuildError, match="dirty"):
        rebuild_module._verify_fetched_source(source, expected)


def test_equation_parity_runs_only_in_isolated_codegen_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = rebuild_module.LocalOperations(tmp_path)
    operations._source = tmp_path / "fetched-acados"
    destination = tmp_path / "native/.generated-staging/candidate"
    metadata = destination / "grey_box/pifire_grey.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        json.dumps({"dims": {"N": 24, "nx": 11, "nu": 1, "np": 12}})
    )
    (destination / "manifest.json").write_text(json.dumps({}))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        rebuild_module,
        "_stream",
        lambda command, **_: calls.append(tuple(command)),
    )
    operations.validate_generated("equation", destination)
    assert calls == [
        (
            "uv",
            "run",
            "--no-default-groups",
            "--group",
            "codegen",
            "python",
            "-m",
            "controller.acados.codegen.cli",
            "--equation-parity",
            str(destination),
        )
    ]



def test_configure_resets_ephemeral_generated_and_output_cache_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = rebuild_module.LocalOperations(tmp_path)
    source = tmp_path / "fetched-acados"
    source.mkdir()
    compiler = {
        "id": "GNU",
        "version": "16.1.1",
        "target": "x86_64-linux-gnu",
        "executable": "/usr/bin/gcc",
    }
    calls: list[tuple[str, ...]] = []

    def stream(command: tuple[str, ...], **_: object) -> None:
        calls.append(tuple(command))
        (operations.paths.build_root / "acados-source-dir.txt").write_text(
            str(source), encoding="utf-8"
        )

    monkeypatch.setattr(rebuild_module, "_stream", stream)
    monkeypatch.setattr(rebuild_module, "_requested_compiler_identity", lambda: compiler)
    monkeypatch.setattr(
        rebuild_module, "_configured_compiler_identity", lambda _path: compiler
    )
    monkeypatch.setattr(rebuild_module, "_validated_generated_manifest", lambda *_: {})
    monkeypatch.setattr(rebuild_module, "_verify_fetched_source", lambda *_: None)

    operations.configure_and_fetch()

    command = calls[0]
    assert (
        f"-DACADOS_PIFIRE_GENERATED_ROOT={operations.paths.generated_target}" in command
    )
    assert (
        "-DACADOS_PIFIRE_LIBRARY_OUTPUT_DIRECTORY="
        f"{operations.paths.build_root / 'bootstrap-output'}"
    ) in command

def test_apple_clang_and_clang_share_one_compiler_identity() -> None:
    assert rebuild_module._normalize_compiler_id("AppleClang") == "Clang"
    assert rebuild_module._normalize_compiler_id("Clang") == "Clang"
    assert rebuild_module._normalize_compiler_id("GNU") == "GNU"


def test_timing_database_path_honors_supported_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "custom.db"
    monkeypatch.setenv("PIFIRE_DB_PATH", str(configured))
    assert rebuild_module._settings_database_path(tmp_path) == configured