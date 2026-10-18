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

The script drives native jj in a jj workspace, and git otherwise; --jj / --git
force the choice. JJ mode never executes Git commands: it fetches and discovers
tags with jj, creates a lightweight tag with `jj tag set`, and publishes the
bookmark and exact tag only through scripts/exact_revision_gate.py. The updater
uses `git describe --tags`, so lightweight release tags are supported.

JJ requires an empty, mutable working-copy commit before a manifest bump. The
release ID is pinned before tagging, because JJ may automatically open an empty
child when the release becomes immutable. Publication temporarily checks out
that exact commit without rewriting it, so @ and its bookmark agree during the
gate. Afterwards an empty @ is retained or opened, including on gate failure.
--no-push checks the prospective local release, reports the unchanged remote,
and prints a guarded publication command; it does not require origin to advance.

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
import shlex
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
# VCS backends. Release orchestration uses these for every VCS operation.
# ---------------------------------------------------------------------------


class JjVcs:
    name = "jj"

    def __init__(self, runner: Runner):
        self.runner = runner
        self._release_id: str | None = None

    def release_commit(self) -> str:
        """Pin releases by commit ID: tagging may automatically advance @."""
        if self._release_id is not None:
            return self._release_id
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

    def require_clean(self, *, writing: bool = False) -> None:
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
        if (
            writing
            and self.runner.out(
                "jj", "--no-pager", "log", "-r", "@ & immutable()", "--no-graph", "-T", "commit_id"
            ).strip()
        ):
            raise Refused("the jj working copy is immutable; run jj new before bumping the manifest.")

    def committed_versions(self, manifest: Path) -> dict:
        return read_versions(
            self.runner.out("jj", "--no-pager", "file", "show", "-r", self.release_commit(), str(manifest))
        )

    def is_dirty(self) -> bool:
        return bool(self.runner.out("jj", "--no-pager", "diff", "--name-only").strip())

    def commit(self, message: str, bookmark: str, manifest: Path) -> None:
        self.runner.run("jj", "describe", "-m", message)
        self._release_id = self.runner.out(
            "jj", "--no-pager", "log", "-r", "@", "--no-graph", "-T", "commit_id"
        ).strip()
        self.runner.run("jj", "bookmark", "set", bookmark, "-r", self._release_id)

    def fetch(self) -> None:
        self.runner.run("jj", "git", "fetch", "--remote", "origin")

    def measured_against(self, bookmark: str) -> str:
        return f'{json.dumps(bookmark)}@"origin"'

    def latest_merged_tag(self, ref: str) -> str:
        tags = self.runner.out("jj", "--no-pager", "tag", "list", "-r", f"::{ref}", "-T", 'name ++ "\\n"').splitlines()
        return max(tags, key=_version_sort_key, default="")

    def tag_exists(self, tag: str) -> bool:
        tags = self.runner.out("jj", "--no-pager", "tag", "list", "-T", 'name ++ "\\n"').splitlines()
        return tag in tags

    def tag(self, tag: str, commit: str) -> None:
        self.runner.run("jj", "tag", "set", "-r", commit, tag)
        self._release_id = commit

    def describe(self, commit: str) -> str:
        tags = self.runner.out("jj", "--no-pager", "tag", "list", "-r", commit, "-T", 'name ++ "\\n"').splitlines()
        return max(tags, key=_version_sort_key, default=commit[:12])

    def push(self, bookmark: str, tag: str) -> None:
        commit = self.release_commit()
        # Set the bookmark first: mutating commands can automatically open an
        # empty child when @ becomes immutable. Only change the checkout after
        # that, then invoke the gate without any intervening JJ mutations.
        self.runner.run("jj", "bookmark", "set", bookmark, "-r", commit)
        self.runner.run("jj", "--ignore-immutable", "edit", commit)
        self.runner.run(*self._push_command(bookmark, tag))

    def _push_command(self, bookmark: str, tag: str) -> tuple[str, ...]:
        return (
            "uv",
            "--directory",
            self.runner.out("jj", "workspace", "root").strip(),
            "run",
            "python",
            "scripts/exact_revision_gate.py",
            "push",
            "--bookmark",
            bookmark,
            "--artifact-root",
            ".artifacts/exact-revision",
            "--tag",
            tag,
        )

    def push_hint(self, bookmark: str, tag: str) -> str:
        commit = self.release_commit()
        bookmark_command = shlex.join(("jj", "bookmark", "set", bookmark, "-r", commit))
        checkout = shlex.join(("jj", "--ignore-immutable", "edit", commit))
        push = shlex.join(self._push_command(bookmark, tag))
        cleanup = shlex.quote(shlex.join(("jj", "new", "-r", commit)))
        return f"  {bookmark_command} && {checkout} && (trap {cleanup} EXIT; {push})"

    def finish(self) -> None:
        if self._release_id is None:
            return
        current = self.runner.out("jj", "--no-pager", "log", "-r", "@", "--no-graph", "-T", "commit_id").strip()
        if current == self._release_id:
            self.runner.run("jj", "new")
        self._release_id = None


