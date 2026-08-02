"""Every top-level entry point calls datastore.init() before touching settings.

A settings migration only some callers reach is a real defect: updater.py and
wizard.py run as their own standalone processes (the upgrade script and the
installer launch them directly, never through app.py/control.py), so they
used to skip the migration app.py:39 and control.py:70 get for free.
read_settings_store() used to paper over that gap by migrating on the first
settings read of any process, guarded by a module-global flag in
common/datastore.py -- a write hidden inside a read, gated by process-global
mutable state in a library module.

The fix is to make the migration explicit everywhere instead: every entry
point calls datastore.init() itself (see common/datastore.py's init(), which
calls _upgrade_settings_in_store() directly), and this test is what keeps a
future entry point from silently skipping it -- a missing call fails CI here
rather than shipping a process that serves an unmigrated settings tree.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Entry points found by the scan below that legitimately must NOT call
#: datastore.init(), and why. Empty today -- every entry point the scan finds
#: touches the datastore and is expected to initialise it.
EXCLUDED_ENTRY_POINTS = {}


def _is_top_level_main_guard(node):
    """True for `if __name__ == "__main__":` (the repo's own convention for
    marking "this module is also run as a program")."""
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    )


def _entry_point_scripts():
    """Every repo-root .py file with a top-level __main__ guard -- the modules
    the repo runs as programs, not only ever imports."""
    scripts = []
    for path in sorted(REPO_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        if any(_is_top_level_main_guard(node) for node in tree.body):
            scripts.append(path)
    return scripts


def _calls_datastore_init(path):
    """True if `datastore.init()` is called anywhere in the module.

    Parsed with `ast`, not a substring search: a mention inside a comment or a
    docstring would make a substring search lie about coverage it does not
    have. Module level counts as well as inside the __main__ guard -- app.py
    is imported as a WSGI callable by gunicorn, so its call runs unconditionally
    at import time rather than behind the guard, and that is still coverage.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "init"
            and isinstance(func.value, ast.Name)
            and func.value.id == "datastore"
        ):
            return True
    return False


def test_every_entry_point_initialises_the_datastore():
    scripts = _entry_point_scripts()
    assert scripts, "the scan found no entry points -- it is not testing anything"

    missing = [
        path.name for path in scripts if path.name not in EXCLUDED_ENTRY_POINTS and not _calls_datastore_init(path)
    ]
    assert not missing, (
        f"{missing} run as standalone programs but never call datastore.init() -- "
        "add the call before the first settings/datastore access (see app.py, "
        "control.py, updater.py or wizard.py for the pattern), or add a justified "
        "entry to EXCLUDED_ENTRY_POINTS."
    )


def test_the_scan_finds_the_known_entry_points():
    """A sanity check on the scan itself: if it silently stopped finding the
    modules known to need this, the test above would stop testing anything."""
    names = {path.name for path in _entry_point_scripts()}
    assert {"app.py", "control.py", "updater.py", "wizard.py"} <= names
