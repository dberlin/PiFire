"""Unit tests for updater/tag_release.py.

Every test here drives a FakeRunner. Nothing in this file may reach a real git
or jj: the module under test pushes tags and moves bookmarks, so a runner that
fell through to subprocess would publish refs from the test suite.
"""

import importlib.util
import json
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


def test_resolve_bookmark_asks_jj_for_the_bare_name():
    # jj renders a trailing '*' on a bookmark whose local target differs from
    # its remote. Carrying that into the name creates a bookmark git cannot
    # name, and the push dies on 'invalid refspec'. `.name()` is the property
    # that keeps the marker out, so the template is what this pins.
    runner = FakeRunner(replies={BOOKMARK_CMD: "massive-reworks-and-new-ui\n"})
    tag_release.JjVcs(runner).resolve_bookmark()
    template = runner.calls[0][-1]
    assert ".name()" in template


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


def test_jj_commit_seals_the_release_and_moves_the_bookmark():
    runner = FakeRunner(replies={("jj", "--no-pager", "diff"): "updater/updater_manifest.json\n"})
    vcs = tag_release.JjVcs(runner)
    vcs.commit("chore(release): v1.11.0-dev28", "trunk", Path("updater/updater_manifest.json"))
    # jj new seals it: without that the release is still the working copy, and
    # any later edit silently amends a commit about to be tagged and pushed.
    assert runner.ran("jj", "describe")
    assert runner.ran("jj", "new")
    assert runner.ran("jj", "bookmark", "set", "trunk", "-r", "@-")


def test_git_vcs_detached_head_resolves_no_publication_bookmark():
    command = ("git", "symbolic-ref", "--quiet", "--short", "HEAD")
    vcs = tag_release.GitVcs(FakeRunner(ok={command: False}))
    assert vcs.resolve_bookmark() == ""


def test_publication_bookmark_is_measured_from_its_origin_ref():
    runner = FakeRunner()
    assert tag_release.measured_against(runner, "massive-reworks-and-new-ui") == (
        "origin/massive-reworks-and-new-ui"
    )


def test_detached_git_head_without_a_publication_bookmark_is_measured_from_head():
    runner = FakeRunner(ok={("git", "symbolic-ref", "--quiet", "--short", "HEAD"): False})
    assert tag_release.measured_against(runner, "") == "HEAD"


def test_attached_git_head_is_measured_from_its_origin_branch():
    command = ("git", "symbolic-ref", "--quiet", "--short", "HEAD")
    runner = FakeRunner(replies={command: "main\n"}, ok={command: True})
    assert tag_release.measured_against(runner, "") == "origin/main"


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _cut(runner, manifest, argv):
    return tag_release.main(argv, runner=runner, vcs=tag_release.JjVcs(runner), manifest=manifest)


def _release_runner(extra=None):
    replies = {
        BOOKMARK_CMD: "trunk\n",
        ("jj", "--no-pager", "log", "-r", "@-"): "a846d32c852397e95937eb07edb259a450c0920f\n",
        ("jj", "--no-pager", "diff"): ["\n", "updater/updater_manifest.json\n"],
        ("jj", "--no-pager", "file", "show"): json.dumps({"metadata": {"versions": {"server": "1.11.0", "build": 82}}}),
        ("git", "describe"): "v1.11.0-dev28",
        ("git", "tag", "--sort=v:refname", "--merged"): "v1.11.0-dev28",
        ("git", "rev-parse", "--short"): "a846d32c8",
    }
    replies.update(extra or {})
    # origin/trunk exists; the tag being cut does not. Keyed in full, because a
    # short prefix would answer True for the tag-exists probe too.
    return FakeRunner(
        replies=replies,
        ok={("git", "rev-parse", "--verify", "--quiet", "origin/trunk"): True},
    )


def test_a_clean_cut_tags_an_annotated_tag_and_pushes_both_refs(manifest):
    runner = _release_runner()
    assert _cut(runner, manifest, ["v1.11.0-dev28"]) == 0
    # Annotated, not lightweight: git describe prefers annotated tags.
    assert runner.ran("git", "tag", "-a", "v1.11.0-dev28")
    assert runner.ran("jj", "git", "push", "-b", "trunk")
    assert runner.ran("git", "push", "origin", "refs/tags/v1.11.0-dev28")


