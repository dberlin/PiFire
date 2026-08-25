"""Grammar floor for the acados rebuild bootstrap.

`rebuild-acados.sh` runs `python3 -m tools.rebuild_acados` with the *system*
interpreter, and the installers and updater invoke it the same way, so every
module the bootstrap parses on the way in must be valid under a pre-3.14
grammar even though the package as a whole targets 3.14. PEP 758's
unparenthesized `except A, B` is the trap: it is canonical everywhere else in
this repository and a hard SyntaxError here.

`ast.parse(..., feature_version=...)` enforces the older grammar from inside
3.14, so this needs no second interpreter.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

# Oldest system `python3` the install scripts are expected to meet (Debian
# bookworm ships 3.11). Anything the bootstrap parses must satisfy this
# grammar, not the repository's 3.14 target.
_BOOTSTRAP_GRAMMAR = (3, 11)

_BOOTSTRAP_ENTRY_POINT = "tools.rebuild_acados"


def _module_source_path(module: str) -> Path | None:
    """Resolve a dotted module to a first-party source file, or None."""
    candidate = _REPOSITORY_ROOT / Path(*module.split("."))
    for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
        if path.is_file():
            return path
    return None


def _module_level_imports(tree: ast.Module, package: str) -> set[str]:
    """Dotted modules imported when `tree` is executed as a module body.

    Only statements at module level count: imports nested inside functions run
    on demand and are not part of what the interpreter must parse to load the
    module.
    """
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                root = ".".join(base[: len(base) - node.level + 1])
                prefix = f"{root}.{node.module}" if node.module else root
            else:
                prefix = node.module or ""
            imported.add(prefix)
            imported.update(f"{prefix}.{alias.name}" for alias in node.names)
    return imported


def _bootstrap_sources() -> dict[str, Path]:
    """Every first-party module parsed to import the bootstrap entry point."""
    pending = [_BOOTSTRAP_ENTRY_POINT]
    sources: dict[str, Path] = {}
    while pending:
        module = pending.pop()
        if module in sources:
            continue
        path = _module_source_path(module)
        if path is None:
            continue
        sources[module] = path
        # Importing a submodule executes every parent package's __init__.
        parts = module.split(".")
        pending.extend(".".join(parts[:depth]) for depth in range(1, len(parts)))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        package = module if path.name == "__init__.py" else ".".join(parts[:-1])
        pending.extend(_module_level_imports(tree, package))
    return sources


_BOOTSTRAP_SOURCES = _bootstrap_sources()


def test_entry_point_is_covered() -> None:
    """Guard the discovery itself: a rename must not silently empty the set."""
    assert _BOOTSTRAP_ENTRY_POINT in _BOOTSTRAP_SOURCES


@pytest.mark.parametrize("module", sorted(_BOOTSTRAP_SOURCES))
def test_bootstrap_module_parses_under_older_grammar(module: str) -> None:
    path = _BOOTSTRAP_SOURCES[module]
    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(path), feature_version=_BOOTSTRAP_GRAMMAR)
    except SyntaxError as error:
        version = ".".join(str(part) for part in _BOOTSTRAP_GRAMMAR)
        pytest.fail(
            f"{path.relative_to(_REPOSITORY_ROOT)} does not parse under the Python "
            f"{version} grammar used by the system interpreter that runs "
            f"rebuild-acados.sh: line {error.lineno}: {error.msg}"
        )


def test_grammar_floor_is_below_the_interpreter_running_the_tests() -> None:
    """The check is only meaningful while it constrains more than 3.14 does."""
    assert _BOOTSTRAP_GRAMMAR < sys.version_info[:2]
