"""Unit tests for updater/tag_release.py.

Every test here drives a FakeRunner. Nothing in this file may reach a real git
or jj: the module under test pushes tags and moves bookmarks, so a runner that
fell through to subprocess would publish refs from the test suite.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "updater" / "tag_release.py"

spec = importlib.util.spec_from_file_location("tag_release", MODULE_PATH)
tag_release = importlib.util.module_from_spec(spec)
sys.modules["tag_release"] = tag_release
spec.loader.exec_module(tag_release)


class FakeRunner:
    """Answers commands from a canned table and records what it was asked to do.

    Lookup is by the command's leading words, so a test only has to pin the
    part of the argv it cares about. An unmatched capture raises rather than
    returning "", because a silent empty string is how a wrong template ends up
    looking like a legitimately unset bookmark.
    """

    def __init__(self, replies=None, ok=None):
        self.replies = dict(replies or {})
        self.oks = dict(ok or {})
        self.calls = []

    def _match(self, table, cmd):
        for length in range(len(cmd), 0, -1):
            key = tuple(cmd[:length])
            if key in table:
                return table[key]
        return None

    def out(self, *cmd):
        self.calls.append(list(cmd))
        reply = self._match(self.replies, cmd)
        if reply is None:
            raise AssertionError(f"FakeRunner has no reply for {list(cmd)}")
        # A list is a sequence of answers to the same command -- `jj diff` is
        # legitimately asked twice, and must read clean before the manifest is
        # written and dirty after. The last entry sticks.
        if isinstance(reply, list):
            return reply.pop(0) if len(reply) > 1 else reply[0]
        return reply

    def run(self, *cmd):
        self.calls.append(list(cmd))

    def ok(self, *cmd):
        self.calls.append(list(cmd))
        matched = self._match(self.oks, cmd)
        return False if matched is None else matched

    def ran(self, *prefix):
        return any(call[: len(prefix)] == list(prefix) for call in self.calls)


@pytest.fixture
def manifest(tmp_path):
    path = tmp_path / "updater_manifest.json"
    path.write_text(
        json.dumps(
            {
                "metadata": {"versions": {"server": "1.11.0", "build": 81, "cookfile": "1.5.0"}},
                "other": {"keep": "me"},
            },
            indent=2,
        )
        + "\n"
    )
    return path


# --------------------------------------------------------------------------
# Version derivation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v1.11.0-dev28", "1.11.0"),
        ("v1.11-dannyb", "1.11.0"),
        ("v2", "2.0.0"),
        ("1.11.0", "1.11.0"),
        ("v1.11.2", "1.11.2"),
    ],
)
def test_derive_version_strips_prefix_suffix_and_pads(tag, expected):
    assert tag_release.derive_version(tag) == expected


@pytest.mark.parametrize(
    "current,kind,expected",
    [
        ("v1.11.0-dev37", "dev", "v1.11.0-dev38"),
        ("v1.11.0", "dev", "v1.11.0-dev1"),
        ("v1.11.0-dev37", "patch", "v1.11.1"),
        ("v1.11.0-dev37", "minor", "v1.12.0"),
        ("v1.11.0-dev37", "major", "v2.0.0"),
    ],
)
def test_increment_tag_advances_the_requested_component(current, kind, expected):
    assert tag_release.increment_tag(current, kind) == expected


def test_increment_tag_refuses_an_unstructured_latest_tag():
    with pytest.raises(tag_release.Refused, match="cannot auto-increment"):
        tag_release.increment_tag("v1.11-dannyb", "dev")


@pytest.mark.parametrize("version", ["1.11-dannyb", "1.11.0-dev28", "dev", "1..2", ""])
def test_non_numeric_versions_are_rejected(version):
    # semantic_ver_to_list runs int() over each dotted part, so a non-numeric
    # manifest version raises inside settings migration on the next boot.
    with pytest.raises(tag_release.Refused):
        tag_release.check_numeric(version)


@pytest.mark.parametrize("version", ["1.11.0", "1.11.2", "2.0.0", "10.20.30"])
def test_numeric_versions_are_accepted(version):
    tag_release.check_numeric(version)


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------


def test_write_manifest_sets_both_fields_and_keeps_the_rest(manifest):
    tag_release.write_manifest(manifest, "1.12.0", 90)
    written = json.loads(manifest.read_text())
    assert written["metadata"]["versions"]["server"] == "1.12.0"
    assert written["metadata"]["versions"]["build"] == 90
    assert written["metadata"]["versions"]["cookfile"] == "1.5.0"
    assert written["other"] == {"keep": "me"}


def test_write_manifest_keeps_two_space_indent_and_trailing_newline(manifest):
    tag_release.write_manifest(manifest, "1.12.0", 90)
    text = manifest.read_text()
    assert text.endswith("}\n")
    assert '\n  "metadata"' in text


def test_next_build_increments_the_committed_value(manifest):
    assert tag_release.next_build(manifest) == 82


# --------------------------------------------------------------------------
# Bookmark resolution -- the jj backend
# --------------------------------------------------------------------------

BOOKMARK_CMD = ("jj", "--no-pager", "log")


def test_resolve_bookmark_returns_the_single_bookmark_behind_the_working_copy():
    vcs = tag_release.JjVcs(FakeRunner(replies={BOOKMARK_CMD: "massive-reworks-and-new-ui\n"}))
    assert vcs.resolve_bookmark() == "massive-reworks-and-new-ui"


def test_resolve_bookmark_strips_an_out_of_sync_marker_if_one_ever_reappears():
    vcs = tag_release.JjVcs(FakeRunner(replies={BOOKMARK_CMD: "massive-reworks-and-new-ui*\n"}))
    assert vcs.resolve_bookmark() == "massive-reworks-and-new-ui"


def test_resolve_bookmark_refuses_when_several_are_equally_close():
    vcs = tag_release.JjVcs(FakeRunner(replies={BOOKMARK_CMD: "one\ntwo\n"}))
    with pytest.raises(tag_release.Refused, match="cannot tell which bookmark"):
        vcs.resolve_bookmark()


def test_resolve_bookmark_refuses_when_there_is_none():
    vcs = tag_release.JjVcs(FakeRunner(replies={BOOKMARK_CMD: "\n"}))
    with pytest.raises(tag_release.Refused, match="cannot tell which bookmark"):
        vcs.resolve_bookmark()


def test_jj_refuses_to_commit_over_a_dirty_working_copy():
    runner = FakeRunner(replies={("jj", "--no-pager", "diff"): "controller/mpc.py\n"})
    vcs = tag_release.JjVcs(runner)
    with pytest.raises(tag_release.Refused, match="working copy has changes"):
        vcs.require_clean()


def test_jj_clean_working_copy_passes():
    vcs = tag_release.JjVcs(FakeRunner(replies={("jj", "--no-pager", "diff"): "\n"}))
    vcs.require_clean()


def test_jj_commit_keeps_release_at_working_copy_for_the_gate():
    runner = FakeRunner(replies={("jj", "--no-pager", "log", "-r", "@"): "release-id"})
    vcs = tag_release.JjVcs(runner)
    vcs.commit("chore(release): v1.11.0-dev28", "trunk", Path("updater/updater_manifest.json"))
    assert runner.ran("jj", "describe")
    assert not runner.ran("jj", "new")
    assert runner.ran("jj", "bookmark", "set", "trunk", "-r", "release-id")


def test_git_vcs_detached_head_resolves_no_publication_bookmark():
    command = ("git", "symbolic-ref", "--quiet", "--short", "HEAD")
    vcs = tag_release.GitVcs(FakeRunner(ok={command: False}))
    assert vcs.resolve_bookmark() == ""


def test_publication_bookmark_is_measured_from_its_origin_ref():
    runner = FakeRunner()
    assert (
        tag_release.GitVcs(runner).measured_against("massive-reworks-and-new-ui") == "origin/massive-reworks-and-new-ui"
    )


def test_detached_git_head_without_a_publication_bookmark_is_measured_from_head():
    runner = FakeRunner(ok={("git", "symbolic-ref", "--quiet", "--short", "HEAD"): False})
    assert tag_release.GitVcs(runner).measured_against("") == "HEAD"


def test_attached_git_head_is_measured_from_its_origin_branch():
    command = ("git", "symbolic-ref", "--quiet", "--short", "HEAD")
    runner = FakeRunner(replies={command: "main\n"}, ok={command: True})
    assert tag_release.GitVcs(runner).measured_against("") == "origin/main"


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _cut(runner, manifest, argv):
    return tag_release.main(argv, runner=runner, vcs=tag_release.JjVcs(runner), manifest=manifest)


RELEASE_ID = "a846d32c852397e95937eb07edb259a450c0920f"
EMPTY_ID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
REMOTE_TAGS = ("jj", "--no-pager", "tag", "list", "-r", '::"trunk"@"origin"')
LOCAL_TAGS = ("jj", "--no-pager", "tag", "list", "-r", f"::{RELEASE_ID}")
EXACT_TAGS = ("jj", "--no-pager", "tag", "list", "-r", RELEASE_ID)
ALL_TAGS = ("jj", "--no-pager", "tag", "list", "-T")
GATE = (
    "uv",
    "--directory",
    "/workspace/release",
    "run",
    "python",
    "scripts/exact_revision_gate.py",
    "push",
    "--bookmark",
    "trunk",
    "--artifact-root",
    ".artifacts/exact-revision",
    "--tag",
    "v1.11.0-dev28",
)


class ReleaseRunner(FakeRunner):
    """Model JJ advancing @ automatically when its commit becomes tagged."""

    def __init__(self, replies):
        super().__init__(replies=replies)
        self.current = EMPTY_ID

    def out(self, *cmd):
        if cmd[:5] == ("jj", "--no-pager", "log", "-r", "@"):
            self.calls.append(list(cmd))
            return self.current
        return super().out(*cmd)

    def run(self, *cmd):
        super().run(*cmd)
        if cmd[:2] == ("jj", "describe"):
            self.current = RELEASE_ID
        elif cmd[:3] == ("jj", "tag", "set") and self.current == cmd[4]:
            self.current = EMPTY_ID
        elif cmd[:3] == ("jj", "--ignore-immutable", "edit"):
            self.current = cmd[3]
        elif cmd == ("jj", "new"):
            self.current = EMPTY_ID
        elif cmd[0] == "uv" and cmd[3:7] == ("run", "python", "scripts/exact_revision_gate.py", "push"):
            assert self.current == RELEASE_ID, "gate must run on the tagged release, not its automatic child"


def _release_runner(extra=None):
    replies = {
        BOOKMARK_CMD: "trunk\n",
        ("jj", "workspace", "root"): "/workspace/release\n",
        ("jj", "--no-pager", "log", "-r", "@-"): RELEASE_ID,
        ("jj", "--no-pager", "log", "-r", "@"): RELEASE_ID,
        ("jj", "--no-pager", "log", "-r", "@ & immutable()"): "",
        ("jj", "--no-pager", "diff"): ["\n", "updater/updater_manifest.json\n"],
        ("jj", "--no-pager", "file", "show"): json.dumps({"metadata": {"versions": {"server": "1.11.0", "build": 82}}}),
        ALL_TAGS: "v1.11.0-dev27\n",
        REMOTE_TAGS: "v1.11.0-dev28\n",
        LOCAL_TAGS: "v1.11.0-dev28\n",
        EXACT_TAGS: "v1.11.0-dev28\n",
    }
    replies.update(extra or {})
    return ReleaseRunner(replies=replies)


def test_clean_jj_release_tags_and_uses_guard_before_opening_empty_working_copy(manifest):
    runner = _release_runner()
    assert _cut(runner, manifest, ["v1.11.0-dev28"]) == 0
    assert runner.ran("jj", "tag", "set", "-r", RELEASE_ID, "v1.11.0-dev28")
    assert runner.ran(*GATE)
    assert not runner.ran("git")
    assert not runner.ran("jj", "git", "push")
    assert runner.calls.index(list(GATE)) < runner.calls.index(["jj", "new"])


@pytest.mark.parametrize("push", [True, False])
def test_subdirectory_release_binds_gate_and_hint_to_workspace_root(manifest, monkeypatch, capsys, push):
    root = manifest.parent
    subdirectory = root / "nested"
    subdirectory.mkdir()
    monkeypatch.chdir(subdirectory)
    runner = _release_runner({("jj", "workspace", "root"): f"{root}\n"})
    argv = ["v1.11.0-dev28"] if push else ["v1.11.0-dev28", "--no-push"]

    assert _cut(runner, manifest, argv) == 0

    if push:
        assert runner.ran("uv", "--directory", str(root), "run", "python", "scripts/exact_revision_gate.py", "push")
    else:
        assert f"uv --directory {root} run python scripts/exact_revision_gate.py push" in capsys.readouterr().out
        assert not runner.ran("uv")
    assert Path.cwd() == subdirectory


def test_guard_failure_is_not_reported_as_publication_success(manifest, capsys):
    runner = _release_runner()
    original_run = runner.run

    def fail_gate(*cmd):
        original_run(*cmd)
        if cmd == GATE:
            raise subprocess.CalledProcessError(1, cmd)

    runner.run = fail_gate
    with pytest.raises(subprocess.CalledProcessError):
        _cut(runner, manifest, ["v1.11.0-dev28"])
    assert "Pushed" not in capsys.readouterr().out
    assert runner.ran("jj", "new")
    assert not runner.ran("git")


def test_auto_dev_uses_numeric_suffix_order(manifest):
    runner = _release_runner(
        {
            REMOTE_TAGS: ["v1.11.0-dev9\nv1.11.0-dev28\n", "v1.11.0-dev29\n"],
            EXACT_TAGS: "v1.11.0-dev29\n",
        }
    )
    assert _cut(runner, manifest, ["--dev"]) == 0
    assert runner.ran("jj", "tag", "set", "-r", RELEASE_ID, "v1.11.0-dev29")
    fetch = runner.calls.index(["jj", "git", "fetch", "--remote", "origin"])
    commit = next(index for index, call in enumerate(runner.calls) if call[:2] == ["jj", "describe"])
    assert fetch < commit
    assert not runner.ran("git")


def test_auto_minor_uses_numeric_versions_not_lexical_order(manifest):
    runner = _release_runner(
        {
            REMOTE_TAGS: ["v1.30.4\nv1.9.6\nv1.8.0-dev12a\nv1.5.3-bugfix00\n", "v1.31.0\n"],
            EXACT_TAGS: "v1.31.0\n",
            ("jj", "--no-pager", "file", "show"): json.dumps(
                {"metadata": {"versions": {"server": "1.31.0", "build": 82}}}
            ),
        }
    )
    assert _cut(runner, manifest, ["--minor"]) == 0
    written = json.loads(manifest.read_text())["metadata"]["versions"]
    assert (written["server"], written["build"]) == ("1.31.0", 82)
    assert runner.ran("jj", "tag", "set", "-r", RELEASE_ID, "v1.31.0")
    assert not runner.ran("git")


def test_no_push_succeeds_with_old_remote_and_prints_guarded_hint(manifest, capsys):
    runner = _release_runner({REMOTE_TAGS: "v1.10.0\n"})
    assert _cut(runner, manifest, ["v1.11.0-dev28", "--no-push"]) == 0
    assert not runner.ran("uv")
    assert not runner.ran("jj", "git", "push")
    assert not runner.ran("git")
    assert not runner.ran("jj", "new")
    assert runner.current == EMPTY_ID
    output = capsys.readouterr().out
    assert "scripts/exact_revision_gate.py push" in output
    assert "--tag v1.11.0-dev28" in output
    assert "v1.10.0" in output
    assert f"edit {RELEASE_ID}" in output
    assert f"edit {EMPTY_ID}" not in output


def test_an_existing_tag_is_refused_before_anything_is_written(manifest):
    runner = _release_runner({ALL_TAGS: "v1.11.0-dev28\n"})
    before = manifest.read_bytes()
    with pytest.raises(tag_release.Refused, match="already exists"):
        _cut(runner, manifest, ["v1.11.0-dev28"])
    assert manifest.read_bytes() == before
    assert not runner.ran("jj", "describe")
    assert not runner.ran("jj", "new")
    assert not runner.ran("jj", "tag", "set")
    assert not runner.ran("uv")


def test_tag_existence_compares_literal_names_not_patterns(manifest):
    runner = _release_runner({ALL_TAGS: "v1.11.0-dev280\n"})
    assert _cut(runner, manifest, ["v1.11.0-dev28", "--no-push"]) == 0


def test_tag_only_refuses_when_the_committed_manifest_disagrees(manifest):
    runner = _release_runner(
        {
            ("jj", "--no-pager", "file", "show"): json.dumps(
                {"metadata": {"versions": {"server": "1.10.0", "build": 5}}}
            ),
        }
    )
    before = manifest.read_bytes()
    with pytest.raises(tag_release.Refused, match="release commit's manifest says"):
        _cut(runner, manifest, ["v1.11.0-dev28", "--tag-only"])
    assert manifest.read_bytes() == before
    assert not runner.ran("jj", "tag", "set")
    assert not runner.ran("jj", "--ignore-immutable", "edit")


def test_tag_only_checks_out_exact_release_for_guard_without_rewriting_it(manifest):
    runner = _release_runner()
    before = manifest.read_bytes()
    assert _cut(runner, manifest, ["v1.11.0-dev28", "--tag-only"]) == 0
    assert manifest.read_bytes() == before
    assert runner.ran("jj", "--ignore-immutable", "edit", RELEASE_ID)
    assert runner.ran("jj", "bookmark", "set", "trunk", "-r", RELEASE_ID)
    assert runner.ran(*GATE)
    assert runner.ran("jj", "new")
    assert not runner.ran("jj", "describe")
    assert not runner.ran("git")


def test_tag_only_local_release_leaves_existing_empty_working_copy(manifest):
    runner = _release_runner({REMOTE_TAGS: "v1.10.0\n"})
    before = manifest.read_bytes()
    assert _cut(runner, manifest, ["v1.11.0-dev28", "--tag-only", "--no-push"]) == 0
    assert manifest.read_bytes() == before
    assert runner.ran("jj", "tag", "set", "-r", RELEASE_ID, "v1.11.0-dev28")
    assert not runner.ran("jj", "--ignore-immutable", "edit")
    assert not runner.ran("jj", "describe")
    assert not runner.ran("jj", "new")
    assert not runner.ran("uv")
    assert not runner.ran("git")


@pytest.mark.parametrize("options", [[], ["--tag-only"]])
def test_dirty_working_copy_is_preserved_before_release(manifest, options):
    runner = _release_runner({("jj", "--no-pager", "diff"): "controller/mpc.py\n"})
    before = manifest.read_bytes()
    with pytest.raises(tag_release.Refused, match="working copy has changes"):
        _cut(runner, manifest, ["v1.11.0-dev28", *options])
    assert manifest.read_bytes() == before
    assert not runner.ran("jj", "tag", "set")
    assert not runner.ran("jj", "describe")


def test_manifest_bump_refuses_immutable_working_copy_before_writing(manifest):
    runner = _release_runner({("jj", "--no-pager", "log", "-r", "@ & immutable()"): RELEASE_ID})
    before = manifest.read_bytes()
    with pytest.raises(tag_release.Refused, match="immutable"):
        _cut(runner, manifest, ["v1.11.0-dev28"])
    assert manifest.read_bytes() == before
    assert not runner.ran("jj", "describe")


def test_check_refreshes_native_remote_refs_without_release_writes(manifest, capsys):
    runner = _release_runner()
    before = manifest.read_bytes()
    assert _cut(runner, manifest, ["--check"]) == 0
    assert manifest.read_bytes() == before
    assert runner.ran("jj", "git", "fetch", "--remote", "origin")
    fetch = runner.calls.index(["jj", "git", "fetch", "--remote", "origin"])
    report = next(index for index, call in enumerate(runner.calls) if call[: len(REMOTE_TAGS)] == list(REMOTE_TAGS))
    assert fetch < report
    assert "v1.11.0-dev28" in capsys.readouterr().out
    assert not runner.ran("jj", "tag", "set")
    assert not runner.ran("jj", "describe")
    assert not runner.ran("jj", "new")
    assert not runner.ran("git")


def test_a_tag_that_version_sorts_below_an_existing_one_exits_nonzero(manifest):
    runner = _release_runner({REMOTE_TAGS: "v1.11.0-dev99"})
    assert _cut(runner, manifest, ["v1.11.0-dev28"]) == 1


def test_local_cut_still_refuses_a_tag_hidden_by_higher_local_version(manifest):
    runner = _release_runner({LOCAL_TAGS: "v1.11.0-dev99\nv1.11.0-dev28"})
    assert _cut(runner, manifest, ["v1.11.0-dev28", "--no-push"]) == 1


def test_a_tag_that_is_not_on_the_release_commit_exits_nonzero(manifest):
    runner = _release_runner({EXACT_TAGS: ""})
    assert _cut(runner, manifest, ["v1.11.0-dev28"]) == 1


def test_explicit_version_and_build_override_the_derived_ones(manifest):
    runner = _release_runner(
        {
            ("jj", "--no-pager", "file", "show"): json.dumps(
                {"metadata": {"versions": {"server": "1.11.2", "build": 80}}}
            ),
        }
    )
    _cut(runner, manifest, ["v1.11-dannyb", "--version", "1.11.2", "--build", "80"])
    written = json.loads(manifest.read_text())["metadata"]["versions"]
    assert (written["server"], written["build"]) == ("1.11.2", 80)


def test_a_non_numeric_derived_version_is_refused(manifest):
    runner = _release_runner()
    with pytest.raises(tag_release.Refused, match="not purely numeric"):
        _cut(runner, manifest, ["v1.11-dannyb", "--version", "1.11-dannyb"])


def test_git_release_retains_annotated_tag_and_separate_ref_pushes(manifest):
    runner = FakeRunner(
        replies={
            ("git", "symbolic-ref"): "main\n",
            ("git", "rev-parse", "HEAD"): RELEASE_ID,
            ("git", "show"): json.dumps({"metadata": {"versions": {"server": "1.11.0", "build": 82}}}),
            ("git", "describe"): "v1.11.0-dev28",
            ("git", "tag", "--sort=v:refname", "--merged"): "v1.11.0-dev28",
        },
        ok={("git", "symbolic-ref"): True},
    )
    assert tag_release.main(["v1.11.0-dev28"], runner=runner, vcs=tag_release.GitVcs(runner), manifest=manifest) == 0
    assert runner.ran("git", "tag", "-a", "v1.11.0-dev28", "-m", "v1.11.0-dev28", RELEASE_ID)
    assert runner.ran("git", "push", "origin", "main")
    assert runner.ran("git", "push", "origin", "refs/tags/v1.11.0-dev28")
    assert not runner.ran("jj")
    assert not runner.ran("uv")
