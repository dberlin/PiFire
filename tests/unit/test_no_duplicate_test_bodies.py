"""Guard against the copy-pasted-test problem coming back.

A test whose body is byte-identical to another test's -- decorators included --
is either a redundant copy or a mislabelled one. Both are worth catching at
merge time rather than in a once-a-year audit.

ALLOWLIST is for genuinely-justified twins. Add to it only with a reason.
"""

from pathlib import Path

from tests.tools.duplicate_tests import find_duplicate_test_bodies

ROOT = Path(__file__).resolve().parents[1]

# (file, test name) pairs that are duplicates on purpose.
# test_upload_with_an_empty_filename_is_400 in these two modules is
# byte-identical, but each module's `_upload` helper posts to a different
# endpoint and field: /api/files/recipes/upload field "recipe" vs
# /api/files/cookfiles/upload field "file". Verified twice -- genuine twins,
# not a leftover copy-paste.
ALLOWLIST: set[tuple[str, str]] = {
    ("tests/web/test_api_files_recipes_write.py", "test_upload_with_an_empty_filename_is_400"),
    ("tests/web/test_api_files_cookfile_write.py", "test_upload_with_an_empty_filename_is_400"),
}


def test_no_duplicate_test_bodies():
    groups = find_duplicate_test_bodies(ROOT)
    offenders = [group for group in groups if not all((path, name) in ALLOWLIST for path, _line, name in group.members)]

    assert offenders == [], "\n".join(
        f"{group.line_count} identical lines:\n"
        + "\n".join(f"    {path}:{line}  {name}" for path, line, name in group.members)
        for group in offenders
    )
