#!/usr/bin/env python3
"""Cut a release: bump the manifest, commit it, tag that commit, push, and check
that the updater page will actually show what you asked for.

    updater/tag_release.py v1.11-dannyb              # derive 1.11.0, bump build, commit, tag, push
    updater/tag_release.py v1.11-dannyb --version 1.11.2 --build 80
    updater/tag_release.py v1.11-dannyb --tag-only   # manifest already committed; just tag
    updater/tag_release.py v1.11-dannyb --no-push    # local only
    updater/tag_release.py --check                   # what shows today, change nothing
    updater/tag_release.py --dev                     # latest dev tag + 1
    updater/tag_release.py --patch                   # X.Y.Z -> X.Y.(Z+1)
    updater/tag_release.py --minor                   # X.Y.Z -> X.(Y+1).0
    updater/tag_release.py --major                   # X.Y.Z -> (X+1).0.0

    updater/tag_release.py v1.11-dannyb --git        # force the git path in a jj checkout
    updater/tag_release.py v1.11-dannyb --bookmark b # jj: which bookmark to move and push

This repo is a jj workspace colocated with git, so the script drives jj when it
finds one and git otherwise; --jj / --git force the choice. The difference
matters: `git commit` in a colocated repo silently works, and leaves jj to
reconcile a commit that appeared underneath it. jj mode does the same job with
`jj describe` + `jj new` + `jj bookmark set` + `jj git push` instead.

TAGS ARE ALWAYS GIT, in both modes. jj has no tag support at all, and the two
things that decide the updater page's "Remote:" line -- `git describe` and
`git tag --sort=v:refname --merged` -- are git-side by nature. jj mode only
changes how the COMMIT is made and pushed.

The updater page's version line is TWO independent things:

    Current: v<metadata.versions.server from updater/updater_manifest.json>
             (<git describe --tags --always>)
    Remote:  <last entry of `git tag --sort=v:refname --merged <ref>`>

So a tag alone moves only half of it, and `git describe` appends -<n>-g<sha>
unless the tag is ON the commit you are running -- which is why the manifest
bump is committed FIRST and the tag goes on that commit.

Two traps this checks for you:

  * `--sort=v:refname` is a VERSION sort, not creation order. A tag whose name
    sorts below an existing one is created, pushed, and silently ignored --
    v1.11-dannyb sorts BELOW v1.11.0-dev17, because git compares 1.11 against
    1.11.0 long before it reaches the suffix.
  * common/common.py's semantic_ver_to_list runs int() over each dotted part of
    the manifest version, so it must be PURELY NUMERIC. A "1.11-dannyb" in
    there raises ValueError inside settings migration on the next boot.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MANIFEST = Path("updater/updater_manifest.json")


class Refused(Exception):
    """A precondition that must stop the cut before anything is published."""


# ---------------------------------------------------------------------------
# Command runner. Every subprocess in this file goes through one of these three
# methods, so the tests can substitute a fake and no test run can publish a ref.
# ---------------------------------------------------------------------------


class Runner:
    def out(self, *cmd: str) -> str:
        """Stdout of a command that must succeed."""
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout

    def run(self, *cmd: str) -> None:
        """A command run for its effect, with output left on the terminal."""
        subprocess.run(cmd, check=True)

    def ok(self, *cmd: str) -> bool:
        """Whether a command succeeded, discarding its output."""
        return subprocess.run(cmd, capture_output=True, check=False).returncode == 0


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def derive_version(tag: str) -> str:
    """1.11.0 from v1.11-dannyb: strip a leading v, cut at the first '-', pad to three parts."""
    version = tag.removeprefix("v").split("-", 1)[0]
    parts = version.split(".")
    return ".".join(parts + ["0"] * (3 - len(parts))) if len(parts) < 3 else version


_RELEASE_TAG = re.compile(r"^v?([0-9]+)\.([0-9]+)\.([0-9]+)(?:-dev([0-9]+))?$")


def increment_tag(current: str, kind: str) -> str:
    """Advance one component of a conventional release tag."""
    match = _RELEASE_TAG.fullmatch(current)
    if match is None:
        raise Refused(f"cannot auto-increment latest tag {current!r}; expected vX.Y.Z or vX.Y.Z-devN.")
    major, minor, patch = (int(value) for value in match.group(1, 2, 3))
    dev = int(match.group(4)) if match.group(4) is not None else None
    if kind == "dev":
        return f"v{major}.{minor}.{patch}-dev{1 if dev is None else dev + 1}"
    if kind == "patch":
        return f"v{major}.{minor}.{patch + 1}"
    if kind == "minor":
        return f"v{major}.{minor + 1}.0"
    if kind == "major":
        return f"v{major + 1}.0.0"
    raise ValueError(f"unknown increment kind: {kind}")


def check_numeric(version: str) -> None:
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)*", version):
        raise Refused(
            f"manifest version {version!r} is not purely numeric.\n"
            "semantic_ver_to_list() runs int() over each dotted part, so this would\n"
            "raise inside settings migration on the next boot. Pass --version X.Y.Z."
        )


def read_versions(text: str) -> dict:
    return json.loads(text)["metadata"]["versions"]


def next_build(manifest: Path) -> int:
    return int(read_versions(manifest.read_text())["build"]) + 1


def write_manifest(manifest: Path, version: str, build: int) -> None:
    loaded = json.loads(manifest.read_text())
    loaded["metadata"]["versions"]["server"] = version
    loaded["metadata"]["versions"]["build"] = build
    manifest.write_text(json.dumps(loaded, indent=2) + "\n")


# ---------------------------------------------------------------------------
# VCS backends. Everything below talks to these, never to jj/git directly,
# except where the comment says the operation is git-only by nature.
# ---------------------------------------------------------------------------


class JjVcs:
    name = "jj"

    def __init__(self, runner: Runner):
        self.runner = runner

    def release_commit(self) -> str:
        """The commit the tag goes on.

        jj's @ is the working copy, so the release is its parent once `jj new`
        has sealed it; naming the commit explicitly beats relying on HEAD having
        been exported yet.
        """
        return self.runner.out("jj", "--no-pager", "log", "-r", "@-", "--no-graph", "-T", "commit_id").strip()

    def resolve_bookmark(self) -> str:
        """The nearest bookmark behind @.

        In jj this is NOT implied by the working copy -- bookmarks do not follow
        new commits the way git branches do. `.name()` is what makes this safe:
        rendering a bookmark directly appends a '*' when its local target
        differs from the remote, and that character cannot appear in a git ref.
        """
        raw = self.runner.out(
            "jj",
            "--no-pager",
            "log",
            "-r",
            "heads(::@ & bookmarks())",
            "--no-graph",
            "-T",
            'local_bookmarks.map(|b| b.name()).join("\\n") ++ "\\n"',
        )
        # The rstrip("*") is belt and braces behind `.name()`: a template that
        # ever renders the marker again would otherwise name a git ref that
        # cannot exist, and the failure lands on the push, after the tag.
        found = [line.strip().rstrip("*") for line in raw.splitlines() if line.strip()]
        if len(found) != 1:
            raise Refused(
                f"cannot tell which bookmark to move (found: {', '.join(found) or 'none'}).\nPass --bookmark <name>."
            )
        return found[0]

    def require_clean(self) -> None:
        """A release commit must carry only the manifest.

        jj has no path-scoped commit, so instead of splitting the change out
        afterwards the working copy is required to be clean going in -- which
        also stops a release commit quietly carrying someone's half-finished
        edit.
        """
        dirty = self.runner.out("jj", "--no-pager", "diff", "--name-only").strip()
        if dirty:
            raise Refused(
                "the jj working copy has changes, and a release commit must carry\n"
                "only the manifest. Describe or abandon them first, then re-run:\n"
                + "\n".join(f"  {line}" for line in dirty.splitlines())
            )

    def committed_versions(self, manifest: Path) -> dict:
        return read_versions(self.runner.out("jj", "--no-pager", "file", "show", "-r", "@-", str(manifest)))

    def is_dirty(self) -> bool:
        return bool(self.runner.out("jj", "--no-pager", "diff", "--name-only").strip())

    def commit(self, message: str, bookmark: str, manifest: Path) -> None:
        self.runner.run("jj", "describe", "-m", message)
        # Seals it: @ becomes a fresh empty commit and the release is @-, which
        # is what release_commit and committed_versions read. Without this the
        # release would still be the working copy, and any later edit would
        # silently amend a commit that is about to be tagged and pushed.
        self.runner.run("jj", "new")
        self.runner.run("jj", "bookmark", "set", bookmark, "-r", "@-")

    def push(self, bookmark: str) -> None:
        self.runner.run("jj", "git", "push", "-b", bookmark)

    def push_hint(self, bookmark: str, tag: str) -> str:
        return f"  jj git push -b {bookmark} && git push origin refs/tags/{tag}"


class GitVcs:
    name = "git"

    def __init__(self, runner: Runner):
        self.runner = runner

    def release_commit(self) -> str:
        return self.runner.out("git", "rev-parse", "HEAD").strip()

    def resolve_bookmark(self) -> str:
        command = ("git", "symbolic-ref", "--quiet", "--short", "HEAD")
        return self.runner.out(*command).strip() if self.runner.ok(*command) else ""

    def require_clean(self) -> None:
        """Nothing to check: the git path commits the manifest by path."""

    def committed_versions(self, manifest: Path) -> dict:
        return read_versions(self.runner.out("git", "show", f"HEAD:{manifest}"))

    def is_dirty(self) -> bool:
        return not self.runner.ok("git", "diff", "--quiet", "--", str(MANIFEST))

    def commit(self, message: str, bookmark: str, manifest: Path) -> None:
        # Only the manifest: this repo often has other work in progress.
        self.runner.run("git", "add", "--", str(manifest))
        self.runner.run("git", "commit", "-q", "-m", message, "--", str(manifest))

    def push(self, bookmark: str) -> None:
        if not bookmark:
            print("Detached HEAD: not pushing the commit, only the tag.")
            return
        self.runner.run("git", "push", "origin", bookmark)

    def push_hint(self, bookmark: str, tag: str) -> str:
        return f"  git push origin HEAD refs/tags/{tag}"


def detect_vcs(runner: Runner, forced: str | None) -> JjVcs | GitVcs:
    """Chosen before the first chdir so a jj workspace that is not colocated
    still finds its own root rather than a parent repo's."""
    if forced == "git":
        return GitVcs(runner)
    if forced == "jj" or runner.ok("jj", "workspace", "root"):
        return JjVcs(runner)
    return GitVcs(runner)


