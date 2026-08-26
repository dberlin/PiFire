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

"Reachable" is deliberately generous. The scan descends into every nested
statement block -- `if`/`elif`/`else`, `for`/`while`/`else`, `with`, and
`try`/`except`/`finally` -- at any depth, treating a call inside any branch
as reachable regardless of which branch a particular run would actually
take (it does not evaluate conditions). A `read_settings()` sitting inside
`if args.something:` is exactly as visible to this test as one at module
top level; real entry points put most of their work behind a flag or an
argparse branch, so a scan that only saw unconditional top-level statements
would miss most of the surface it claims to cover.

Two things it does NOT see, by design: (1) a call inside a `class` method,
or behind a `lambda` -- neither runs just because the class/def/lambda
statement was reached; (2) a call reached only through calling a
module-level *function*, which is inlined at the call site so it's seen in
its actual run order, but each module-level function is inlined at most
once (guards against infinite recursion on mutual/self recursion), and only
functions defined at module level are inlined this way -- a call routed
through more indirect plumbing (a callback stored in a variable, a method
call, `getattr`) is invisible to the scan just as it is to a human skimming
call sites without running the code.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Entry points found by the scan below that legitimately don't have to
#: initialise the datastore before their first settings access, and why.
EXCLUDED_ENTRY_POINTS = {
    # Runs as `sudo python board-config.py ...` from the wizard's command_list.
    # init() would run _first_boot_import() and the settings-tree migration;
    # those writes would land as root-owned datastore files without the
    # group-write bit supervisor's umask=002 programs depend on.
    # board-config.py only ever reads settings (read_settings() self-heals
    # to defaults via connection() -> _ensure_schema() with no init() needed),
    # and every command_list invocation runs after the parent wizard.py process
    # has already called datastore.init() itself, so the tree it reads is
    # already migrated.
    "board-config.py",
}

#: Settings read/write entry points; a call to any of these is what
#: datastore.init() must precede.
_SETTINGS_ACCESSORS = {"read_settings", "read_settings_store", "write_settings", "write_settings_store"}


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


#: For each compound-statement type, the nested statement lists it can run,
#: in an order consistent with execution -- e.g. a `try` body runs before its
#: handlers, which run before `finally`. Every branch is included, not just
#: the one that happens to run for a given input: a settings access gated by
#: `if`/`elif`/`else`, a loop, a `with`, or a `try`/`except`/`finally` is
#: reachable code, and the ordering invariant this test enforces has to hold
#: for every path a real run could take, not just the one path that was
#: exercised when the module was written.
def _nested_stmt_lists(stmt):
    if isinstance(stmt, ast.If):
        return [stmt.body, stmt.orelse]
    if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
        return [stmt.body, stmt.orelse]
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return [stmt.body]
    if isinstance(stmt, (ast.Try, ast.TryStar)):
        return [stmt.body, *(handler.body for handler in stmt.handlers), stmt.orelse, stmt.finalbody]
    return []


class _DirectCalls(ast.NodeVisitor):
    """Collects the Call nodes that belong to a single statement's own
    expressions -- an assignment's value, an `if`/`while`'s test, a `for`'s
    iterable, a `with`'s context expressions -- without descending into any
    nested statement block. Nested blocks (`if`/`for`/`while`/`try`/`with`
    bodies, handlers, `orelse`, `finally`) are walked separately by
    `_entry_sequence_calls`, one statement list at a time, so each call keeps
    its own position in source order relative to calls in sibling and
    enclosing statements instead of being flattened together with them.

    Does not descend into nested function/class definitions -- their bodies
    only run when called, not when the def statement itself is reached -- or
    into lambda bodies.
    """

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

    def visit_If(self, node):
        self.visit(node.test)

    def visit_For(self, node):
        self.visit(node.iter)

    def visit_AsyncFor(self, node):
        self.visit(node.iter)

    def visit_While(self, node):
        self.visit(node.test)

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)

    def visit_AsyncWith(self, node):
        for item in node.items:
            self.visit(item.context_expr)

    def visit_Try(self, node):
        pass

    def visit_TryStar(self, node):
        pass


def _calls_in_statement(stmt):
    """Call nodes that belong to `stmt` itself, per `_DirectCalls` -- not
    counting calls inside any nested statement block, which
    `_entry_sequence_calls` walks separately via `_nested_stmt_lists`.
    """
    collector = _DirectCalls()
    collector.visit(stmt)
    return collector.calls


def _entry_sequence_calls(tree):
    """Yield datastore.init() and settings-accessor Call nodes in the order
    they execute when the module is run as `python entrypoint.py`.

    Walks the module body top to bottom, descending into every nested
    statement block along the way -- `if`/`elif`/`else`, `for`/`while` (and
    their `else`), `with`, and `try`/`except`/`finally`. A call inside any of
    these counts as reachable at that block's position: if the branch can run
    at all, the call inside it can happen, so it is ordered relative to calls
    in sibling and enclosing statements exactly as if the block were absent.
    This does not evaluate conditions -- a call inside `if False:` is treated
    the same as one inside `if True:` -- so the walk is an over-approximation
    of what actually executes on any one run, which is the conservative
    direction for an ordering check like this one.

    The `if __name__ == "__main__":` guard is unpacked in place -- its
    condition is true in that scenario -- and a call to a function defined
    elsewhere at module level is inlined at the point it's called, so a call
    routed through a `main()`-style wrapper (display_launch.py) or a helper
    invoked from the guard (board-config.py) is still seen in the order it
    actually runs. Each module-level function is inlined at most once, which
    is enough to keep this from looping on recursion; a call reached only
    through a second level of function calls (a helper calling another
    helper) is inlined too, since the same `walk` recurses into whatever it
    inlines, but a call inside a *class* method, or behind a lambda, is not
    -- `_DirectCalls` treats those as never merely "reached".
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
            for nested in _nested_stmt_lists(stmt):
                yield from walk(nested)

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
    in a function nobody calls, or behind indirection the scan doesn't follow
    (a class method, a lambda, a stored callback) -- does not satisfy this.
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