class GitVcs:
    name = "git"

    def __init__(self, runner: Runner):
        self.runner = runner

    def release_commit(self) -> str:
        return self.runner.out("git", "rev-parse", "HEAD").strip()

    def resolve_bookmark(self) -> str:
        command = ("git", "symbolic-ref", "--quiet", "--short", "HEAD")
        return self.runner.out(*command).strip() if self.runner.ok(*command) else ""

    def require_clean(self, *, writing: bool = False) -> None:
        """Nothing to check: the git path commits the manifest by path."""

    def committed_versions(self, manifest: Path) -> dict:
        return read_versions(self.runner.out("git", "show", f"HEAD:{manifest}"))

    def is_dirty(self) -> bool:
        return not self.runner.ok("git", "diff", "--quiet", "--", str(MANIFEST))

    def commit(self, message: str, bookmark: str, manifest: Path) -> None:
        # Only the manifest: this repo often has other work in progress.
        self.runner.run("git", "add", "--", str(manifest))
        self.runner.run("git", "commit", "-q", "-m", message, "--", str(manifest))

    def fetch(self) -> None:
        self.runner.run("git", "fetch", "--tags", "--force")

    def measured_against(self, bookmark: str) -> str:
        if bookmark:
            return f"origin/{bookmark}"
        command = ("git", "symbolic-ref", "--quiet", "--short", "HEAD")
        if self.runner.ok(*command):
            return f"origin/{self.runner.out(*command).strip()}"
        return "HEAD"

    def latest_merged_tag(self, ref: str) -> str:
        tags = self.runner.out("git", "tag", "--sort=v:refname", "--merged", ref).split()
        return tags[-1] if tags else ""

    def tag_exists(self, tag: str) -> bool:
        return self.runner.ok("git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}")

    def tag(self, tag: str, commit: str) -> None:
        self.runner.run("git", "tag", "-a", tag, "-m", tag, commit)

    def describe(self, commit: str) -> str:
        return self.runner.out("git", "describe", "--tags", "--always", commit).strip()

    def push(self, bookmark: str, tag: str) -> None:
        if bookmark:
            self.runner.run("git", "push", "origin", bookmark)
        else:
            print("Detached HEAD: not pushing the commit, only the tag.")
        self.runner.run("git", "push", "origin", f"refs/tags/{tag}")

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


def _version_sort_key(tag: str) -> tuple[str | int, ...]:
    """Numeric components, including devN, sort numerically rather than lexically.

    Keep text components so historical -dev, -bugfix00 and -dev12a tags still
    participate, rather than silently hiding a tag the updater would consider.
    """
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"([0-9]+)", tag))


def report(vcs, manifest: Path, bookmark: str, *, local: bool = False) -> tuple[str, str]:
    ref = vcs.measured_against(bookmark)
    commit = vcs.release_commit()
    described = vcs.describe(commit)
    shown = vcs.latest_merged_tag(ref)
    print()
    print(f"VCS mode         : {vcs.name}{f' (bookmark {bookmark})' if bookmark else ''}")
    print(f"Measured against : {ref}")
    version = read_versions(manifest.read_text())["server"]
    if vcs.name == "jj":
        print(f"Release manifest : v{version}; exact-commit tag/revision: {described}")
        print("Current's git describe may prefer an older annotated tag on the same commit.")
    else:
        print(f"Current: will show v{version} ({described})")
    print(f"Remote:  will show {shown}")
    if local:
        shown = vcs.latest_merged_tag(commit)
        print(f"After publication: {shown} (local release; origin is unchanged)")
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

    if args.increment or args.check:
        # Refresh before deriving or reporting anything. Stale remote refs can
        # otherwise select an existing name or misreport what a deployed
        # checkout on the publication bookmark will show.
        vcs.fetch()

    if args.increment:
        ref = vcs.measured_against(bookmark)
        current_tag = vcs.latest_merged_tag(ref)
        if not current_tag:
            raise Refused(f"cannot auto-increment: no tag is merged into {ref}")
        args.tag = increment_tag(current_tag, args.increment)
        print(f"Latest tag on {ref}: {current_tag}; cutting {args.tag}")

    if args.check:
        report(vcs, manifest, bookmark)
        return 0

    if vcs.tag_exists(args.tag):
        raise Refused(f"tag {args.tag} already exists; choose a new release tag.")

    version = args.version or derive_version(args.tag)
    check_numeric(version)
    build = args.build if args.build is not None else next_build(manifest)

    vcs.require_clean(writing=args.commit)
    try:
        if args.commit:
            write_manifest(manifest, version, build)
            print(f"Set {MANIFEST} to server {version}, build {build}")
            if vcs.is_dirty():
                vcs.commit(f"chore(release): {args.tag} (server {version}, build {build})", bookmark, manifest)
                print(f"Committed {vcs.release_commit()[:12]} and moved {bookmark} to it")
            else:
                print(f"({MANIFEST} was already at those values; nothing to commit)")

        # --tag-only must not tag a commit whose manifest says something else.
        committed = vcs.committed_versions(manifest)["server"]
        if committed != version:
            raise Refused(
                f"the release commit's manifest says server {committed}, not {version}.\n"
                "Commit the manifest bump before tagging, or drop --tag-only."
            )

        commit_id = vcs.release_commit()
        vcs.tag(args.tag, commit_id)
        print(f"Tagged {commit_id[:12]} as {args.tag}")

        if args.push:
            vcs.push(bookmark, args.tag)
            print(f"Pushed {args.tag} to origin")
        else:
            print("Not pushed (--no-push):")
            print(vcs.push_hint(bookmark, args.tag))

        described, shown = report(vcs, manifest, bookmark, local=not args.push and vcs.name == "jj")
        problems = 0
        if described != args.tag:
            print()
            print(f"!! Release commit describes as '{described}', not '{args.tag}'.")
            print("!! Check the tags on the release commit before deploying.")
            problems = 1
        if shown != args.tag:
            print()
            print(f"!! Latest reachable tag is {shown}, not {args.tag}.")
            print("!! The updater selects tags by version order, not creation time.")
            problems = 1
        return problems
    finally:
        if vcs.name == "jj":
            vcs.finish()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refused as refusal:
        print(f"Refusing: {refusal}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as failure:
        print(f"Command failed: {' '.join(failure.cmd)}", file=sys.stderr)
        sys.exit(failure.returncode)