def repo_root(runner: Runner, vcs) -> Path:
    if vcs.name == "jj":
        return Path(runner.out("jj", "workspace", "root").strip())
    return Path(runner.out("git", "rev-parse", "--show-toplevel").strip())


# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="updater/tag_release.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("tag", nargs="?", help="the release tag, e.g. v1.11.0-dev28")
    increments = parser.add_mutually_exclusive_group()
    increments.add_argument("--dev", dest="increment", action="store_const", const="dev")
    increments.add_argument("--patch", dest="increment", action="store_const", const="patch")
    increments.add_argument("--minor", dest="increment", action="store_const", const="minor")
    increments.add_argument("--major", dest="increment", action="store_const", const="major")
    parser.add_argument("--version", help="manifest server version (default: derived from the tag)")
    parser.add_argument("--build", type=int, help="manifest build number (default: current + 1)")
    parser.add_argument("--bookmark", help="jj: which bookmark to move and push")
    parser.add_argument("--check", action="store_true", help="report what shows today, change nothing")
    parser.add_argument("--no-push", dest="push", action="store_false", help="cut locally only")
    parser.add_argument("--tag-only", dest="commit", action="store_false", help="manifest already committed")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--jj", dest="vcs", action="store_const", const="jj")
    mode.add_argument("--git", dest="vcs", action="store_const", const="git")
    parser.set_defaults(push=True, commit=True, vcs=None, increment=None)
    args = parser.parse_args(argv)
    if args.check:
        if args.tag or args.increment:
            parser.error("--check cannot be combined with a tag or increment option")
    elif bool(args.tag) == bool(args.increment):
        parser.error("pass exactly one tag or increment option (--dev, --patch, --minor, --major)")
    return args


