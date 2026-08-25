"""What the updater does when the checkout is not on a branch.

Every git command the updater builds is relative to `origin/{branch}`, and the
branch came from parsing the starred line of `git branch -a`. On a detached
HEAD git prints `* (HEAD detached at abc1234)` there -- prose, not a ref -- and
that string was handed out as a branch name. Every caller then asked git for
`origin/(HEAD detached at abc1234)`, and the updater page interpolated it,
unquoted, into a shell command whose parentheses the shell refused to parse: the
detached process never started, so nothing ever wrote to the install status and
the page sat on "Starting Update..." indefinitely.

These run against real git repositories in tmp_path. Nothing here touches the
PiFire checkout, and no updater subprocess is ever launched.
"""

import os
import subprocess

import pytest

import updater


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def checkout(tmp_path):
    """A clone one commit behind its origin, still on `main`."""
    origin = tmp_path / "origin"
    work = tmp_path / "work"
    origin.mkdir()
    git("init", "-q", "-b", "main", cwd=origin)
    git("config", "user.email", "t@example.com", cwd=origin)
    git("config", "user.name", "Test", cwd=origin)
    for text, message in (("one", "first"), ("two", "second")):
        (origin / "a.txt").write_text(text)
        git("add", "-A", cwd=origin)
        git("commit", "-qm", message, cwd=origin)
    git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    return work


@pytest.fixture
def detached(checkout):
    """The same clone, parked on a commit rather than a branch."""
    sha = git("rev-parse", "HEAD~1", cwd=checkout).stdout.strip()
    git("checkout", "-q", "--detach", sha, cwd=checkout)
    return checkout


@pytest.fixture
def in_checkout(monkeypatch):
    """updater.py's git calls inherit the process cwd, so tests move into the
    repository under test rather than passing a path."""

    def enter(path):
        monkeypatch.chdir(path)

    return enter


def test_a_branch_checkout_reports_its_branch(checkout, in_checkout):
    in_checkout(checkout)

    assert updater.get_branch() == ("main", "")


def test_a_detached_checkout_is_an_error_not_a_branch_name(detached, in_checkout):
    in_checkout(detached)
    branch, error = updater.get_branch()

    assert "ERROR" in branch
    assert error, "a detached HEAD has to carry a reason, not just an error marker"
    assert "detached" in error.lower()


def test_the_error_names_the_commit_and_the_remedy(detached, in_checkout):
    """The message reaches the browser through /api/update/pull's 502, so it is
    the only thing the operator gets to act on."""
    in_checkout(detached)
    sha = git("rev-parse", "--short", "HEAD", cwd=detached).stdout.strip()
    _, error = updater.get_branch()

    assert sha in error
    assert "check out a branch" in error.lower()


def test_the_detached_placeholder_is_not_offered_as_a_branch(detached, in_checkout):
    """It reached the updater page's branch picker, where selecting it posted a
    target that git cannot check out."""
    in_checkout(detached)
    branches, _ = updater.get_available_branches()

    assert not any(b.startswith("(") for b in branches), branches
    assert "main" in branches


def test_detached_head_reports_none_on_a_branch(checkout, in_checkout):
    in_checkout(checkout)

    assert updater.detached_head() is None


def test_detached_head_reports_the_commit_when_parked(detached, in_checkout):
    in_checkout(detached)
    sha = git("rev-parse", "--short", "HEAD", cwd=detached).stdout.strip()

    assert updater.detached_head() == sha


# ---------------------------------------------------------------------------
# The readers built on top of it. None of these may raise, and none may report
# success: an update that cannot resolve a branch has to say so.


def test_the_log_reader_refuses_rather_than_asking_git_for_a_prose_ref(detached, in_checkout):
    in_checkout(detached)
    result, error = updater.get_log(2)

    assert "ERROR" in result
    assert error
    assert "ambiguous argument" not in error, "git was asked for origin/(HEAD detached at ...)"


def test_the_update_check_fails_cleanly(detached, in_checkout):
    in_checkout(detached)
    result = updater.get_available_updates()

    assert result["success"] is False
    assert "ambiguous argument" not in result["message"]


def test_do_update_reports_the_branch_error_instead_of_raising(detached, in_checkout):
    """`error_msg` was only bound inside the success branch, so this path raised
    UnboundLocalError -- inside a detached process, where nothing sees it."""
    in_checkout(detached)
    result, error = updater.do_update()

    assert "ERROR" in result
    assert "detached" in error.lower()


def test_install_update_does_not_claim_success_on_a_detached_head(detached, in_checkout):
    in_checkout(detached)
    success, _status, output = updater.install_update()

    assert success is False
    assert "detached" in output.lower(), (
        "the operator is told to check their git install otherwise, which is not the problem"
    )


