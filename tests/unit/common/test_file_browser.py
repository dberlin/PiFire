"""Managed-folder browsing: containment, extension listing, pagination.

The cookfile blueprint resolves client-supplied filenames by string
concatenation (`HISTORY_FOLDER + filename`, blueprints/cookfile/routes.py:205)
or not at all (`send_file(request.form["dl_cookfile"])`, :162). The history
blueprint got this right (`_safe_history_path`, blueprints/history/routes.py:18)
and the recipes delete route got it right a different way (`secure_filename`,
blueprints/recipes/routes.py:388). This module is the single implementation the
new /api/files surface uses, so there is one place to be right.

secure_filename is deliberately NOT used: cook titles are user-chosen and
legitimately contain spaces and parentheses that secure_filename mangles,
silently breaking opens and downloads for valid files. A realpath-containment
check validates the resulting PATH instead of the name's character set --
the same reasoning as _safe_history_path's docstring.

SAFETY: file_mgmt/recipes.py and file_mgmt/cookfile.py used to end their
archive-creation paths with `os.system(f"rm -rf {path}")` on a path derived
from a user-chosen title -- a live command-injection and data-loss primitive.
Those are gone; test_no_shell_outs_left_in_file_mgmt below keeps them gone.
"""

import os
import pathlib

import pytest

from common.file_browser import browse_files, list_managed_files, resolve_managed_file


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "history"
    d.mkdir()
    return str(d) + "/"


def test_no_shell_outs_left_in_file_mgmt():
    """Source-level guard, not a behaviour test.

    Both archive writers cleaned up their scratch folder with a shell
    `rm -rf` on an interpolated, user-influenced path. A behavioural test
    cannot prove the call is absent everywhere; reading the source can.
    """
    root = pathlib.Path(__file__).resolve().parents[3] / "file_mgmt"
    offenders = [
        path.name
        for path in sorted(root.glob("*.py"))
        if "os.system" in path.read_text() or "subprocess" in path.read_text()
    ]
    assert offenders == []


def test_resolves_a_plain_name(folder):
    open(os.path.join(folder, "A-CookFile.pifire"), "w").close()
    resolved = resolve_managed_file(folder, "A-CookFile.pifire")
    assert resolved == os.path.realpath(os.path.join(folder, "A-CookFile.pifire"))


def test_allows_spaces_and_parentheses_that_secure_filename_would_mangle(folder):
    name = "Brisket (Sunday) #2.pifire"
    open(os.path.join(folder, name), "w").close()
    assert resolve_managed_file(folder, name) is not None


def test_rejects_parent_traversal(folder):
    assert resolve_managed_file(folder, "../../etc/passwd") is None
    assert resolve_managed_file(folder, "../secret.pifire") is None


def test_rejects_a_nested_traversal_that_climbs_back_out(folder):
    assert resolve_managed_file(folder, "sub/../../escaped.pifire") is None


def test_rejects_absolute_paths(folder):
    assert resolve_managed_file(folder, "/etc/passwd") is None


def test_rejects_empty_and_the_folder_itself(folder):
    assert resolve_managed_file(folder, "") is None
    assert resolve_managed_file(folder, ".") is None


def test_rejects_a_symlink_pointing_outside(folder, tmp_path):
    outside = tmp_path / "outside.pifire"
    outside.write_text("x")
    os.symlink(str(outside), os.path.join(folder, "link.pifire"))
    assert resolve_managed_file(folder, "link.pifire") is None


def test_resolves_names_that_do_not_exist_yet(folder):
    """Containment is a PATH check, not an existence check -- upload needs a
    contained destination for a file that is not there yet. Existence is the
    caller's separate assertion."""
    assert resolve_managed_file(folder, "New.pifire") is not None


def test_list_filters_by_extension_and_creates_a_missing_folder(tmp_path):
    missing = str(tmp_path / "nope") + "/"
    assert list_managed_files(missing, ".pifire") == []
    assert os.path.isdir(missing)


def test_list_filters_by_extension(folder):
    for name in ("a.pifire", "b.pifire", "c.pfrecipe", "notes.txt"):
        open(os.path.join(folder, name), "w").close()
    assert sorted(list_managed_files(folder, ".pifire")) == ["a.pifire", "b.pifire"]
    assert list_managed_files(folder, ".pfrecipe") == ["c.pfrecipe"]


def test_browse_paginates_sorts_and_reports_totals(folder):
    for i in range(25):
        open(os.path.join(folder, f"cook-{i:02d}.pifire"), "w").close()

    page1 = browse_files(folder, ".pifire", page=1, per_page=10, reverse=False)
    assert [i["filename"] for i in page1["items"]][:2] == ["cook-00.pifire", "cook-01.pifire"]
    assert page1["total"] == 25
    assert page1["last_page"] == 3
    assert page1["page"] == 1
    assert page1["per_page"] == 10
    assert page1["reverse"] is False

    rev = browse_files(folder, ".pifire", page=1, per_page=10, reverse=True)
    assert rev["items"][0]["filename"] == "cook-24.pifire"
    assert rev["reverse"] is True


def test_browse_opens_only_the_requested_pages_archives(folder):
    """A hundred-cook folder must not unzip a hundred archives per request:
    paginate_list slices first, _details reads second."""
    for i in range(25):
        open(os.path.join(folder, f"cook-{i:02d}.pifire"), "w").close()
    page2 = browse_files(folder, ".pifire", page=2, per_page=10, reverse=False)
    assert len(page2["items"]) == 10
    assert page2["items"][0]["filename"] == "cook-10.pifire"
    assert page2["page"] == 2


def test_browse_clamps_a_page_past_the_end(folder):
    for i in range(3):
        open(os.path.join(folder, f"c{i}.pifire"), "w").close()
    out = browse_files(folder, ".pifire", page=99, per_page=10)
    assert out["page"] == 1
    assert out["last_page"] == 1
    assert len(out["items"]) == 3


def test_browse_reports_ERROR_title_for_an_unreadable_archive(folder):
    with open(os.path.join(folder, "broken.pifire"), "w") as f:
        f.write("not a zip")
    out = browse_files(folder, ".pifire")
    assert out["items"] == [{"filename": "broken.pifire", "title": "ERROR", "thumbnail": ""}]


def test_browse_of_an_empty_folder(folder):
    out = browse_files(folder, ".pifire")
    assert out == {
        "items": [],
        "page": 1,
        "last_page": 1,
        "per_page": 10,
        "reverse": True,
        "total": 0,
    }