def measured_against(runner: Runner) -> str:
    """Match updater.get_remote_version(): the active Git branch, or HEAD.

    A jj publication bookmark is not evidence that colocated Git HEAD names
    that branch. In the normal jj workspace HEAD is detached, so the updater
    measures tags reachable from HEAD.
    """
    command = ("git", "symbolic-ref", "--quiet", "--short", "HEAD")
    if runner.ok(*command):
        return f"origin/{runner.out(*command).strip()}"
    return "HEAD"


def latest_merged_tag(runner: Runner, ref: str) -> str:
    tags = runner.out("git", "tag", "--sort=v:refname", "--merged", ref).split()
    return tags[-1] if tags else ""


def report(runner: Runner, vcs, manifest: Path, bookmark: str) -> tuple[str, str]:
    ref = measured_against(runner)
    commit = vcs.release_commit()
    described = runner.out("git", "describe", "--tags", "--always", commit).strip()
    shown = latest_merged_tag(runner, ref)
    print()
    print(f"VCS mode         : {vcs.name}{f' (bookmark {bookmark})' if bookmark else ''}")
    print(f"Measured against : {ref}")
    print(f"Current: will show v{read_versions(manifest.read_text())['server']} ({described})")
    print(f"Remote:  will show {shown}")
    return described, shown