def test_get_update_data_survives_a_detached_head(detached, in_checkout):
    """It is what /api/update/state renders the whole page from."""
    in_checkout(detached)
    data = updater.get_update_data({"versions": {"server": "1.10.9"}})

    assert not any(b.startswith("(") for b in data["branches"])
    assert isinstance(data["version"], str)


def test_a_branch_checkout_still_reads_its_log_and_commit_count(checkout, in_checkout):
    """The negative side of all of the above: nothing here refuses a checkout
    that IS on a branch."""
    in_checkout(checkout)
    os.environ.pop("GIT_DIR", None)

    result, error = updater.get_log(2)
    assert error == ""
    assert "second" in result

    avail = updater.get_available_updates()
    assert avail["success"] is True
    assert avail["commits_behind"] == 0


# ---------------------------------------------------------------------------
# The version the page shows, and the log it renders.


def test_the_log_is_plain_text_not_html(checkout, in_checkout):
    """It is JSON served into a <pre>. The `<br>`s this used to substitute for
    newlines were written for a Flask template that no longer calls it, and
    reached the browser as four literal characters between every commit."""
    in_checkout(checkout)
    result, error = updater.get_log(2)

    assert error == ""
    assert "<br>" not in result
    assert result.count("\n") >= 2, "one commit per line"


def test_the_remote_version_is_read_from_history_on_a_detached_head(detached, in_checkout):
    """A detached checkout has no origin/<branch> to ask about, but it does have
    a history -- and reporting an error blanks the version on a checkout that
    can perfectly well name its own."""
    in_checkout(detached)
    git("tag", "-a", "v1.11-test", "-m", "v1.11-test", cwd=detached)

    result, error = updater.get_remote_version()

    assert result == "v1.11-test"
    assert error == ""


def test_the_remote_version_still_follows_the_branch_when_on_one(checkout, in_checkout):
    in_checkout(checkout)
    git("tag", "-a", "v1.11-test", "-m", "v1.11-test", cwd=checkout)

    result, error = updater.get_remote_version()

    assert result == "v1.11-test"
    assert error == ""


def test_a_tag_only_on_a_later_commit_is_not_claimed_by_a_detached_head(detached, in_checkout):
    """--merged is doing real work: HEAD is one commit back, so a tag on the tip
    is not reachable from it and must not be reported as this checkout's."""
    in_checkout(detached)
    tip = git("rev-parse", "origin/main", cwd=detached).stdout.strip()
    git("tag", "-a", "v9.9-tip", "-m", "v9.9-tip", tip, cwd=detached)

    result, _ = updater.get_remote_version()

    assert result != "v9.9-tip"


# ---------------------------------------------------------------------------
# Tags that MOVED upstream. `git fetch --tags` refuses to overwrite a tag it
# already has -- "would clobber existing tag" -- and fails the WHOLE fetch, so
# one re-cut release left every existing clone reporting "ERROR Fetching Tags."
# forever.


@pytest.fixture
def moved_tag(checkout):
    """A clone holding v1.0, whose origin has since re-cut v1.0 elsewhere."""
    origin = checkout.parent / "origin"
    git("tag", "-a", "v1.0", "-m", "v1.0", cwd=origin)
    git("fetch", "--tags", cwd=checkout)
    git("tag", "-d", "v1.0", cwd=origin)
    git("tag", "-a", "v1.0", "-m", "v1.0", "HEAD~1", cwd=origin)
    return checkout


def test_a_plain_tag_fetch_really_does_fail_on_a_moved_tag(moved_tag):
    """The premise, checked rather than assumed -- otherwise the fix below is
    guarding against nothing."""
    plain = git("fetch", "--tags", cwd=moved_tag)

    assert plain.returncode != 0
    assert "clobber" in (plain.stderr + plain.stdout).lower()


def test_the_remote_version_survives_a_moved_tag(moved_tag, in_checkout):
    in_checkout(moved_tag)

    result, error = updater.get_remote_version()

    assert "ERROR" not in result, result
    assert error == ""


def test_the_remote_version_survives_an_unreachable_remote(checkout, in_checkout):
    """A network blip must not blank the version line: the tags already on disk
    still answer "what version is this checkout"."""
    in_checkout(checkout)
    git("tag", "-a", "v1.2-local", "-m", "v1.2-local", cwd=checkout)
    git("remote", "set-url", "origin", "https://127.0.0.1:1/nope.git", cwd=checkout)

    result, error = updater.get_remote_version()

    assert result == "v1.2-local"
    assert error == ""