def test_auto_dev_uses_the_latest_merged_tag_and_runs_the_normal_release(manifest):
    runner = _release_runner(
        extra={
            ("git", "describe"): "v1.11.0-dev29",
            ("git", "tag", "--sort=v:refname", "--merged"): [
                "v1.11.0-dev28",
                "v1.11.0-dev29",
            ],
        }
    )
    assert _cut(runner, manifest, ["--dev"]) == 0
    assert runner.ran("git", "tag", "-a", "v1.11.0-dev29")
    assert runner.ran("git", "push", "origin", "refs/tags/v1.11.0-dev29")
    fetch = runner.calls.index(["git", "fetch", "--tags", "--force"])
    tag = next(index for index, call in enumerate(runner.calls) if call[:3] == ["git", "tag", "-a"])
    commit = next(index for index, call in enumerate(runner.calls) if call[:2] == ["jj", "describe"])
    assert fetch < commit < tag


def test_no_push_leaves_both_refs_local(manifest):
    runner = _release_runner()
    assert _cut(runner, manifest, ["v1.11.0-dev28", "--no-push"]) == 0
    assert runner.ran("git", "tag", "-a", "v1.11.0-dev28")
    assert not runner.ran("jj", "git", "push", "-b", "trunk")
    assert not runner.ran("git", "push", "origin", "refs/tags/v1.11.0-dev28")


def test_an_existing_tag_is_refused_before_anything_is_written(manifest):
    runner = _release_runner()
    runner.oks[("git", "rev-parse", "--verify", "--quiet", "refs/tags/v1.11.0-dev28")] = True
    with pytest.raises(tag_release.Refused, match="already exists"):
        _cut(runner, manifest, ["v1.11.0-dev28"])
    assert json.loads(manifest.read_text())["metadata"]["versions"]["build"] == 81


def test_tag_only_refuses_when_the_committed_manifest_disagrees(manifest):
    runner = _release_runner(
        extra={
            ("jj", "--no-pager", "file", "show"): json.dumps(
                {"metadata": {"versions": {"server": "1.10.0", "build": 5}}}
            )
        }
    )
    with pytest.raises(tag_release.Refused, match="release commit's manifest says"):
        _cut(runner, manifest, ["v1.11.0-dev28", "--tag-only"])
    assert not runner.ran("git", "tag", "-a", "v1.11.0-dev28")


def test_tag_only_does_not_touch_the_manifest(manifest):
    runner = _release_runner()
    assert _cut(runner, manifest, ["v1.11.0-dev28", "--tag-only"]) == 0
    assert json.loads(manifest.read_text())["metadata"]["versions"]["build"] == 81


def test_check_changes_nothing(manifest):
    runner = _release_runner()
    assert _cut(runner, manifest, ["--check"]) == 0
    assert json.loads(manifest.read_text())["metadata"]["versions"]["build"] == 81
    assert not runner.ran("git", "tag", "-a")
    assert not runner.ran("jj", "describe")


def test_check_refreshes_remote_refs_before_reporting(manifest):
    runner = _release_runner()

    assert _cut(runner, manifest, ["--check"]) == 0

    fetch = runner.calls.index(["git", "fetch", "--tags", "--force"])
    report = next(
        index
        for index, call in enumerate(runner.calls)
        if call[:4] == ["git", "tag", "--sort=v:refname", "--merged"]
    )
    assert fetch < report


def test_a_tag_that_version_sorts_below_an_existing_one_exits_nonzero(manifest):
    # --sort=v:refname is a VERSION sort, not creation order: a tag sorting
    # below an existing one is created, pushed, and silently ignored.
    runner = _release_runner(extra={("git", "tag", "--sort=v:refname", "--merged"): "v1.11.0-dev99"})
    assert _cut(runner, manifest, ["v1.11.0-dev28"]) == 1


def test_a_tag_that_is_not_on_the_release_commit_exits_nonzero(manifest):
    runner = _release_runner(extra={("git", "describe"): "v1.11.0-dev28-3-gdeadbee"})
    assert _cut(runner, manifest, ["v1.11.0-dev28"]) == 1


def test_explicit_version_and_build_override_the_derived_ones(manifest):
    runner = _release_runner(
        extra={
            ("jj", "--no-pager", "file", "show"): json.dumps(
                {"metadata": {"versions": {"server": "1.11.2", "build": 80}}}
            )
        }
    )
    # Exit code is not asserted: v1.11-dannyb version-sorts below the existing
    # v1.11.0-dev28, so the self-check correctly reports trap #1 and exits 1.
    _cut(runner, manifest, ["v1.11-dannyb", "--version", "1.11.2", "--build", "80"])
    written = json.loads(manifest.read_text())["metadata"]["versions"]
    assert (written["server"], written["build"]) == ("1.11.2", 80)


def test_a_non_numeric_derived_version_is_refused(manifest):
    runner = _release_runner()
    with pytest.raises(tag_release.Refused, match="not purely numeric"):
        _cut(runner, manifest, ["v1.11-dannyb", "--version", "1.11-dannyb"])
