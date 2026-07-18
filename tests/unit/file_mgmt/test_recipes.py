"""Unit coverage for file_mgmt/recipes.py's `create_recipefile`.

Latent bug (task 6 of the bugfix plan): unlike its cookfile counterpart
(file_mgmt/cookfile.py's `create_cookfile`, which loops appending
`-1`/`-2`/... on a title collision), `create_recipefile()` derived its title
from `datetime.now().strftime("%Y-%m-%d--%H%M")` (minute resolution) with NO
collision check -- `zipfile.ZipFile(filename, "w", ...)` would silently
truncate and overwrite any `.pfrecipe` that already had that exact title,
losing its content, whenever two new recipes were created within the same
clock-minute. The fix mirrors `create_cookfile`'s disambiguation loop: only
the final `.pfrecipe` archive name gets a `-N` suffix on collision (the
transient per-call working folder does not need one, since it is always
removed before the function returns).
"""

import datetime
import os

import pytest

import file_mgmt.recipes as recipes_mod
from file_mgmt.recipes import create_recipefile


class _FrozenDateTime(datetime.datetime):
    """A datetime.datetime subclass whose `.now()` always returns the same
    fixed instant, so two back-to-back `create_recipefile()` calls compute
    the exact same title -- reproducing a same-clock-minute collision on
    demand instead of waiting for a real one."""

    _frozen = datetime.datetime(2026, 7, 18, 12, 34)

    @classmethod
    def now(cls, tz=None):
        return cls._frozen


@pytest.fixture
def isolated_recipe_folder(tmp_path, monkeypatch):
    recipe_dir = str(tmp_path / "recipes") + "/"
    monkeypatch.setattr(recipes_mod, "RECIPE_FOLDER", recipe_dir)
    return recipe_dir


def test_create_recipefile_same_title_collision_does_not_overwrite(ds, isolated_recipe_folder, monkeypatch):
    monkeypatch.setattr(recipes_mod.datetime, "datetime", _FrozenDateTime)

    first_filename = create_recipefile()
    assert os.path.exists(first_filename)

    # Tamper with the first archive's mtime/marker so we can tell, after the
    # second call, whether it got silently truncated-and-overwritten (the
    # bug) or left alone (the fix).
    first_contents_before = open(first_filename, "rb").read()

    second_filename = create_recipefile()
    assert os.path.exists(second_filename)

    # The core assertion: both archives must be present as distinct files,
    # and the first one's bytes must be untouched by the second call.
    assert first_filename != second_filename
    assert second_filename == first_filename.replace(".pfrecipe", "-1.pfrecipe")
    assert open(first_filename, "rb").read() == first_contents_before
