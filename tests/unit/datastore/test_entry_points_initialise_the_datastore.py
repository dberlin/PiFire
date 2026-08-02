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

Presence alone isn't the invariant: a call that runs AFTER the first settings
read is exactly the bug above, just spelled differently. What's checked here
is order -- datastore.init() must execute before the first settings access
reachable from the module's own run-as-a-script path.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Entry points found by the scan below that legitimately don't have to
#: initialise the datastore before their first settings access, and why.
EXCLUDED_ENTRY_POINTS = {
    # Runs as `sudo python board-config.py ...` from the wizard's command_list
    # and from upgrade.sh -- always as root. init() would run _first_boot_import()
    # and the settings-tree migration, both writes; under sudo's default umask
    # those land as root-owned datastore files without the group-write bit that
    # supervisor's umask=002 programs depend on for control/display to share
    # them. board-config.py only ever reads settings (read_settings() self-heals
    # to defaults via connection() -> _ensure_schema() with no init() needed),
    # and every command_list invocation runs after the parent wizard.py process
    # has already called datastore.init() itself, so the tree it reads is
    # already migrated.
    "board-config.py",
}

#: Settings read/write entry points; a call to any of these is what
#: datastore.init() must precede.
_SETTINGS_ACCESSORS = {"read_settings", "read_settings_store", "write_settings", "write_settings_store"}

#: Statement kinds that carry no branching of their own -- the shapes
#: `_calls_in_statement` is willing to look inside. A compound statement
#: (`if`/`for`/`while`/`try`/`with`) is deliberately excluded: a call nested
#: in one of those does not run merely because the enclosing statement was
#: reached, so it must stay invisible to the ordering walk below.
_UNCONDITIONAL_STMT_TYPES = (
    ast.Expr,
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    ast.Assert,
    ast.Delete,
    ast.Raise,
    ast.Return,
)


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


def _is_datastore_init_call(call):
    """True for a `datastore.init()` call node."""
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "init"
        and isinstance(func.value, ast.Name)
        and func.value.id == "datastore"
    )


def _is_settings_accessor_call(call):
    """True for a call to one of `_SETTINGS_ACCESSORS`."""
    func = call.func
    return isinstance(func, ast.Name) and func.id in _SETTINGS_ACCESSORS


class _UnconditionalCalls(ast.NodeVisitor):
    """Collects the Call nodes reachable from a single statement without
    conditional or deferred execution. Does not descend into nested
    function/class definitions -- their bodies only run when called, not when
    the def statement itself is reached -- or into lambda bodies."""

    def __init__(self):
        self.calls = []

    def visit_FunctionDef(self, node):
        pass

    def visit_AsyncFunctionDef(self, node):
        pass

    def visit_ClassDef(self, node):
        pass

    def visit_Lambda(self, node):
        pass

    def visit_Call(self, node):
        self.calls.append(node)
        self.generic_visit(node)


def _calls_in_statement(stmt):
    """Call nodes that run unconditionally when `stmt` executes.

    Restricted to `_UNCONDITIONAL_STMT_TYPES`; a compound statement is opaque
    here even though a plain `ast.walk` would happily find calls inside its
    body -- a `datastore.init()` sitting inside `if False:` must not count as
    reached.
    """
    if not isinstance(stmt, _UNCONDITIONAL_STMT_TYPES):
        return []
    collector = _UnconditionalCalls()
    collector.visit(stmt)
    return collector.calls


def _entry_sequence_calls(tree):
    """Yield datastore.init() and settings-accessor Call nodes in the order
    they execute when the module is run as `python entrypoint.py`.

    Walks the module body top to bottom. The `if __name__ == "__main__":`
    guard is unpacked in place -- its condition is true in that scenario --
    and a call to a function defined elsewhere at module level is inlined at
    the point it's called, so a call routed through a `main()`-style wrapper
    (display_launch.py) or a helper invoked from the guard (board-config.py)
    is still seen in the order it actually runs. Each module-level function
    is inlined at most once, which is enough to keep this from looping on
    recursion.
    """
    module_functions = {
        node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    inlined = set()

    def walk(stmts):
        for stmt in stmts:
            if _is_top_level_main_guard(stmt):
                yield from walk(stmt.body)
                continue
            for call in _calls_in_statement(stmt):
                if _is_datastore_init_call(call) or _is_settings_accessor_call(call):
                    yield call
                    continue
                name = call.func.id if isinstance(call.func, ast.Name) else None
                if name in module_functions and name not in inlined:
                    inlined.add(name)
                    yield from walk(module_functions[name].body)

    yield from walk(tree.body)


def _init_precedes_first_settings_access(path):
    """True if datastore.init() executes before the first settings
    read/write reachable from this module's run-as-a-script execution order.

    Vacuously true when no settings access is reachable at all -- there is
    nothing for the ordering to violate.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    init_seen = False
    for call in _entry_sequence_calls(tree):
        if _is_datastore_init_call(call):
            init_seen = True
        elif _is_settings_accessor_call(call):
            return init_seen
    return True


def test_every_entry_point_initialises_the_datastore_before_reading_settings():
    """datastore.init() must precede the first settings access reachable from
    each entry point's run-as-a-script path, per `_init_precedes_first_settings_access`.
    A call that exists somewhere in the file -- after the first settings read,
    inside a branch that never runs, or in a function nobody calls -- does not
    satisfy this.
    """
    scripts = _entry_point_scripts()
    assert scripts, "the scan found no entry points -- it is not testing anything"

    violations = [
        path.name
        for path in scripts
        if path.name not in EXCLUDED_ENTRY_POINTS and not _init_precedes_first_settings_access(path)
    ]
    assert not violations, (
        f"{violations} read or write settings before calling datastore.init() (or never reach "
        "the call at all) -- call datastore.init() before the first settings access reachable "
        "from the module's __main__ guard (see app.py, control.py, updater.py or wizard.py for "
        "the pattern), or add a justified entry to EXCLUDED_ENTRY_POINTS."
    )


def test_the_scan_finds_the_known_entry_points():
    """A sanity check on the scan itself: if it silently stopped finding the
    modules known to need this, the test above would stop testing anything."""
    names = {path.name for path in _entry_point_scripts()}
    assert {"app.py", "control.py", "updater.py", "wizard.py"} <= names