def main(argv=None, runner=None, vcs=None, manifest=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    runner = runner or Runner()
    if vcs is None:
        vcs = detect_vcs(runner, args.vcs)
        root = repo_root(runner, vcs)
        manifest = manifest or root / MANIFEST
    manifest = manifest or MANIFEST

    bookmark = args.bookmark or vcs.resolve_bookmark()

    if args.increment:
        # Refresh before deriving or writing anything. A stale local tag set
        # could otherwise choose a name that already exists remotely, publish
        # the release commit, then fail only when the duplicate tag is pushed.
        runner.run("git", "fetch", "--tags", "--force")
        ref = measured_against(runner)
        current_tag = latest_merged_tag(runner, ref)
        if not current_tag:
            raise Refused(f"cannot auto-increment: no tag is merged into {ref}")
        args.tag = increment_tag(current_tag, args.increment)
        print(f"Latest tag on {ref}: {current_tag}; cutting {args.tag}")

    if args.check:
        report(runner, vcs, manifest, bookmark)
        return 0

    if runner.ok("git", "rev-parse", "--verify", "--quiet", f"refs/tags/{args.tag}"):
        raise Refused(
            f"tag {args.tag} already exists. Delete it first:\n"
            f"  git tag -d {args.tag} && git push origin :refs/tags/{args.tag}"
        )

    version = args.version or derive_version(args.tag)
    check_numeric(version)
    build = args.build if args.build is not None else next_build(manifest)

    if args.commit:
        vcs.require_clean()
        write_manifest(manifest, version, build)
        print(f"Set {MANIFEST} to server {version}, build {build}")
        if vcs.is_dirty():
            vcs.commit(f"chore(release): {args.tag} (server {version}, build {build})", bookmark, manifest)
            print(f"Committed {vcs.release_commit()[:12]} and moved {bookmark} to it")
        else:
            print(f"({MANIFEST} was already at those values; nothing to commit)")

    # Checked rather than assumed, so --tag-only cannot quietly tag a commit
    # whose manifest says something else.
    committed = vcs.committed_versions(manifest)["server"]
    if committed != version:
        raise Refused(
            f"the release commit's manifest says server {committed}, not {version}.\n"
            "Commit the manifest bump before tagging, or drop --tag-only."
        )

    # Git, in both modes: jj has no tags. Annotated, not lightweight -- `git
    # describe`, which is what the page's "Current:" field runs, prefers
    # annotated tags and ignores lightweight ones unless asked. Named explicitly
    # rather than tagging HEAD, so jj mode tags the commit it just sealed even
    # if HEAD has not been exported.
    commit_id = vcs.release_commit()
    runner.run("git", "tag", "-a", args.tag, "-m", args.tag, commit_id)
    print(f"Tagged {runner.out('git', 'rev-parse', '--short', commit_id).strip()} as {args.tag}")

    if args.push:
        vcs.push(bookmark)
        runner.run("git", "push", "origin", f"refs/tags/{args.tag}")
        print(f"Pushed {args.tag} to origin")
    else:
        print("Not pushed (--no-push):")
        print(vcs.push_hint(bookmark, args.tag))

    described, shown = report(runner, vcs, manifest, bookmark)

    problems = 0
    if described != args.tag:
        print()
        print(f"!! git describe says '{described}', not '{args.tag}' -- the tag is not on the")
        print("!! commit you are running, so Current: will carry a -N-g<sha> suffix.")
        problems = 1
    if shown != args.tag:
        print()
        print(f"!! Remote: will keep showing {shown} -- it version-sorts above {args.tag}.")
        print("!! Compare with:  git tag --sort=v:refname | tail -5")
        problems = 1
    return problems


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refused as refusal:
        print(f"Refusing: {refusal}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as failure:
        print(f"Command failed: {' '.join(failure.cmd)}", file=sys.stderr)
        sys.exit(failure.returncode)
